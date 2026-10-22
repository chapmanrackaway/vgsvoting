from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from ..extensions import db


def clerk_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to make changes.", "info")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path))
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def get_or_404(model, ident):
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404)
    return obj


def form_str(name, default=""):
    return (request.form.get(name) or default).strip()


def form_int(name):
    value = (request.form.get(name) or "").strip()
    try:
        return int(value) if value else None
    except ValueError:
        return None


def arg_int(name):
    value = (request.args.get(name) or "").strip()
    try:
        return int(value) if value else None
    except ValueError:
        return None


def safe_next(default):
    target = request.args.get("next") or request.form.get("next") or ""
    # Only allow same-site relative redirects.
    if target.startswith("/") and not target.startswith("//"):
        return target
    return default
