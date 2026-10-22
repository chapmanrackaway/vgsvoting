import csv
import io

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    BILL_STATUSES, CHAMBERS, PARTIES, POSITIONS, THRESHOLDS, Amendment, Bill, Committee, Member,
    MemberVote, Vote, utcnow,
)
from .bills import log_action
from .helpers import arg_int, clerk_required, form_str, get_or_404

bp = Blueprint("votes", __name__)


def _parse_body(value, bill):
    """Body strings look like 'floor:House' or 'committee:12'."""
    kind, _, ident = (value or "").partition(":")
    if kind == "committee" and ident.isdigit():
        committee = db.session.get(Committee, int(ident))
        if committee:
            return committee.chamber, committee
    if kind == "floor" and ident in CHAMBERS:
        return ident, None
    if bill.committee:
        return bill.committee.chamber, bill.committee
    return bill.chamber, None


def _eligible_members(chamber, committee):
    if committee:
        return committee.active_members()
    return db.session.scalars(
        select(Member).where(Member.chamber == chamber, Member.active.is_(True))
        .order_by(Member.last_name, Member.first_name)
    ).all()


def _suggested_statuses(bill, chamber, committee):
    if committee:
        return "Reported from Committee", "Failed in Committee"
    if chamber != bill.chamber:
        return "Passed General Assembly", "Failed on Floor"
    return f"Passed {chamber}", "Failed on Floor"


def _apply_positions(vote, members):
    existing = {mv.member_id: mv for mv in vote.member_votes}
    for member in members:
        pos = request.form.get(f"m{member.id}", "X")
        pos = pos if pos in POSITIONS else "X"
        if member.id in existing:
            existing.pop(member.id).position = pos
        else:
            vote.member_votes.append(MemberVote(member=member, position=pos))
    # Members who are no longer eligible but were recorded earlier keep their entry
    # only if the form still submitted a position for them.
    for member_id, mv in existing.items():
        pos = request.form.get(f"m{member_id}")
        if pos in POSITIONS:
            mv.position = pos


@bp.route("/votes")
def vote_list():
    q = select(Vote).options(selectinload(Vote.bill), selectinload(Vote.member_votes),
                             selectinload(Vote.committee), selectinload(Vote.amendment))
    chamber = request.args.get("chamber")
    if chamber in CHAMBERS:
        q = q.where(Vote.chamber == chamber)
    bill_id = arg_int("bill")
    if bill_id:
        q = q.where(Vote.bill_id == bill_id)
    votes = db.session.scalars(q.order_by(Vote.taken_at.desc())).all()
    if request.args.get("format") == "csv":
        return _votes_csv(votes)
    return render_template("votes/list.html", votes=votes, chamber=chamber)


def _votes_csv(votes):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["vote_id", "taken_at_utc", "bill", "amendment", "body", "motion", "yeas", "nays",
                     "abstain", "not_voting", "result", "member", "party", "district", "position"])
    for v in votes:
        t = v.tally()
        base = [v.id, v.taken_at.isoformat(timespec="minutes"), v.bill.label,
                v.amendment.number if v.amendment else "", v.body, v.motion,
                t["Y"], t["N"], t["A"], t["X"], v.result]
        if not v.member_votes:
            writer.writerow(base + ["", "", "", "Voice vote" if v.voice else ""])
        for mv in sorted(v.member_votes, key=lambda mv: mv.member.last_name.lower()):
            writer.writerow(base + [mv.member.full_name, mv.member.party or "", mv.member.district or "",
                                    POSITIONS[mv.position]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=votes.csv"})


@bp.route("/bills/<int:bill_id>/votes/new", methods=["GET", "POST"])
@clerk_required
def new_vote(bill_id):
    bill = get_or_404(Bill, bill_id)
    body_value = request.values.get("body")
    chamber, committee = _parse_body(body_value, bill)
    amendment_id = arg_int("amendment") or (int(request.form["amendment_id"])
                                            if request.form.get("amendment_id", "").isdigit() else None)
    amendment = db.session.get(Amendment, amendment_id) if amendment_id else None
    if amendment and amendment.bill_id != bill.id:
        abort(400)
    members = _eligible_members(chamber, committee)

    if request.method == "POST":
        motion = form_str("motion")
        if not motion:
            flash("Describe the motion being voted on.", "error")
        else:
            threshold = form_str("threshold")
            vote = Vote(
                bill=bill, amendment=amendment, chamber=chamber, committee=committee, motion=motion,
                threshold=threshold if threshold in THRESHOLDS else "majority",
                voice=request.form.get("voice") == "1",
                notes=form_str("notes") or None, taken_at=utcnow(),
                user_id=current_user.id,
            )
            if vote.voice:
                vote.voice_result = "Passed" if form_str("voice_result") == "Passed" else "Failed"
            else:
                tb = form_str("tie_breaker")
                vote.tie_breaker = tb if tb in ("Y", "N") and chamber == "Senate" and not committee else None
                _apply_positions(vote, members)
            db.session.add(vote)
            db.session.flush()

            subject = f"Amendment #{amendment.number}" if amendment else ""
            desc = f"{vote.body}: {motion}"
            if subject and subject.lower() not in motion.lower():
                desc += f" ({subject})"
            desc += f" — {vote.result.lower()}"
            if not vote.voice:
                desc += f" ({vote.tally_text})"
            log_action(bill, desc, chamber=chamber, vote=vote)

            if amendment and request.form.get("update_amendment") == "1":
                amendment.status = "Adopted" if vote.passed else "Rejected"
            new_status = form_str("status_passed" if vote.passed else "status_failed")
            if new_status in BILL_STATUSES and new_status != bill.status:
                bill.status = new_status
                log_action(bill, f"Status: {new_status}", chamber=chamber)
            db.session.commit()
            flash(f"Vote recorded: {vote.result} ({vote.tally_text}).", "success")
            return redirect(url_for("votes.vote_detail", vote_id=vote.id))

    pass_status, fail_status = _suggested_statuses(bill, chamber, committee)
    if amendment:
        pass_status = fail_status = ""
    bodies = [("floor:House", "House Floor"), ("floor:Senate", "Senate Floor")] + [
        (f"committee:{c.id}", c.display_name)
        for c in db.session.scalars(select(Committee).order_by(Committee.chamber, Committee.name))
    ]
    body_value = f"committee:{committee.id}" if committee else f"floor:{chamber}"
    return render_template(
        "votes/form.html", bill=bill, vote=None, amendment=amendment, members=members,
        chamber=chamber, committee=committee, bodies=bodies, body_value=body_value,
        pass_status=pass_status, fail_status=fail_status, positions={},
    )


@bp.route("/votes/<int:vote_id>")
def vote_detail(vote_id):
    vote = get_or_404(Vote, vote_id)
    by_position = {k: [] for k in POSITIONS}
    by_party = {}
    for mv in sorted(vote.member_votes, key=lambda mv: (mv.member.last_name.lower(), mv.member.first_name.lower())):
        by_position[mv.position].append(mv.member)
        party = PARTIES.get(mv.member.party, "No party")
        by_party.setdefault(party, {k: 0 for k in POSITIONS})[mv.position] += 1
    return render_template("votes/detail.html", vote=vote, by_position=by_position, by_party=by_party)


@bp.route("/votes/<int:vote_id>/edit", methods=["GET", "POST"])
@clerk_required
def edit_vote(vote_id):
    vote = get_or_404(Vote, vote_id)
    members = _eligible_members(vote.chamber, vote.committee)
    recorded = {mv.member_id: mv for mv in vote.member_votes}
    # Keep anyone already recorded on the vote even if since removed from the committee.
    extra = [mv.member for mv in vote.member_votes if mv.member_id not in {m.id for m in members}]
    members = sorted(list(members) + extra, key=lambda m: (m.last_name.lower(), m.first_name.lower()))
    if request.method == "POST":
        motion = form_str("motion")
        if motion:
            vote.motion = motion
        threshold = form_str("threshold")
        vote.threshold = threshold if threshold in THRESHOLDS else vote.threshold
        vote.notes = form_str("notes") or None
        if vote.voice:
            vote.voice_result = "Passed" if form_str("voice_result") == "Passed" else "Failed"
        else:
            tb = form_str("tie_breaker")
            vote.tie_breaker = tb if tb in ("Y", "N") and vote.chamber == "Senate" and not vote.committee else None
            _apply_positions(vote, members)
        db.session.flush()
        log_action(vote.bill, f"{vote.body}: vote on '{vote.motion}' corrected — {vote.result.lower()}"
                   + ("" if vote.voice else f" ({vote.tally_text})"), chamber=vote.chamber, vote=vote)
        db.session.commit()
        flash("Vote updated. Check the bill's status in case the outcome changed.", "success")
        return redirect(url_for("votes.vote_detail", vote_id=vote.id))
    return render_template(
        "votes/form.html", bill=vote.bill, vote=vote, amendment=vote.amendment, members=members,
        chamber=vote.chamber, committee=vote.committee, bodies=None, body_value=None,
        pass_status="", fail_status="", positions={k: v.position for k, v in recorded.items()},
    )


@bp.route("/votes/<int:vote_id>/delete", methods=["POST"])
@clerk_required
def delete_vote(vote_id):
    vote = get_or_404(Vote, vote_id)
    bill = vote.bill
    log_action(bill, f"{vote.body}: vote on '{vote.motion}' deleted", chamber=vote.chamber)
    db.session.delete(vote)
    db.session.commit()
    flash("Vote deleted. Bill status was not changed.", "success")
    return redirect(url_for("bills.bill_detail", bill_id=bill.id))
