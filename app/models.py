"""Database models for the Virginia Government Simulation tracker."""
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


CHAMBERS = ["House", "Senate"]
PARTIES = {"D": "Democrat", "R": "Republican", "I": "Independent"}
BILL_TYPES = {"B": "Bill", "J": "Joint Resolution", "R": "Resolution"}
BILL_STATUSES = [
    "Introduced",
    "In Committee",
    "Reported from Committee",
    "Failed in Committee",
    "Continued",
    "Passed House",
    "Passed Senate",
    "Failed on Floor",
    "In Conference",
    "Passed General Assembly",
    "Signed by Governor",
    "Governor's Amendments",
    "Vetoed",
    "Veto Overridden",
    "Withdrawn",
]
AMENDMENT_STAGES = ["Committee", "Floor", "Governor"]
AMENDMENT_STATUSES = ["Pending", "Adopted", "Rejected", "Withdrawn", "Ruled Not Germane"]
POSITIONS = {"Y": "Yea", "N": "Nay", "A": "Abstain", "X": "Not Voting"}
THRESHOLDS = {"majority": "Simple majority", "two_thirds": "Two-thirds"}
ROLES = {"admin": "Administrator", "clerk": "Clerk"}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="clerk")
    created_at = db.Column(db.DateTime, default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"


class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    chamber = db.Column(db.String(20), nullable=False, index=True)
    district = db.Column(db.String(20))
    party = db.Column(db.String(1))
    school = db.Column(db.String(120))
    position = db.Column(db.String(80))  # e.g. Speaker, Majority Leader
    email = db.Column(db.String(120))
    active = db.Column(db.Boolean, nullable=False, default=True)
    notes = db.Column(db.Text)

    assignments = db.relationship(
        "CommitteeAssignment", back_populates="member", cascade="all, delete-orphan"
    )

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def title(self):
        return {"House": "Del.", "Senate": "Sen."}.get(self.chamber, "")

    @property
    def tag(self):
        parts = [p for p in (self.party, self.district) if p]
        return f"({'-'.join(parts)})" if parts else ""

    @property
    def display_name(self):
        return " ".join(p for p in (self.title, self.full_name, self.tag) if p)


class Committee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    chamber = db.Column(db.String(20), nullable=False)
    __table_args__ = (UniqueConstraint("chamber", "name"),)

    assignments = db.relationship(
        "CommitteeAssignment", back_populates="committee", cascade="all, delete-orphan"
    )

    @property
    def display_name(self):
        return f"{self.chamber} {self.name}"

    def active_members(self):
        return sorted(
            (a.member for a in self.assignments if a.member.active),
            key=lambda m: (m.last_name.lower(), m.first_name.lower()),
        )


class CommitteeAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    committee_id = db.Column(db.Integer, db.ForeignKey("committee.id"), nullable=False)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="Member")  # Chair, Vice Chair, Member
    __table_args__ = (UniqueConstraint("committee_id", "member_id"),)

    committee = db.relationship("Committee", back_populates="assignments")
    member = db.relationship("Member", back_populates="assignments")


bill_copatrons = db.Table(
    "bill_copatrons",
    db.Column("bill_id", db.Integer, db.ForeignKey("bill.id"), primary_key=True),
    db.Column("member_id", db.Integer, db.ForeignKey("member.id"), primary_key=True),
)


class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chamber = db.Column(db.String(20), nullable=False)
    bill_type = db.Column(db.String(1), nullable=False, default="B")
    number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(300), nullable=False)
    summary = db.Column(db.Text)
    text = db.Column(db.Text)
    patron_id = db.Column(db.Integer, db.ForeignKey("member.id"))
    committee_id = db.Column(db.Integer, db.ForeignKey("committee.id"))
    status = db.Column(db.String(40), nullable=False, default="Introduced", index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("chamber", "bill_type", "number"),)

    patron = db.relationship("Member", foreign_keys=[patron_id])
    committee = db.relationship("Committee")
    copatrons = db.relationship("Member", secondary=bill_copatrons)
    actions = db.relationship(
        "BillAction", back_populates="bill", cascade="all, delete-orphan",
        order_by="BillAction.created_at.desc()",
    )
    amendments = db.relationship(
        "Amendment", back_populates="bill", cascade="all, delete-orphan",
        order_by="Amendment.number",
    )
    votes = db.relationship(
        "Vote", back_populates="bill", cascade="all, delete-orphan",
        order_by="Vote.taken_at.desc()",
    )

    @property
    def label(self):
        return f"{self.chamber[0]}{self.bill_type}{self.number}"


class BillAction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=False, index=True)
    chamber = db.Column(db.String(20))
    description = db.Column(db.String(500), nullable=False)
    vote_id = db.Column(db.Integer, db.ForeignKey("vote.id", ondelete="SET NULL"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    bill = db.relationship("Bill", back_populates="actions")
    vote = db.relationship("Vote")
    user = db.relationship("User")


class Amendment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    offered_by_id = db.Column(db.Integer, db.ForeignKey("member.id"))
    chamber = db.Column(db.String(20))
    stage = db.Column(db.String(20), nullable=False, default="Floor")
    text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("bill_id", "number"),)

    bill = db.relationship("Bill", back_populates="amendments")
    offered_by = db.relationship("Member")
    votes = db.relationship("Vote", back_populates="amendment")

    @property
    def label(self):
        return f"{self.bill.label} Amdt. {self.number}"


class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bill.id"), nullable=False, index=True)
    amendment_id = db.Column(db.Integer, db.ForeignKey("amendment.id", ondelete="SET NULL"))
    chamber = db.Column(db.String(20), nullable=False)
    committee_id = db.Column(db.Integer, db.ForeignKey("committee.id"))
    motion = db.Column(db.String(200), nullable=False)
    threshold = db.Column(db.String(20), nullable=False, default="majority")
    voice = db.Column(db.Boolean, nullable=False, default=False)
    voice_result = db.Column(db.String(10))  # Passed/Failed for voice votes
    tie_breaker = db.Column(db.String(1))  # Lt. Governor's tie-breaking vote: Y or N
    notes = db.Column(db.Text)
    taken_at = db.Column(db.DateTime, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"))

    bill = db.relationship("Bill", back_populates="votes")
    amendment = db.relationship("Amendment", back_populates="votes")
    committee = db.relationship("Committee")
    user = db.relationship("User")
    member_votes = db.relationship(
        "MemberVote", back_populates="vote", cascade="all, delete-orphan"
    )

    @property
    def body(self):
        return self.committee.display_name if self.committee else f"{self.chamber} Floor"

    def tally(self):
        counts = {k: 0 for k in POSITIONS}
        for mv in self.member_votes:
            counts[mv.position] = counts.get(mv.position, 0) + 1
        return counts

    @property
    def tally_text(self):
        if self.voice:
            return "Voice vote"
        t = self.tally()
        return f"{t['Y']}-Y {t['N']}-N {t['A']}-A"

    @property
    def result(self):
        if self.voice:
            return self.voice_result or "Failed"
        t = self.tally()
        return compute_result(t["Y"], t["N"], self.threshold, self.tie_breaker)

    @property
    def passed(self):
        return self.result == "Passed"


def compute_result(yeas, nays, threshold="majority", tie_breaker=None):
    """Decide a recorded vote. Abstentions and non-votes do not count."""
    if yeas + nays == 0:
        return "Failed"
    if threshold == "two_thirds":
        return "Passed" if yeas * 3 >= (yeas + nays) * 2 else "Failed"
    if yeas == nays and tie_breaker in ("Y", "N"):
        return "Passed" if tie_breaker == "Y" else "Failed"
    return "Passed" if yeas > nays else "Failed"


class MemberVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vote_id = db.Column(db.Integer, db.ForeignKey("vote.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False, index=True)
    position = db.Column(db.String(1), nullable=False, default="X")
    __table_args__ = (UniqueConstraint("vote_id", "member_id"),)

    vote = db.relationship("Vote", back_populates="member_votes")
    member = db.relationship("Member")
