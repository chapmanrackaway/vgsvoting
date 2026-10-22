"""Virginia Government Simulation legislative tracker."""
import logging
import os
import secrets
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFError
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .extensions import csrf, db, login_manager
from . import models

log = logging.getLogger(__name__)


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(os.path.abspath(os.getcwd()), "instance", "vgs.db")
    # Heroku/Render style URLs use postgres://; SQLAlchemy needs an explicit driver.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    # WAL + busy timeout keep SQLite from throwing "database is locked" under
    # concurrent clerks; foreign keys are off by default in SQLite.
    if type(dbapi_conn).__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=False)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        secret = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set; using a random key. Logins will reset on restart.")

    app.config.update(
        SECRET_KEY=secret,
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 280},
        PUBLIC_READ=os.environ.get("PUBLIC_READ", "1") not in ("0", "false", "False"),
        TIMEZONE=os.environ.get("TIMEZONE", "America/New_York"),
        SIM_NAME=os.environ.get("SIM_NAME", "Virginia Government Simulation"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
        WTF_CSRF_TIME_LIMIT=None,  # forms left open all session shouldn't expire
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"timeout": 15}}
        path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    from .views import auth, bills, main, roster, votes

    for bp in (auth.bp, main.bp, roster.bp, bills.bp, votes.bp):
        app.register_blueprint(bp)

    from . import cli

    cli.register(app)

    with app.app_context():
        try:
            db.create_all()
        except OperationalError:
            # Another worker may be creating the same tables at the same moment.
            db.session.rollback()
            log.exception("create_all failed; continuing (tables likely exist)")

    tz = ZoneInfo(app.config["TIMEZONE"])

    @app.template_filter("localtime")
    def localtime(dt, fmt="%b %d, %Y %I:%M %p"):
        if not dt:
            return ""
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime(fmt)

    @app.context_processor
    def inject_constants():
        return {
            "CHAMBERS": models.CHAMBERS,
            "PARTIES": models.PARTIES,
            "BILL_TYPES": models.BILL_TYPES,
            "BILL_STATUSES": models.BILL_STATUSES,
            "AMENDMENT_STAGES": models.AMENDMENT_STAGES,
            "AMENDMENT_STATUSES": models.AMENDMENT_STATUSES,
            "POSITIONS": models.POSITIONS,
            "THRESHOLDS": models.THRESHOLDS,
            "ROLES": models.ROLES,
            "SIM_NAME": app.config["SIM_NAME"],
            "can_edit": current_user.is_authenticated,
        }

    @app.before_request
    def require_login_when_private():
        if app.config["PUBLIC_READ"] or current_user.is_authenticated:
            return None
        if request.endpoint in (None, "static", "healthz") or (
            request.endpoint or ""
        ).startswith("auth."):
            return None
        return redirect(url_for("auth.login", next=request.full_path))

    @app.route("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok")
        except Exception:  # noqa: BLE001 - health check must never raise
            db.session.rollback()
            log.exception("health check failed")
            return jsonify(status="db_error"), 503

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="That page doesn't exist."), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403,
                               message="Only administrators can do that."), 403

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
        return render_template(
            "error.html", code=400,
            message="This form had expired or was submitted twice. Go back, reload the page and try again.",
        ), 400

    @app.errorhandler(400)
    def bad_request(e):
        msg = getattr(e, "description", None) or "The request couldn't be processed."
        return render_template("error.html", code=400, message=msg), 400

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return render_template(
            "error.html", code=500,
            message="Something went wrong. Your last change may not have been saved; please try again.",
        ), 500

    @app.errorhandler(OperationalError)
    def db_unavailable(_e):
        db.session.rollback()
        log.exception("database error")
        return render_template(
            "error.html", code=503,
            message="The database is temporarily unavailable. Please try again in a moment.",
        ), 503

    return app


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(models.User, int(user_id))
    except (TypeError, ValueError):
        return None
