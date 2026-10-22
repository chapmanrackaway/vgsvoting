import csv
import io

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    CHAMBERS, PARTIES, Amendment, Bill, Committee, CommitteeAssignment, Member, MemberVote,
    Vote, bill_copatrons,
)
from .helpers import clerk_required, form_int, form_str, get_or_404

bp = Blueprint("roster", __name__)

CSV_FIELDS = ["first_name", "last_name", "chamber", "district", "party", "school",
              "position", "email", "active"]
COMMITTEE_ROLES = ["Chair", "Vice Chair", "Member"]


def _member_from_form(member):
    member.first_name = form_str("first_name")
    member.last_name = form_str("last_name")
    member.chamber = form_str("chamber")
    member.district = form_str("district") or None
    party = form_str("party")
    member.party = party if party in PARTIES else None
    member.school = form_str("school") or None
    member.position = form_str("position") or None
    member.email = form_str("email") or None
    member.active = request.form.get("active") == "on"
    member.notes = form_str("notes") or None
    errors = []
    if not member.first_name or not member.last_name:
        errors.append("First and last name are required.")
    if member.chamber not in CHAMBERS:
        errors.append("Choose a chamber.")
    return errors


@bp.route("/roster")
def members():
    q = select(Member).options(
        selectinload(Member.assignments).selectinload(CommitteeAssignment.committee))
    chamber = request.args.get("chamber")
    party = request.args.get("party")
    search = (request.args.get("q") or "").strip()
    show_inactive = request.args.get("inactive") == "1"
    if chamber in CHAMBERS:
        q = q.where(Member.chamber == chamber)
    if party in PARTIES:
        q = q.where(Member.party == party)
    if not show_inactive:
        q = q.where(Member.active.is_(True))
    if search:
        like = f"%{search.lower()}%"
        q = q.where(or_(
            func.lower(Member.first_name).like(like), func.lower(Member.last_name).like(like),
            func.lower(Member.school).like(like), func.lower(Member.district).like(like),
        ))
    q = q.order_by(Member.chamber, Member.last_name, Member.first_name)
    rows = db.session.scalars(q).all()
    return render_template("roster/members.html", members=rows, chamber=chamber, party=party,
                           search=search, show_inactive=show_inactive)


@bp.route("/roster/new", methods=["GET", "POST"])
@clerk_required
def new_member():
    member = Member(active=True, chamber=request.args.get("chamber") or "House")
    if request.method == "POST":
        errors = _member_from_form(member)
        if not errors:
            db.session.add(member)
            db.session.commit()
            flash(f"Added {member.display_name}.", "success")
            if request.form.get("another"):
                return redirect(url_for("roster.new_member", chamber=member.chamber))
            return redirect(url_for("roster.members"))
        for e in errors:
            flash(e, "error")
    return render_template("roster/member_form.html", member=member)


@bp.route("/roster/<int:member_id>")
def member_detail(member_id):
    member = get_or_404(Member, member_id)
    sponsored = db.session.scalars(
        select(Bill).where(Bill.patron_id == member.id).order_by(Bill.chamber, Bill.number)
    ).all()
    copatroned = db.session.scalars(
        select(Bill).join(bill_copatrons).where(bill_copatrons.c.member_id == member.id)
        .order_by(Bill.chamber, Bill.number)
    ).all()
    record = db.session.scalars(
        select(MemberVote).where(MemberVote.member_id == member.id)
        .join(Vote).order_by(Vote.taken_at.desc())
        .options(selectinload(MemberVote.vote).selectinload(Vote.member_votes))
    ).all()
    amendments = db.session.scalars(
        select(Amendment).where(Amendment.offered_by_id == member.id).order_by(Amendment.created_at)
    ).all()
    return render_template("roster/member_detail.html", member=member, sponsored=sponsored,
                           copatroned=copatroned, record=record, amendments=amendments)


@bp.route("/roster/<int:member_id>/edit", methods=["GET", "POST"])
@clerk_required
def edit_member(member_id):
    member = get_or_404(Member, member_id)
    if request.method == "POST":
        errors = _member_from_form(member)
        if not errors:
            db.session.commit()
            flash(f"Saved {member.display_name}.", "success")
            return redirect(url_for("roster.member_detail", member_id=member.id))
        db.session.rollback()
        for e in errors:
            flash(e, "error")
    return render_template("roster/member_form.html", member=member)


@bp.route("/roster/<int:member_id>/delete", methods=["POST"])
@clerk_required
def delete_member(member_id):
    member = get_or_404(Member, member_id)
    in_use = (
        db.session.scalar(select(func.count()).select_from(MemberVote).where(MemberVote.member_id == member.id))
        or db.session.scalar(select(func.count()).select_from(Bill).where(Bill.patron_id == member.id))
        or db.session.scalar(select(func.count()).select_from(Amendment).where(Amendment.offered_by_id == member.id))
        or db.session.scalar(select(func.count()).select_from(bill_copatrons).where(bill_copatrons.c.member_id == member.id))
    )
    if in_use:
        member.active = False
        db.session.commit()
        flash(f"{member.display_name} has bills or votes on record, so they were marked inactive "
              "instead of deleted.", "info")
        return redirect(url_for("roster.member_detail", member_id=member.id))
    db.session.delete(member)
    db.session.commit()
    flash(f"Deleted {member.display_name}.", "success")
    return redirect(url_for("roster.members"))


@bp.route("/roster/export.csv")
def export_members():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_FIELDS)
    for m in db.session.scalars(select(Member).order_by(Member.chamber, Member.last_name)):
        writer.writerow([m.first_name, m.last_name, m.chamber, m.district or "", m.party or "",
                         m.school or "", m.position or "", m.email or "", "yes" if m.active else "no"])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=roster.csv"})


@bp.route("/roster/import", methods=["GET", "POST"])
@clerk_required
def import_members():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file to upload.", "error")
            return redirect(url_for("roster.import_members"))
        try:
            content = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash("The file must be a UTF-8 CSV (in Excel, use 'CSV UTF-8').", "error")
            return redirect(url_for("roster.import_members"))
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            flash("The file is empty.", "error")
            return redirect(url_for("roster.import_members"))
        headers = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames if h}
        added = updated = 0
        problems = []
        for lineno, raw in enumerate(reader, start=2):
            row = {k: (raw.get(orig) or "").strip() for k, orig in headers.items()}
            first, last = row.get("first_name", ""), row.get("last_name", "")
            chamber = row.get("chamber", "").title()
            if chamber in ("Delegate", "Delegates", "House Of Delegates"):
                chamber = "House"
            if chamber in ("Senator", "Senators"):
                chamber = "Senate"
            if not first or not last or chamber not in CHAMBERS:
                problems.append(f"Line {lineno}: needs first_name, last_name and chamber (House/Senate).")
                continue
            existing = db.session.scalar(select(Member).where(
                func.lower(Member.first_name) == first.lower(),
                func.lower(Member.last_name) == last.lower(),
                Member.chamber == chamber,
            ))
            member = existing or Member(first_name=first, last_name=last, chamber=chamber)
            party = row.get("party", "")[:1].upper()
            member.party = party if party in PARTIES else member.party
            for field in ("district", "school", "position", "email"):
                if row.get(field):
                    setattr(member, field, row[field])
            member.active = row.get("active", "yes").lower() not in ("no", "n", "false", "0", "inactive")
            if existing:
                updated += 1
            else:
                db.session.add(member)
                added += 1
        db.session.commit()
        flash(f"Import complete: {added} added, {updated} updated.", "success")
        for p in problems[:20]:
            flash(p, "error")
        if len(problems) > 20:
            flash(f"...and {len(problems) - 20} more rows skipped.", "error")
        return redirect(url_for("roster.members"))
    return render_template("roster/import.html", fields=CSV_FIELDS)


# ---------------------------------------------------------------- committees

@bp.route("/committees", methods=["GET", "POST"])
def committees():
    if request.method == "POST":
        return _create_committee()
    rows = db.session.scalars(
        select(Committee).order_by(Committee.chamber, Committee.name)
        .options(selectinload(Committee.assignments).selectinload(CommitteeAssignment.member))
    ).all()
    return render_template("committees/list.html", committees=rows)


@clerk_required
def _create_committee():
    name, chamber = form_str("name"), form_str("chamber")
    if not name or chamber not in CHAMBERS:
        flash("Committee name and chamber are required.", "error")
        return redirect(url_for("roster.committees"))
    committee = Committee(name=name, chamber=chamber)
    db.session.add(committee)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That committee already exists.", "error")
        return redirect(url_for("roster.committees"))
    flash(f"Created {committee.display_name}.", "success")
    return redirect(url_for("roster.committee_detail", committee_id=committee.id))


@bp.route("/committees/<int:committee_id>", methods=["GET", "POST"])
def committee_detail(committee_id):
    committee = get_or_404(Committee, committee_id)
    if request.method == "POST":
        return _update_committee(committee)
    assigned_ids = {a.member_id for a in committee.assignments}
    candidates = [m for m in db.session.scalars(
        select(Member).where(Member.chamber == committee.chamber, Member.active.is_(True))
        .order_by(Member.last_name, Member.first_name)
    ) if m.id not in assigned_ids]
    order = {r: i for i, r in enumerate(COMMITTEE_ROLES)}
    assignments = sorted(committee.assignments,
                         key=lambda a: (order.get(a.role, 9), a.member.last_name.lower()))
    bills = db.session.scalars(
        select(Bill).where(Bill.committee_id == committee.id).order_by(Bill.chamber, Bill.number)
    ).all()
    return render_template("committees/detail.html", committee=committee, assignments=assignments,
                           candidates=candidates, roles=COMMITTEE_ROLES, bills=bills)


@clerk_required
def _update_committee(committee):
    action = request.form.get("action")
    if action == "rename":
        name = form_str("name")
        if name:
            committee.name = name
    elif action == "add":
        role = form_str("role", "Member")
        role = role if role in COMMITTEE_ROLES else "Member"
        ids = [int(i) for i in request.form.getlist("member_ids") if i.isdigit()]
        existing = {a.member_id for a in committee.assignments}
        for mid in ids:
            member = db.session.get(Member, mid)
            if member and mid not in existing:
                committee.assignments.append(CommitteeAssignment(member=member, role=role))
    elif action == "role":
        assignment = db.session.get(CommitteeAssignment, form_int("assignment_id"))
        role = form_str("role")
        if assignment and assignment.committee_id == committee.id and role in COMMITTEE_ROLES:
            assignment.role = role
    elif action == "remove":
        assignment = db.session.get(CommitteeAssignment, form_int("assignment_id"))
        if assignment and assignment.committee_id == committee.id:
            db.session.delete(assignment)
    elif action == "delete":
        in_use = db.session.scalar(
            select(func.count()).select_from(Vote).where(Vote.committee_id == committee.id)
        )
        if in_use:
            flash("This committee has recorded votes and can't be deleted.", "error")
            return redirect(url_for("roster.committee_detail", committee_id=committee.id))
        for bill in db.session.scalars(select(Bill).where(Bill.committee_id == committee.id)):
            bill.committee_id = None
        db.session.delete(committee)
        db.session.commit()
        flash("Committee deleted.", "success")
        return redirect(url_for("roster.committees"))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That change conflicts with an existing record.", "error")
    return redirect(url_for("roster.committee_detail", committee_id=committee.id))
