import csv
import io

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    AMENDMENT_STAGES, AMENDMENT_STATUSES, BILL_STATUSES, BILL_TYPES, CHAMBERS, Amendment, Bill,
    BillAction, Committee, Member,
)
from .helpers import arg_int, clerk_required, form_int, form_str, get_or_404

bp = Blueprint("bills", __name__)


def log_action(bill, description, chamber=None, vote=None):
    action = BillAction(bill=bill, description=description, chamber=chamber, vote=vote,
                        user_id=current_user.id if current_user.is_authenticated else None)
    db.session.add(action)
    return action


def _next_number(chamber, bill_type):
    current = db.session.scalar(
        select(func.max(Bill.number)).where(Bill.chamber == chamber, Bill.bill_type == bill_type)
    )
    return (current or 0) + 1


def _members():
    return db.session.scalars(
        select(Member).where(Member.active.is_(True))
        .order_by(Member.chamber, Member.last_name, Member.first_name)
    ).all()


def _committees():
    return db.session.scalars(select(Committee).order_by(Committee.chamber, Committee.name)).all()


def _bill_from_form(bill):
    bill.title = form_str("title")
    bill.summary = form_str("summary") or None
    bill.text = form_str("text") or None
    bill.patron_id = form_int("patron_id")
    bill.committee_id = form_int("committee_id")
    copatron_ids = {int(i) for i in request.form.getlist("copatron_ids") if i.isdigit()}
    copatron_ids.discard(bill.patron_id)
    bill.copatrons = [m for m in (db.session.get(Member, i) for i in copatron_ids) if m]
    errors = []
    if not bill.title:
        errors.append("A short title is required.")
    if bill.patron_id and not db.session.get(Member, bill.patron_id):
        errors.append("Unknown patron.")
    if bill.committee_id and not db.session.get(Committee, bill.committee_id):
        errors.append("Unknown committee.")
    return errors


@bp.route("/bills")
def bill_list():
    q = select(Bill).options(selectinload(Bill.patron), selectinload(Bill.committee))
    chamber = request.args.get("chamber")
    status = request.args.get("status")
    committee_id = arg_int("committee")
    search = (request.args.get("q") or "").strip()
    if chamber in CHAMBERS:
        q = q.where(Bill.chamber == chamber)
    if status in BILL_STATUSES:
        q = q.where(Bill.status == status)
    if committee_id:
        q = q.where(Bill.committee_id == committee_id)
    if search:
        like = f"%{search.lower()}%"
        conds = [func.lower(Bill.title).like(like), func.lower(Bill.summary).like(like)]
        # Allow searching "HB12" / "sb 3"
        compact = search.upper().replace(" ", "")
        if len(compact) >= 3 and compact[0] in "HS" and compact[1] in BILL_TYPES and compact[2:].isdigit():
            conds.append((Bill.chamber == ("House" if compact[0] == "H" else "Senate"))
                         & (Bill.bill_type == compact[1]) & (Bill.number == int(compact[2:])))
        q = q.where(or_(*conds))
    q = q.order_by(Bill.chamber, Bill.bill_type, Bill.number)
    bills = db.session.scalars(q).all()
    if request.args.get("format") == "csv":
        return _bills_csv(db.session.scalars(
            q.options(selectinload(Bill.actions), selectinload(Bill.amendments))).all())
    return render_template("bills/list.html", bills=bills, chamber=chamber, status=status,
                           committee_id=committee_id, search=search, committees=_committees())


def _bills_csv(bills):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["bill", "title", "patron", "committee", "status", "amendments",
                     "last_action", "last_action_at"])
    for b in bills:
        last = b.actions[0] if b.actions else None
        writer.writerow([b.label, b.title, b.patron.display_name if b.patron else "",
                         b.committee.display_name if b.committee else "", b.status,
                         len(b.amendments), last.description if last else "",
                         last.created_at.isoformat(timespec="minutes") if last else ""])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=bills.csv"})


@bp.route("/bills/new", methods=["GET", "POST"])
@clerk_required
def new_bill():
    bill = Bill(chamber=request.args.get("chamber") or "House", bill_type="B", status="Introduced")
    if request.method == "POST":
        bill.chamber = form_str("chamber")
        bill.bill_type = form_str("bill_type", "B")
        errors = _bill_from_form(bill)
        if bill.chamber not in CHAMBERS:
            errors.append("Choose a chamber.")
        if bill.bill_type not in BILL_TYPES:
            errors.append("Choose a bill type.")
        requested = form_int("number")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("bills/form.html", bill=bill, members=_members(),
                                   committees=_committees(), next_number=requested)
        bill.status = "In Committee" if bill.committee_id else "Introduced"
        # Retry in case two clerks file a bill in the same chamber at the same instant.
        for _attempt in range(5):
            bill.number = requested or _next_number(bill.chamber, bill.bill_type)
            db.session.add(bill)
            try:
                db.session.flush()
                break
            except IntegrityError:
                db.session.rollback()
                if requested:
                    flash(f"{bill.label} already exists. Pick another number or leave it blank.", "error")
                    return render_template("bills/form.html", bill=bill, members=_members(),
                                           committees=_committees(), next_number=None)
        else:
            flash("Couldn't assign a bill number; please try again.", "error")
            return redirect(url_for("bills.new_bill"))
        log_action(bill, "Introduced" + (f" by {bill.patron.display_name}" if bill.patron else ""),
                   chamber=bill.chamber)
        if bill.committee:
            log_action(bill, f"Referred to {bill.committee.display_name}", chamber=bill.chamber)
        db.session.commit()
        flash(f"Filed {bill.label}: {bill.title}.", "success")
        return redirect(url_for("bills.bill_detail", bill_id=bill.id))
    return render_template("bills/form.html", bill=bill, members=_members(), committees=_committees(),
                           next_number=None)


@bp.route("/bills/<int:bill_id>")
def bill_detail(bill_id):
    bill = get_or_404(Bill, bill_id)
    return render_template("bills/detail.html", bill=bill, committees=_committees(), members=_members())


@bp.route("/bills/<int:bill_id>/print")
def bill_print(bill_id):
    bill = get_or_404(Bill, bill_id)
    return render_template("bills/print.html", bill=bill)


@bp.route("/bills/<int:bill_id>/edit", methods=["GET", "POST"])
@clerk_required
def edit_bill(bill_id):
    bill = get_or_404(Bill, bill_id)
    if request.method == "POST":
        old_committee = bill.committee_id
        errors = _bill_from_form(bill)
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "error")
        else:
            if bill.committee_id != old_committee and bill.committee_id:
                log_action(bill, f"Referred to {db.session.get(Committee, bill.committee_id).display_name}",
                           chamber=bill.chamber)
            db.session.commit()
            flash(f"Saved {bill.label}.", "success")
            return redirect(url_for("bills.bill_detail", bill_id=bill.id))
    return render_template("bills/form.html", bill=bill, members=_members(), committees=_committees(),
                           next_number=bill.number)


@bp.route("/bills/<int:bill_id>/delete", methods=["POST"])
@clerk_required
def delete_bill(bill_id):
    bill = get_or_404(Bill, bill_id)
    if request.form.get("confirm") != bill.label:
        flash(f"Type {bill.label} to confirm deletion.", "error")
        return redirect(url_for("bills.bill_detail", bill_id=bill.id))
    label = bill.label
    for action in bill.actions:
        action.vote = None
    db.session.flush()
    db.session.delete(bill)
    db.session.commit()
    flash(f"Deleted {label} and all of its amendments, votes and history.", "success")
    return redirect(url_for("bills.bill_list"))


@bp.route("/bills/<int:bill_id>/action", methods=["POST"])
@clerk_required
def add_action(bill_id):
    bill = get_or_404(Bill, bill_id)
    status = form_str("status")
    description = form_str("description")
    committee_id = form_int("committee_id")
    chamber = form_str("chamber") or bill.chamber
    chamber = chamber if chamber in CHAMBERS + ["Governor"] else bill.chamber
    changed = False
    if committee_id and committee_id != bill.committee_id:
        committee = db.session.get(Committee, committee_id)
        if committee:
            bill.committee = committee
            log_action(bill, f"Referred to {committee.display_name}", chamber=committee.chamber)
            if status not in BILL_STATUSES:
                status = "In Committee"
            changed = True
    if status in BILL_STATUSES and status != bill.status:
        bill.status = status
        log_action(bill, description or f"Status: {status}", chamber=chamber)
        changed = True
    elif description:
        log_action(bill, description, chamber=chamber)
        changed = True
    if changed:
        db.session.commit()
        flash("Action recorded.", "success")
    else:
        flash("Nothing to record.", "info")
    return redirect(url_for("bills.bill_detail", bill_id=bill.id) + "#history")


@bp.route("/bills/<int:bill_id>/actions/<int:action_id>/delete", methods=["POST"])
@clerk_required
def delete_action(bill_id, action_id):
    action = get_or_404(BillAction, action_id)
    if action.bill_id != bill_id:
        return redirect(url_for("bills.bill_detail", bill_id=bill_id))
    db.session.delete(action)
    db.session.commit()
    flash("History entry removed.", "success")
    return redirect(url_for("bills.bill_detail", bill_id=bill_id) + "#history")


# ---------------------------------------------------------------- amendments

@bp.route("/amendments")
def amendment_list():
    q = select(Amendment).join(Bill).options(selectinload(Amendment.bill), selectinload(Amendment.offered_by))
    status = request.args.get("status")
    if status in AMENDMENT_STATUSES:
        q = q.where(Amendment.status == status)
    q = q.order_by(Bill.chamber, Bill.bill_type, Bill.number, Amendment.number)
    return render_template("amendments/list.html", amendments=db.session.scalars(q).all(), status=status)


@bp.route("/bills/<int:bill_id>/amendments/new", methods=["GET", "POST"])
@clerk_required
def new_amendment(bill_id):
    bill = get_or_404(Bill, bill_id)
    amendment = Amendment(stage="Floor", status="Pending", chamber=bill.chamber)
    if request.method == "POST":
        errors = _amendment_from_form(amendment)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            for _attempt in range(5):
                amendment.number = (db.session.scalar(
                    select(func.max(Amendment.number)).where(Amendment.bill_id == bill.id)) or 0) + 1
                amendment.bill = bill
                db.session.add(amendment)
                try:
                    db.session.flush()
                    break
                except IntegrityError:
                    db.session.rollback()
            else:
                flash("Couldn't number the amendment; please try again.", "error")
                return redirect(url_for("bills.bill_detail", bill_id=bill.id))
            who = f" by {amendment.offered_by.display_name}" if amendment.offered_by else ""
            log_action(bill, f"{amendment.stage} amendment #{amendment.number} offered{who}",
                       chamber=amendment.chamber)
            db.session.commit()
            flash(f"Recorded {amendment.label}.", "success")
            return redirect(url_for("bills.bill_detail", bill_id=bill.id) + "#amendments")
    return render_template("amendments/form.html", bill=bill, amendment=amendment, members=_members())


def _amendment_from_form(amendment):
    amendment.text = form_str("text")
    amendment.offered_by_id = form_int("offered_by_id")
    stage = form_str("stage")
    amendment.stage = stage if stage in AMENDMENT_STAGES else "Floor"
    chamber = form_str("chamber")
    amendment.chamber = chamber if chamber in CHAMBERS + ["Governor"] else amendment.chamber
    if amendment.stage == "Governor":
        amendment.chamber = "Governor"
    status = form_str("status")
    if status in AMENDMENT_STATUSES:
        amendment.status = status
    errors = []
    if not amendment.text:
        errors.append("Amendment text is required.")
    if amendment.offered_by_id and not db.session.get(Member, amendment.offered_by_id):
        errors.append("Unknown member.")
    return errors


@bp.route("/amendments/<int:amendment_id>/edit", methods=["GET", "POST"])
@clerk_required
def edit_amendment(amendment_id):
    amendment = get_or_404(Amendment, amendment_id)
    bill = amendment.bill
    if request.method == "POST":
        old_status = amendment.status
        errors = _amendment_from_form(amendment)
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "error")
        else:
            if amendment.status != old_status:
                log_action(bill, f"Amendment #{amendment.number} {amendment.status.lower()}",
                           chamber=amendment.chamber)
            db.session.commit()
            flash(f"Saved {amendment.label}.", "success")
            return redirect(url_for("bills.bill_detail", bill_id=bill.id) + "#amendments")
    return render_template("amendments/form.html", bill=bill, amendment=amendment, members=_members())


@bp.route("/amendments/<int:amendment_id>/delete", methods=["POST"])
@clerk_required
def delete_amendment(amendment_id):
    amendment = get_or_404(Amendment, amendment_id)
    bill_id = amendment.bill_id
    for vote in amendment.votes:
        vote.amendment_id = None
    db.session.delete(amendment)
    db.session.commit()
    flash("Amendment deleted.", "success")
    return redirect(url_for("bills.bill_detail", bill_id=bill_id) + "#amendments")
