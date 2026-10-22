import click
from sqlalchemy import func, select

from .extensions import db
from .models import Committee, CommitteeAssignment, Member, User


def register(app):
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(["admin", "clerk"]), default="admin")
    @click.password_option()
    def create_user(username, role, password):
        """Create (or reset the password of) a login account."""
        user = db.session.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if user is None:
            user = User(username=username)
            db.session.add(user)
        user.role = role
        user.set_password(password)
        db.session.commit()
        click.echo(f"Saved {role} '{username}'.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Load a small sample roster and committees for trying the app out."""
        if db.session.scalar(select(func.count(Member.id))):
            click.echo("Members already exist; not seeding.")
            return
        house = [("Ava", "Brooks", "D", "1"), ("Ben", "Carter", "R", "2"), ("Chloe", "Diaz", "D", "3"),
                 ("Dev", "Evans", "R", "4"), ("Ella", "Foster", "D", "5"), ("Finn", "Garcia", "R", "6"),
                 ("Grace", "Hughes", "D", "7"), ("Henry", "Iqbal", "R", "8"), ("Isla", "Jones", "I", "9")]
        senate = [("Jack", "Kim", "D", "1"), ("Kara", "Lopez", "R", "2"), ("Liam", "Moore", "D", "3"),
                  ("Maya", "Nguyen", "R", "4"), ("Noah", "Ortiz", "D", "5")]
        members = []
        for chamber, rows in (("House", house), ("Senate", senate)):
            for first, last, party, district in rows:
                m = Member(first_name=first, last_name=last, party=party, district=district,
                           chamber=chamber, school="Demo University")
                db.session.add(m)
                members.append(m)
        members[0].position = "Speaker of the House"
        for chamber, name in (("House", "Courts of Justice"), ("House", "Education"),
                              ("Senate", "Finance and Appropriations"), ("Senate", "Rules")):
            c = Committee(chamber=chamber, name=name)
            db.session.add(c)
            pool = [m for m in members if m.chamber == chamber]
            for i, m in enumerate(pool[:5]):
                c.assignments.append(CommitteeAssignment(member=m, role="Chair" if i == 0 else "Member"))
        db.session.commit()
        click.echo(f"Seeded {len(members)} members and 4 committees.")
