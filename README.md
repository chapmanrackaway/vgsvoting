# Virginia Government Simulation — Legislative Tracker

A web app for running a student simulation of the Virginia General Assembly. It keeps the
**roster**, files and tracks **bills**, logs **amendments**, and records **votes**: roll call, voice,
committee and floor.

Anyone can view everything without logging in. Clerks log in to make changes.

## Features

| Area | What it does |
| --- | --- |
| **Roster** | Delegates and Senators with party, district, school, and leadership position. Add members one at a time or import a CSV (re-importing updates members, it doesn't duplicate them). Export to CSV. Members with a voting record are marked inactive, never deleted. |
| **Committees** | House and Senate committees with Chair, Vice Chair, and members. Each committee page shows its docket. |
| **Bills** | HB/SB/HJ/SJ/HR/SR numbering is assigned automatically. Each bill has a chief patron, co-patrons, a summary and full text, a committee referral, a status, and a timestamped history. It also has a printable view. |
| **Amendments** | Numbered per bill. Each records who offered it, the stage (committee, floor, or Governor), the text, and its status. A vote on an amendment can mark it Adopted or Rejected automatically. |
| **Votes** | A roll-call grid of the eligible members (committee members, or the whole chamber for floor votes), with a live tally. Buttons set every member at once, and Y/N/A/X keys enter votes from the keyboard. Also supports voice votes, simple-majority and two-thirds thresholds, and the Lieutenant Governor's tie-breaking vote in the Senate. Recording a vote writes the bill's history and can update its status. Votes can be corrected later. |
| **Reports** | Each member's voting record and a vote breakdown by party. Bill status counts by chamber. Printable committee dockets. CSV exports of bills, votes (one row per member's vote) and the roster. Administrators can download a full JSON backup. |
| **Accounts** | Administrator and Clerk roles. Optional private mode (`PUBLIC_READ=0`) requires login to view anything. |

## Why it's more reliable than the old site

- **It uses a real database server.** In production, data lives in PostgreSQL (for example, Cloud SQL), not on the web server's disk. App Engine's disk is wiped whenever an instance restarts, so it can't hold the data. Dropped database connections are detected and replaced automatically (`pool_pre_ping`).
- **Simultaneous clerks are safe.** Bill and amendment numbers are protected by database unique constraints and retried on conflict. In testing, 40 clerks filing bills at the same instant got SB1–SB40 with no duplicates. Double-clicking a submit button is blocked.
- **Errors don't take the site down.** Bad input, expired forms, and database hiccups show a friendly page and roll back cleanly. `/healthz` reports whether the app and database are healthy.
- **Production web server.** Gunicorn runs several workers and threads and restarts workers periodically. App Engine keeps one instance warm (`min_instances: 1`) so it never cold-starts during a session.
- **Few moving parts.** No external CDNs or JavaScript frameworks: every page works even with JavaScript turned off.
- **Tested.** An automated suite covers the whole flow (roster → committee → bill → amendment → votes → status) and runs on both SQLite and PostgreSQL.

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export SECRET_KEY=dev
flask --app wsgi seed-demo          # optional: sample roster and committees
flask --app wsgi run --debug        # http://127.0.0.1:5000
```

The first time you open the site with no accounts, it links you to **/setup** to create the
administrator. You can also create accounts from the command line:
`flask --app wsgi create-user NAME --role admin`.

Locally, data is stored in SQLite at `instance/vgs.db`. Run the tests with `pytest`. To run them against
PostgreSQL, set `TEST_DATABASE_URL=postgresql+psycopg://user@host/db` first.

## Deploy

### Option A — Google App Engine + Cloud SQL (same platform as the current site)

1. Create a Cloud SQL for PostgreSQL instance, a database, and a user.
   `db-f1-micro` is plenty for a simulation.
2. In `app.yaml`, set `SECRET_KEY` to a long random string. Set `DATABASE_URL` to:
   `postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE`
3. Give the App Engine service account the **Cloud SQL Client** role.
4. Run `gcloud app deploy`. Then visit `/setup` to create the administrator account.

The app creates its tables on first start.

### Option B — Docker (any server, Cloud Run, Render, Fly.io, …)

```bash
SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") docker compose up -d
```

This starts the app and PostgreSQL together, with data kept in a Docker volume. On a platform that
gives you a managed Postgres, build the `Dockerfile` and set `DATABASE_URL` and `SECRET_KEY`.

### Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | SQLite in `instance/` | PostgreSQL connection URL (`postgres://` and `postgresql://` both work). |
| `SECRET_KEY` | random per start | **Required in production.** Without it, everyone is logged out on every restart. |
| `PUBLIC_READ` | `1` | Set to `0` to require login for viewing. |
| `SIM_NAME` | Virginia Government Simulation | Name shown in the header. |
| `TIMEZONE` | `America/New_York` | Time zone used to display timestamps. |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS (already set in `app.yaml`). |
| `WEB_CONCURRENCY` / `GUNICORN_THREADS` | 2 / 4 | Web server worker and thread counts. |

## Roster CSV format

```csv
first_name,last_name,chamber,party,district,school,position,email,active
Ava,Brooks,House,D,12,James Madison University,Speaker of the House,ava@example.edu,yes
```

`first_name`, `last_name` and `chamber` (House/Senate) are required. Header names are
case-insensitive, so "First Name" works too.
