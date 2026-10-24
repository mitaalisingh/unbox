"""Unbox, multi-user version: accounts and trips on top of Supabase.

Run (needs the values in .env, see .env.example):
    python -m dotenv run -- flask --app webapp run     # or: set the variables yourself and run  python webapp.py

How login works: the server calls Supabase Auth, keeps the user's tokens in a signed, HttpOnly cookie, and
sends the USER'S token with every database call. The database's row-level rules then decide what that user can
see, so a bug here cannot expose another person's trips.
"""
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sbclient import Supabase, SupabaseError

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


def load_dotenv(path=".env"):
    """Tiny .env reader so local runs work without extra tools. Real environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def valid_email(s):
    return bool(s) and len(s) <= 254 and bool(EMAIL_RE.match(s))


def password_problem(pw, email=""):
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > 128:
        return "That password is too long (128 characters at most)."
    if email and pw.lower() == email.lower():
        return "Your password can't be your email address."
    return None


def safe_next(target, fallback):
    """Only allow redirects to a path on this site (blocks //evil.com and https://evil.com)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


def create_app(sb=None, testing=False):
    load_dotenv()
    app = Flask(__name__)
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if not testing:
            raise RuntimeError("SECRET_KEY is not set (see .env.example).")
        secret = secrets.token_hex(32)
    dev_http = os.environ.get("DEV_HTTP") == "1" or testing
    app.config.update(
        SECRET_KEY=secret, TESTING=testing,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=not dev_http,
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600, MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if os.environ.get("BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    client = sb or Supabase()
    app.sb = client

    # ---------------- CSRF: every POST must carry the token that was put in the page ----------------
    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Your page expired. Please go back, reload it and try again.")

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    # ---------------- sessions ----------------
    def start_session(tokens):
        user = tokens.get("user") or {}
        csrf = session.get("csrf")
        session.clear()
        session.permanent = True
        session.update(at=tokens["access_token"], rt=tokens.get("refresh_token", ""),
                       exp=int(time.time()) + int(tokens.get("expires_in", 3600)),
                       uid=user.get("id", ""), email=user.get("email", ""))
        session["csrf"] = csrf or secrets.token_urlsafe(32)

    def current_user():
        """Return {'id','email','token'} for a logged-in visitor, refreshing an expired token if possible."""
        if not session.get("at"):
            return None
        if time.time() > session.get("exp", 0) - 30:
            try:
                start_session(client.refresh(session.get("rt", "")))
            except SupabaseError:
                session.clear()
                return None
        return {"id": session["uid"], "email": session["email"], "token": session["at"]}

    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            user = current_user()
            if not user:
                return redirect(url_for("login", next=request.path))
            request.user = user
            return view(*a, **kw)
        return wrapped

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "logged_in": bool(session.get("at")), "user_email": session.get("email", "")}

    def callback_url():
        return request.url_root.rstrip("/") + url_for("auth_callback")

    def has_profile(user):
        return bool(client.select("profiles", user["token"], {"select": "user_id", "user_id": f"eq.{user['id']}"}))

    def create_profile_from_metadata(user, meta):
        client.insert("profiles", user["token"], {
            "user_id": user["id"], "display_name": meta.get("display_name"),
            "accepted_terms_at": meta["accepted_terms_at"], "confirmed_18_plus": True})

    def after_login(tokens, nxt=None):
        """Log in, make sure a consent record exists, then go on."""
        start_session(tokens)
        user = current_user()
        meta = (client.get_user(user["token"]) or {}).get("user_metadata") or {}
        if not has_profile(user):
            if meta.get("confirmed_18_plus") and meta.get("accepted_terms_at"):
                create_profile_from_metadata(user, meta)
            else:
                return redirect(url_for("consent"))
        return redirect(safe_next(nxt, url_for("trips")))

    # ---------------- public pages ----------------
    @app.route("/")
    def home():
        return redirect(url_for("trips")) if session.get("at") else render_template("auth/landing.html")

    @app.route("/privacy")
    def privacy():
        return render_template("auth/privacy.html")

    # ---------------- sign-up / login / reset ----------------
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            problem = None
            if not valid_email(email):
                problem = "Please enter a valid email address."
            elif password_problem(pw, email):
                problem = password_problem(pw, email)
            elif pw != request.form.get("password2", ""):
                problem = "The two passwords don't match."
            elif not request.form.get("age18"):
                problem = "Unbox is for people aged 18 and over."
            elif not request.form.get("terms"):
                problem = "Please accept the privacy notice to continue."
            if problem:
                flash(problem)
                return render_template("auth/signup.html", email=email), 400
            meta = {"confirmed_18_plus": True, "accepted_terms_at": datetime.now(timezone.utc).isoformat()}
            try:
                client.sign_up(email, pw, meta, redirect_to=callback_url())
            except SupabaseError as e:
                if e.status == 429 or e.status is None:
                    flash(str(e))
                    return render_template("auth/signup.html", email=email), 400
                # any other failure: say nothing that reveals whether the email already has an account
            return render_template("auth/check_email.html", email=email, purpose="confirm")
        return render_template("auth/signup.html", email="")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        nxt = request.args.get("next", "")
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            try:
                tokens = client.sign_in(email, request.form.get("password", ""))
                return after_login(tokens, request.form.get("next") or nxt)
            except SupabaseError as e:
                flash(str(e))
                return render_template("auth/login.html", email=email, next=nxt), 401
        return render_template("auth/login.html", email="", next=nxt)

    @app.route("/logout", methods=["POST"])
    def logout():
        if session.get("at"):
            client.sign_out(session["at"])
        session.clear()
        return redirect(url_for("home"))

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            if valid_email(email):
                try:
                    client.recover(email, redirect_to=callback_url())
                except SupabaseError:
                    pass                       # same answer whether or not the email has an account
            return render_template("auth/check_email.html", email=email, purpose="reset")
        return render_template("auth/forgot.html")

    @app.route("/auth/callback")
    def auth_callback():
        """Email links land here. The tokens arrive in the URL fragment (#...), which only the browser can read,
        so a tiny script posts them to /auth/session."""
        return render_template("auth/callback.html")

    @app.route("/auth/session", methods=["POST"])
    def auth_session():
        if request.form.get("error_code") or request.form.get("error"):
            flash("That link has expired or was already used. Please request a new one.")
            return redirect(url_for("login"))
        token, kind = request.form.get("access_token", ""), request.form.get("type", "")
        try:
            user = client.get_user(token)                 # proves the token is real before we trust it
        except SupabaseError:
            flash("That link isn't valid any more. Please request a new one.")
            return redirect(url_for("login"))
        tokens = {"access_token": token, "refresh_token": request.form.get("refresh_token", ""),
                  "expires_in": request.form.get("expires_in") or 3600, "user": user}
        if kind == "recovery":
            start_session(tokens)
            return redirect(url_for("change_password"))
        return after_login(tokens)

    @app.route("/account/password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            pw = request.form.get("password", "")
            problem = password_problem(pw, request.user["email"]) or (
                "The two passwords don't match." if pw != request.form.get("password2", "") else None)
            if problem:
                flash(problem)
                return render_template("auth/password.html"), 400
            try:
                client.set_password(request.user["token"], pw)
            except SupabaseError as e:
                flash(str(e))
                return render_template("auth/password.html"), 400
            flash("Your password has been changed.")
            return redirect(url_for("trips"))
        return render_template("auth/password.html")

    @app.route("/consent", methods=["GET", "POST"])
    @login_required
    def consent():
        if has_profile(request.user):
            return redirect(url_for("trips"))
        if request.method == "POST":
            if not (request.form.get("age18") and request.form.get("terms")):
                flash("Please tick both boxes to continue.")
                return render_template("auth/consent.html"), 400
            create_profile_from_metadata(request.user, {"accepted_terms_at": datetime.now(timezone.utc).isoformat()})
            return redirect(url_for("trips"))
        return render_template("auth/consent.html")

    # ---------------- trips ----------------
    def trip_id_or_404(value):
        try:
            return str(uuid.UUID(value))
        except ValueError:
            abort(404)

    @app.route("/trips", methods=["GET", "POST"])
    @login_required
    def trips():
        user = request.user
        if request.method == "POST":
            name = " ".join(request.form.get("name", "").split())
            if not 1 <= len(name) <= 80:
                flash("Give the trip a name (up to 80 characters).")
            else:
                client.insert("trips", user["token"], {"owner_id": user["id"], "name": name})
                flash(f"Trip “{name}” created.")
            return redirect(url_for("trips"))
        rows = client.select("trips", user["token"], {"select": "id,name,archived_at,created_at", "order": "created_at.desc"})
        return render_template("auth/trips.html", active=[r for r in rows if not r.get("archived_at")],
                               archived=[r for r in rows if r.get("archived_at")])

    @app.route("/trips/<tid>/archive", methods=["POST"])
    @login_required
    def archive_trip(tid):
        client.update("trips", request.user["token"], {"id": f"eq.{trip_id_or_404(tid)}"},
                      {"archived_at": datetime.now(timezone.utc).isoformat()})
        flash("Trip archived. It's hidden but kept, and you can restore it any time.")
        return redirect(url_for("trips"))

    @app.route("/trips/<tid>/restore", methods=["POST"])
    @login_required
    def restore_trip(tid):
        client.update("trips", request.user["token"], {"id": f"eq.{trip_id_or_404(tid)}"}, {"archived_at": None})
        flash("Trip restored.")
        return redirect(url_for("trips"))

    @app.route("/trips/<tid>/delete", methods=["POST"])
    @login_required
    def delete_trip(tid):
        if not request.form.get("sure"):
            flash("Tick the box to confirm. Deleting a trip can't be undone.")
            return redirect(url_for("trips"))
        # TODO (photos step): also delete this trip's files from storage. Rows cascade, files do not.
        client.delete("trips", request.user["token"], {"id": f"eq.{trip_id_or_404(tid)}"})
        flash("Trip deleted for good.")
        return redirect(url_for("trips"))

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
