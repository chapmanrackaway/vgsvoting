import io

from app.extensions import db
from app.models import Amendment, Bill, Committee, Member, Vote, compute_result


def add_member(clerk, first, last, chamber="House", party="D", district="1"):
    resp = clerk.post("/roster/new", {"first_name": first, "last_name": last, "chamber": chamber,
                                      "party": party, "district": district, "active": "on"})
    assert resp.status_code == 302


def test_public_pages_load(client):
    for url in ["/", "/home", "/roster", "/committees", "/bills", "/amendments", "/votes",
                "/reports", "/reports/docket", "/healthz", "/login"]:
        assert client.get(url).status_code == 200, url
    assert client.get("/nope").status_code == 404


def test_writes_require_login(client, app):
    resp = client.post("/roster/new", {"first_name": "A", "last_name": "B", "chamber": "House"})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    with app.app_context():
        assert db.session.query(Member).count() == 0


def test_csrf_is_enforced(clerk):
    resp = clerk.post("/roster/new", {"csrf_token": "bad", "first_name": "A", "last_name": "B",
                                      "chamber": "House"})
    assert resp.status_code == 400


def test_compute_result():
    assert compute_result(5, 4) == "Passed"
    assert compute_result(4, 4) == "Failed"
    assert compute_result(4, 4, tie_breaker="Y") == "Passed"
    assert compute_result(6, 3, "two_thirds") == "Passed"
    assert compute_result(5, 3, "two_thirds") == "Failed"
    assert compute_result(0, 0) == "Failed"


def test_full_legislative_flow(clerk, app):
    for i, (first, last) in enumerate([("Ava", "Brooks"), ("Ben", "Carter"), ("Cy", "Diaz")]):
        add_member(clerk, first, last, district=str(i + 1))
    add_member(clerk, "Sam", "Senator", chamber="Senate")
    with app.app_context():
        ids = [m.id for m in db.session.query(Member).order_by(Member.id)]

    clerk.post("/committees", {"name": "Education", "chamber": "House"})
    with app.app_context():
        committee = db.session.query(Committee).one()
        cid = committee.id
    clerk.post(f"/committees/{cid}", {"action": "add", "role": "Member", "member_ids": ids[:3]})
    with app.app_context():
        assert len(db.session.get(Committee, cid).assignments) == 3

    # File two bills; numbers are assigned automatically.
    resp = clerk.post("/bills/new", {"chamber": "House", "bill_type": "B", "title": "School Lunch Act",
                                     "patron_id": ids[0], "committee_id": cid,
                                     "copatron_ids": [ids[1]]})
    assert resp.status_code == 302
    clerk.post("/bills/new", {"chamber": "House", "bill_type": "B", "title": "Second bill"})
    with app.app_context():
        bills = db.session.query(Bill).order_by(Bill.number).all()
        assert [b.label for b in bills] == ["HB1", "HB2"]
        assert bills[0].status == "In Committee"
        assert [m.id for m in bills[0].copatrons] == [ids[1]]
        bill_id = bills[0].id
    # Requesting a taken number is rejected cleanly.
    resp = clerk.post("/bills/new", {"chamber": "House", "bill_type": "B", "title": "Dup", "number": "1"})
    assert resp.status_code == 200 and b"already exists" in resp.data

    assert clerk.get(f"/bills/{bill_id}").status_code == 200
    assert b"HB1" in clerk.get("/bills?q=hb1").data

    # Amendment + vote on amendment.
    clerk.post(f"/bills/{bill_id}/amendments/new", {"text": "Strike line 3", "offered_by_id": ids[1],
                                                   "stage": "Committee", "chamber": "House"})
    with app.app_context():
        amd = db.session.query(Amendment).one()
        assert amd.number == 1
        amd_id = amd.id
    assert clerk.get(f"/bills/{bill_id}/votes/new?amendment={amd_id}&body=committee:{cid}").status_code == 200
    resp = clerk.post(f"/bills/{bill_id}/votes/new", {
        "body": f"committee:{cid}", "amendment_id": amd_id, "motion": "Adopt amendment #1",
        "threshold": "majority", "voice": "0", "update_amendment": "1",
        f"m{ids[0]}": "Y", f"m{ids[1]}": "Y", f"m{ids[2]}": "N"})
    assert resp.status_code == 302
    with app.app_context():
        assert db.session.get(Amendment, amd_id).status == "Adopted"

    # Committee vote reports the bill.
    resp = clerk.post(f"/bills/{bill_id}/votes/new", {
        "body": f"committee:{cid}", "motion": "Report", "threshold": "majority", "voice": "0",
        "status_passed": "Reported from Committee", "status_failed": "Failed in Committee",
        f"m{ids[0]}": "Y", f"m{ids[1]}": "N", f"m{ids[2]}": "A"})
    with app.app_context():
        assert db.session.get(Bill, bill_id).status == "Failed in Committee"  # 1-1 tie fails
        vote = db.session.query(Vote).order_by(Vote.id.desc()).first()
        assert vote.tally()["A"] == 1
        vote_id = vote.id

    # Correct the vote, then floor vote.
    clerk.post(f"/votes/{vote_id}/edit", {"motion": "Report", "threshold": "majority",
                                         f"m{ids[0]}": "Y", f"m{ids[1]}": "Y", f"m{ids[2]}": "A"})
    with app.app_context():
        assert db.session.get(Vote, vote_id).result == "Passed"
    resp = clerk.post(f"/bills/{bill_id}/votes/new", {
        "body": "floor:House", "motion": "Pass", "threshold": "majority", "voice": "1",
        "voice_result": "Passed", "status_passed": "Passed House"})
    with app.app_context():
        assert db.session.get(Bill, bill_id).status == "Passed House"

    # Senate floor with Lt. Governor tie-breaker.
    clerk.post(f"/bills/{bill_id}/votes/new", {
        "body": "floor:Senate", "motion": "Pass", "threshold": "majority", "voice": "0",
        "tie_breaker": "Y", f"m{ids[3]}": "X", "status_passed": "Passed General Assembly"})
    with app.app_context():
        v = db.session.query(Vote).order_by(Vote.id.desc()).first()
        assert v.result == "Failed"  # 0-0 with no votes cast fails even with a tie-breaker
    for url in [f"/votes/{vote_id}", f"/roster/{ids[0]}", f"/bills/{bill_id}/print", "/votes?format=csv",
                "/bills?format=csv", "/roster/export.csv", "/reports", "/", "/backup.json",
                f"/committees/{cid}", "/amendments"]:
        assert clerk.get(url).status_code == 200, url

    # Members with history are deactivated, not deleted.
    clerk.post(f"/roster/{ids[0]}/delete")
    with app.app_context():
        assert db.session.get(Member, ids[0]).active is False

    # Bill deletion requires typing the label, then cascades.
    clerk.post(f"/bills/{bill_id}/delete", {"confirm": "nope"})
    with app.app_context():
        assert db.session.get(Bill, bill_id) is not None
    clerk.post(f"/bills/{bill_id}/delete", {"confirm": "HB1"})
    with app.app_context():
        assert db.session.get(Bill, bill_id) is None
        assert db.session.query(Vote).count() == 0


def test_roster_csv_import(clerk, app):
    csv_data = "First Name,Last Name,Chamber,Party,District,School\n" \
               "Ava,Brooks,House,D,1,UVA\nBen,Carter,senate,R,2,VT\nBad,Row,Council,,,\n"
    resp = clerk.post("/roster/import", {"file": (io.BytesIO(csv_data.encode()), "r.csv")},
                      content_type="multipart/form-data")
    assert resp.status_code == 302
    # Re-import updates rather than duplicates.
    clerk.post("/roster/import", {"file": (io.BytesIO(csv_data.encode()), "r.csv")},
               content_type="multipart/form-data")
    with app.app_context():
        assert db.session.query(Member).count() == 2
        ben = db.session.query(Member).filter_by(last_name="Carter").one()
        assert ben.chamber == "Senate" and ben.school == "VT"


def test_user_management(clerk, client):
    resp = clerk.post("/users", {"username": "house", "password": "short", "role": "clerk"})
    assert b"at least 8" in resp.data
    resp = clerk.post("/users", {"username": "house", "password": "longenough", "role": "clerk"})
    assert resp.status_code == 302
    clerk.post("/logout")
    clerk.get("/login")
    assert clerk.post("/login", {"username": "House", "password": "longenough"}).status_code == 302
    clerk.get("/")
    assert clerk.get("/users").status_code == 403


def test_private_mode(app):
    app.config["PUBLIC_READ"] = False
    c = app.test_client()
    assert c.get("/bills").status_code == 302
    assert c.get("/healthz").status_code == 200
    assert c.get("/login").status_code == 200
