"""
auth.py
-------
Self-contained authentication for the Scout web app: sign up, sign in,
sign out, and a Flask-session-backed login state.  Users are stored in the
``users`` collection of the same ``tactical_scout`` MongoDB database used by
mongo_loader.py.

Wiring (mirrors imported_player.register_import_routes):

    from auth import register_auth_routes
    register_auth_routes(app)

This adds the routes:
    GET/POST  /signup
    GET/POST  /signin
    GET       /logout

and a ``current_user`` context global + a ``login_required`` decorator that
other routes can opt into later (nothing is gated by default).

Passwords are hashed with werkzeug PBKDF2-SHA256.  The session secret comes
from the SECRET_KEY env var, with a dev fallback so the app still runs locally.
"""

import os
import re
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import (
    request, session, redirect, url_for, render_template, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError, PyMongoError
except ImportError:
    raise ImportError("pymongo is required. Run: pip install pymongo")

_logger = logging.getLogger(__name__)

# ── Connection (same DB as mongo_loader) ──────────────────────────────────────
_MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
_DB_NAME   = "tactical_scout"

_client: MongoClient | None = None
_indexes_ready = False


def _db():
    global _client
    if _client is None:
        from db_tls import tls_kwargs
        _client = MongoClient(_MONGO_URI, serverSelectionTimeoutMS=5000, **tls_kwargs(_MONGO_URI))
    return _client[_DB_NAME]


def _users():
    """Return the users collection, ensuring unique indexes exist once."""
    global _indexes_ready
    coll = _db().users
    if not _indexes_ready:
        # Case-insensitive uniqueness so "Alice" and "alice" can't both exist.
        coll.create_index("username", unique=True)
        coll.create_index("email", unique=True)
        _indexes_ready = True
    return coll


# ── Validation helpers ────────────────────────────────────────────────────────
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_EMAIL_RE    = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_signup(username: str, email: str, password: str, confirm: str):
    """Return a list of human-readable error strings (empty == valid)."""
    errors = []
    if not _USERNAME_RE.match(username):
        errors.append(
            "Username must be 3–32 characters: letters, numbers, and . _ - only."
        )
    if not _EMAIL_RE.match(email):
        errors.append("Please enter a valid email address.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != confirm:
        errors.append("Passwords do not match.")
    return errors


def _public_user(doc: dict) -> dict:
    """Strip the password hash before exposing a user document anywhere."""
    if not doc:
        return {}
    return {
        "id":       str(doc.get("_id", "")),
        "username": doc.get("username", ""),
        "email":    doc.get("email", ""),
    }


# ── Session helpers ───────────────────────────────────────────────────────────
def _login_user(doc: dict):
    session["user_id"]  = str(doc["_id"])
    session["username"] = doc.get("username", "")
    session.permanent   = True


def current_user():
    """Return the logged-in user (public fields) for this request, or None.

    Cached on flask.g so repeated calls within one request hit Mongo once.
    """
    if "user" in g:
        return g.user
    g.user = None
    uid = session.get("username")
    if uid:
        doc = _users().find_one({"username": uid})
        g.user = _public_user(doc) if doc else None
        if g.user is None:
            session.clear()  # stale session pointing at a deleted user
    return g.user


def login_required(view):
    """Decorator for routes that should require a signed-in user.

    Unused by default — provided so individual routes can opt in later, e.g.

        @app.route('/secret')
        @login_required
        def secret(): ...
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("signin", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ── Route registration ────────────────────────────────────────────────────────
def register_auth_routes(app):
    # Session signing key — required for Flask sessions / flash messages.
    if not app.secret_key:
        app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
        if app.secret_key == "dev-insecure-change-me":
            _logger.warning(
                "SECRET_KEY not set — using an insecure dev key. "
                "Set the SECRET_KEY env var before deploying."
            )

    # Make current_user available to every template as {{ current_user }}.
    @app.context_processor
    def _inject_current_user():
        return {"current_user": current_user()}

    # ── Sign up ───────────────────────────────────────────────────────────────
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if current_user():
            return redirect(url_for("core.home"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email    = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm  = request.form.get("confirm_password", "")

            errors = _validate_signup(username, email, password, confirm)
            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template(
                    "signup.html", username=username, email=email
                ), 400

            try:
                coll = _users()
                # Friendly pre-checks (the unique index is the real guard).
                if coll.find_one({"username": username}):
                    flash("That username is already taken.", "error")
                    return render_template("signup.html", email=email), 409
                if coll.find_one({"email": email}):
                    flash("An account with that email already exists.", "error")
                    return render_template("signup.html", username=username), 409

                doc = {
                    "username":      username,
                    "email":         email,
                    "password_hash": generate_password_hash(
                        password, method="pbkdf2:sha256"
                    ),
                    "created_at":    datetime.now(timezone.utc),
                }
                result = coll.insert_one(doc)
                doc["_id"] = result.inserted_id
                _login_user(doc)
                _logger.info("New user signed up: %s", username)
                flash("Welcome to Scout, %s!" % username, "success")
                return redirect(url_for("core.home"))

            except DuplicateKeyError:
                flash("That username or email is already registered.", "error")
                return render_template(
                    "signup.html", username=username, email=email
                ), 409
            except PyMongoError as exc:
                _logger.error("Sign-up DB error: %s", exc)
                flash("Sign up is temporarily unavailable. Please try again.", "error")
                return render_template(
                    "signup.html", username=username, email=email
                ), 503

        return render_template("signup.html")

    # ── Sign in ───────────────────────────────────────────────────────────────
    @app.route("/signin", methods=["GET", "POST"])
    def signin():
        if current_user():
            return redirect(url_for("core.home"))

        if request.method == "POST":
            identifier = request.form.get("identifier", "").strip()
            password   = request.form.get("password", "")
            ident_l    = identifier.lower()

            try:
                # Allow logging in with either username or email.
                doc = _users().find_one(
                    {"$or": [{"username": identifier}, {"email": ident_l}]}
                )
            except PyMongoError as exc:
                _logger.error("Sign-in DB error: %s", exc)
                flash("Sign in is temporarily unavailable. Please try again.", "error")
                return render_template("signin.html", identifier=identifier), 503

            if doc and check_password_hash(doc.get("password_hash", ""), password):
                _login_user(doc)
                _logger.info("User signed in: %s", doc.get("username"))
                nxt = request.args.get("next") or request.form.get("next")
                # Only honor local relative redirects (avoid open-redirect).
                if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect(url_for("core.home"))

            # Same message for both cases — don't reveal which field was wrong.
            flash("Incorrect username/email or password.", "error")
            return render_template("signin.html", identifier=identifier), 401

        return render_template("signin.html", next=request.args.get("next", ""))

    # ── Sign out ──────────────────────────────────────────────────────────────
    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("core.home"))

    _logger.info("Auth routes registered: /signup, /signin, /logout")
