from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, select

from ..extensions import db
from ..models import ROLES, User
from .helpers import admin_required, form_str, get_or_404, safe_next

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = form_str("username").lower()
        user = db.session.scalar(select(User).where(func.lower(User.username) == username))
        if user and user.check_password(request.form.get("password", "")):
            login_user(user, remember=True)
            return redirect(safe_next(url_for("main.dashboard")))
        flash("Incorrect username or password.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.dashboard"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Current password is incorrect.", "error")
        elif len(request.form.get("new_password", "")) < 8:
            flash("New password must be at least 8 characters.", "error")
        else:
            current_user.set_password(request.form["new_password"])
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/account.html")


@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = form_str("username")
        password = request.form.get("password", "")
        role = form_str("role", "clerk")
        if not username or len(password) < 8 or role not in ROLES:
            flash("Username is required, password must be at least 8 characters.", "error")
        elif db.session.scalar(select(User).where(func.lower(User.username) == username.lower())):
            flash("That username is already taken.", "error")
        else:
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"Created {ROLES[role].lower()} account '{username}'.", "success")
            return redirect(url_for("auth.users"))
    all_users = db.session.scalars(select(User).order_by(User.username)).all()
    return render_template("auth/users.html", users=all_users)


@bp.route("/users/<int:user_id>/reset", methods=["POST"])
@admin_required
def reset_password(user_id):
    user = get_or_404(User, user_id)
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
    else:
        user.set_password(password)
        role = form_str("role")
        if role in ROLES and user.id != current_user.id:
            user.role = role
        db.session.commit()
        flash(f"Updated '{user.username}'.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f"Deleted '{user.username}'.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run: create the initial admin when no users exist."""
    if db.session.scalar(select(func.count(User.id))):
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = form_str("username")
        password = request.form.get("password", "")
        if not username or len(password) < 8:
            flash("Username is required, password must be at least 8 characters.", "error")
        else:
            user = User(username=username, role="admin")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash("Administrator account created. Welcome!", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/setup.html")

