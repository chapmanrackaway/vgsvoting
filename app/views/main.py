import json

from flask import Blueprint, Response, render_template
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    BILL_STATUSES, CHAMBERS, Amendment, Bill, BillAction, Committee, CommitteeAssignment, Member,
    MemberVote, User, Vote, bill_copatrons, utcnow,
)
from .helpers import admin_required

bp = Blueprint("main", __name__)


@bp.route("/")
@bp.route("/home")
def dashboard():
    counts = {
        "members": db.session.scalar(select(func.count(Member.id)).where(Member.active.is_(True))),
        "bills": db.session.scalar(select(func.count(Bill.id))),
        "amendments": db.session.scalar(select(func.count(Amendment.id))),
        "votes": db.session.scalar(select(func.count(Vote.id))),
    }
    recent_actions = db.session.scalars(
        select(BillAction).options(selectinload(BillAction.bill))
        .order_by(BillAction.created_at.desc()).limit(15)
    ).all()
    recent_votes = db.session.scalars(
        select(Vote).options(selectinload(Vote.bill), selectinload(Vote.member_votes))
        .order_by(Vote.taken_at.desc()).limit(8)
    ).all()
    needs_setup = not db.session.scalar(select(func.count(User.id)))
    return render_template("dashboard.html", counts=counts, recent_actions=recent_actions,
                           recent_votes=recent_votes, needs_setup=needs_setup)


def _dockets():
    """Bills currently sitting in each committee."""
    committees = db.session.scalars(select(Committee).order_by(Committee.chamber, Committee.name)).all()
    pending = db.session.scalars(
        select(Bill).where(Bill.committee_id.is_not(None), Bill.status == "In Committee")
        .order_by(Bill.chamber, Bill.bill_type, Bill.number).options(selectinload(Bill.patron))
    ).all()
    by_committee = {}
    for bill in pending:
        by_committee.setdefault(bill.committee_id, []).append(bill)
    return [(c, by_committee.get(c.id, [])) for c in committees]


@bp.route("/reports")
def reports():
    rows = db.session.execute(
        select(Bill.chamber, Bill.status, func.count(Bill.id)).group_by(Bill.chamber, Bill.status)
    ).all()
    matrix = {s: {c: 0 for c in CHAMBERS} for s in BILL_STATUSES}
    for chamber, status, n in rows:
        matrix.setdefault(status, {c: 0 for c in CHAMBERS})[chamber] = n
    totals = {c: sum(matrix[s][c] for s in matrix) for c in CHAMBERS}
    dockets = _dockets()
    return render_template("reports/index.html", matrix=matrix, totals=totals, dockets=dockets)


@bp.route("/reports/docket")
def docket_print():
    dockets = [(c, bills) for c, bills in _dockets() if bills]
    return render_template("reports/docket.html", dockets=dockets, generated=utcnow())


def _row(obj, cols):
    out = {}
    for col in cols:
        val = getattr(obj, col)
        out[col] = val.isoformat() if hasattr(val, "isoformat") else val
    return out


@bp.route("/backup.json")
@admin_required
def backup():
    def dump(model):
        cols = [c.name for c in model.__table__.columns]
        return [_row(o, cols) for o in db.session.scalars(select(model).order_by(model.id))]

    data = {
        "generated_at": utcnow().isoformat(),
        "members": dump(Member),
        "committees": dump(Committee),
        "committee_assignments": dump(CommitteeAssignment),
        "bills": dump(Bill),
        "bill_copatrons": [dict(r._mapping) for r in db.session.execute(select(bill_copatrons))],
        "bill_actions": dump(BillAction),
        "amendments": dump(Amendment),
        "votes": dump(Vote),
        "member_votes": dump(MemberVote),
    }
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    return Response(json.dumps(data, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": f"attachment; filename=vgs-backup-{stamp}.json"})
