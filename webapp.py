"""Unbox, multi-user version: accounts and trips on top of Supabase.

Run (needs the values in .env, see .env.example):
    python -m dotenv run -- flask --app webapp run     # or: set the variables yourself and run  python webapp.py

How login works: the server calls Supabase Auth, keeps the user's tokens in a signed, HttpOnly cookie, and
sends the USER'S token with every database call. The database's row-level rules then decide what that user can
see, so a bug here cannot expose another person's trips.
"""
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import segno
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
import naming
import search as searchlib
from images import BadImage, clean_photo
from sbclient import Supabase, SupabaseError
from werkzeug.security import check_password_hash, generate_password_hash

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
BUCKET = "photos"


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


def create_app(sb=None, testing=False, embed_doc=None, embed_query=None, suggest=None):
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
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600, MAX_CONTENT_LENGTH=120 * 1024 * 1024,
    )
    if os.environ.get("BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    client = sb or Supabase()
    app.sb = client
    make_doc_vector = embed_doc or searchlib.embed_doc          # tests pass simple stand-ins
    make_query_vector = embed_query or searchlib.embed_query
    suggest_names = suggest or naming.suggest                    # tests pass a stand-in

    if not testing and embed_doc is None:               # load the search model in the background so the first "Add" isn't slow
        import threading
        threading.Thread(target=searchlib._get_model, daemon=True).start()

    def vector_text(vec):
        """pgvector accepts a vector written as text: [0.1,0.2,...]"""
        return "[" + ",".join(str(x) for x in vec) + "]"

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
                if not nxt:
                    return redirect(url_for("welcome"))          # first time here: a short introduction
            else:
                return redirect(url_for("consent"))
        return redirect(safe_next(nxt, url_for("trips")))

    # ---------------- public pages ----------------
    @app.route("/")
    def home():
        return redirect(url_for("trips")) if session.get("at") else render_template("auth/landing.html")

    @app.route("/manifest.webmanifest")
    def manifest():
        resp = send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    @app.route("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/welcome")
    @login_required
    def welcome():
        return render_template("auth/welcome.html")

    @app.route("/privacy")
    def privacy():
        return render_template("auth/privacy.html")

    # ---------------- sign-up / login / reset ----------------
    # Invite codes: when INVITE_CODES is set (comma-separated), only people holding one can sign up.
    # Unset means open sign-up, which is what you want on your own computer.
    def invite_codes():
        return [c.strip().lower() for c in os.environ.get("INVITE_CODES", "").split(",") if c.strip()]

    wrong_codes = {}                                  # ip -> times of wrong guesses (kept in memory only)

    def invite_ok(given):
        codes = invite_codes()
        if not codes:
            return True
        who = request.remote_addr or "?"
        now = time.time()
        recent = [t for t in wrong_codes.get(who, []) if now - t < 900]
        if len(recent) >= 10:
            return False
        given = " ".join((given or "").split()).lower()
        ok = any(secrets.compare_digest(given.encode(), c.encode()) for c in codes)
        wrong_codes[who] = recent if ok else recent + [now]
        return ok

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
            elif not invite_ok(request.form.get("invite")):
                problem = "That invite code isn't valid. Ask the person who invited you."
            if problem:
                flash(problem)
                return render_template("auth/signup.html", email=email, invite_required=bool(invite_codes())), 400
            meta = {"confirmed_18_plus": True, "accepted_terms_at": datetime.now(timezone.utc).isoformat()}
            try:
                client.sign_up(email, pw, meta, redirect_to=callback_url())
            except SupabaseError as e:
                if e.status == 429 or e.status is None:
                    flash(str(e))
                    return render_template("auth/signup.html", email=email), 400
                # any other failure: say nothing that reveals whether the email already has an account
            return render_template("auth/check_email.html", email=email, purpose="confirm")
        return render_template("auth/signup.html", email="", invite_required=bool(invite_codes()))

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

    @app.route("/account")
    @login_required
    def account():
        return render_template("auth/account.html")

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
        tid = trip_id_or_404(tid)
        token = request.user["token"]
        if not get_trip(tid, token):
            abort(404)
        try:                                         # files first: if this fails, nothing is deleted and nothing is left behind
            client.remove(BUCKET, client.list_folder(BUCKET, tid, token), token)
        except SupabaseError:
            flash("Couldn't delete the photos, so nothing was deleted. Please try again.")
            return redirect(url_for("trips"))
        client.delete("trips", token, {"id": f"eq.{tid}"})
        flash("Trip deleted for good, including its photos.")
        return redirect(url_for("trips"))


    # ---------------- boxes and items inside a trip ----------------
    NODE_COLUMNS = "id,trip_id,parent_id,is_box,name,description,tags,weight_kg,photo_path,position"

    TRIP_COLUMNS = ("id,name,archived_at,viewer_pw_set_at,locked_at,share_code_expires_at",
                    "id,name,archived_at,viewer_pw_set_at,locked_at",
                    "id,name,archived_at,viewer_pw_set_at")

    def get_trip(tid, token):
        for columns in TRIP_COLUMNS:
            try:
                rows = client.select("trips", token, {"select": columns, "id": f"eq.{tid}"})
                return rows[0] if rows else None
            except SupabaseError as e:
                if e.status != 400 or columns == TRIP_COLUMNS[-1]:     # 400 = the database doesn't have a newer column yet
                    raise

    def lock_guard(tid, token, as_json=False):
        """A locked trip can only be looked at. If this one is locked, return the response to send instead of changing anything."""
        trip = get_trip(tid, token)
        if not (trip and trip.get("locked_at")):
            return None
        if as_json:
            return jsonify(error="This trip is locked. Unlock it to make changes."), 409
        flash("This trip is locked. Unlock it to make changes.")
        return redirect(url_for("trip_view", tid=tid))

    def load_nodes(tid, token):
        rows = client.select("nodes", token, {"select": NODE_COLUMNS, "trip_id": f"eq.{tid}"})
        return sorted(rows, key=lambda n: (n.get("position") or 0, n["name"].lower()))

    def descendants(nodes, nid):
        """ids of nid and everything inside it, however deep."""
        found, stack = {nid}, [nid]
        while stack:
            cur = stack.pop()
            for n in nodes:
                if n.get("parent_id") == cur and n["id"] not in found:
                    found.add(n["id"])
                    stack.append(n["id"])
        return found

    def rollup(nodes, nid):
        """(number of items, total known weight in kg) inside box nid, including nested boxes."""
        ids = descendants(nodes, nid) - {nid}
        inside = [n for n in nodes if n["id"] in ids]
        return (sum(1 for n in inside if not n["is_box"]),
                round(sum(float(n["weight_kg"]) for n in inside if not n["is_box"] and n.get("weight_kg") is not None), 2))

    def crumbs_for(nodes, node):
        by_id, chain, cur = {n["id"]: n for n in nodes}, [], node
        while cur and cur.get("parent_id") in by_id:
            cur = by_id[cur["parent_id"]]
            chain.append(cur)
        return list(reversed(chain))

    def view_trip(tid, nid):
        token = request.user["token"]
        tid = trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)                                           # someone else's trip looks exactly like a missing one
        nodes = load_nodes(tid, token)
        node = None
        if nid:
            node = next((n for n in nodes if n["id"] == trip_id_or_404(nid)), None)
            if not node:
                abort(404)
        parent = node["id"] if node else None
        kids = [n for n in nodes if n.get("parent_id") == parent] if (not node or node["is_box"]) else []
        shown = kids + ([node] if node else [])
        urls = client.sign_many(BUCKET, [n.get("photo_path") for n in shown], token)
        stats = {n["id"]: rollup(nodes, n["id"]) for n in kids if n["is_box"]}
        if node is None:                                          # the trip's top level: everything in the trip
            known = [float(n["weight_kg"]) for n in nodes if not n["is_box"] and n.get("weight_kg") is not None]
            total_items, total_kg = sum(1 for n in nodes if not n["is_box"]), round(sum(known), 2)
        elif node["is_box"]:
            total_items, total_kg = rollup(nodes, node["id"])
        else:
            total_items, total_kg = 0, 0
        show_all = node is None and request.args.get("show") == "all"
        if show_all:                                              # every item in the trip, wherever it is kept
            items = [n for n in nodes if not n["is_box"]]
            urls = client.sign_many(BUCKET, [n.get("photo_path") for n in items], token)
        else:
            items = [n for n in kids if not n["is_box"]]
        return render_template("auth/trip.html", trip=trip, node=node, crumbs=crumbs_for(nodes, node) if node else [],
                               boxes=[] if show_all else [n for n in kids if n["is_box"]], items=items,
                               urls=urls, stats=stats, total_items=total_items, total_kg=total_kg,
                               locked=bool(trip.get("locked_at")), show_all=show_all)

    @app.route("/trips/<tid>")
    @login_required
    def trip_view(tid):
        return view_trip(tid, None)

    @app.route("/trips/<tid>/n/<nid>")
    @login_required
    def node_view(tid, nid):
        return view_trip(tid, nid)

    def parse_weight(raw):
        raw = (raw or "").strip().replace(",", ".")
        if not raw:
            return None, None
        try:
            kg = round(float(raw), 2)
        except ValueError:
            return None, "Weight must be a number, like 1.5."
        if not 0 <= kg <= 1000:
            return None, "Weight must be between 0 and 1000 kg."
        return kg, None

    def read_fields():
        """The text fields shared by 'add' and 'edit', cleaned and checked. Returns (name, tags, note, kg, problem)."""
        name = " ".join(request.form.get("name", "").split())
        tags = " ".join(request.form.get("tags", "").split())
        description = " ".join(request.form.get("description", "").split())
        weight, werr = parse_weight(request.form.get("weight_kg"))
        problem = ("Give it a name (up to 80 characters)." if not 1 <= len(name) <= 80 else
                   "Tags can be up to 200 characters." if len(tags) > 200 else
                   "The note can be up to 300 characters." if len(description) > 300 else werr)
        return name, tags, description, weight, problem

    @app.route("/trips/<tid>/add", methods=["POST"])
    @login_required
    def add_node(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        parent_id = (request.form.get("parent_id") or "").strip() or None
        back = url_for("node_view", tid=tid, nid=parent_id) if parent_id else url_for("trip_view", tid=tid)
        if request.form.get("again"):                                # typed in from the "add items" screen: stay there for the next one
            back = url_for("add_items", tid=tid, box=parent_id) if parent_id else url_for("add_items", tid=tid)
        if parent_id:
            parent = client.select("nodes", token, {"select": "id,is_box", "id": f"eq.{trip_id_or_404(parent_id)}", "trip_id": f"eq.{tid}"})
            if not parent or not parent[0]["is_box"]:
                flash("Items can only be added inside a box.")
                return redirect(url_for("trip_view", tid=tid))
        name, tags, description, weight, problem = read_fields()
        if problem:
            flash(problem)
            return redirect(back)
        photo = request.files.get("photo")
        image = None
        if photo and photo.filename:
            try:
                image = clean_photo(photo.read())                 # validated and stripped BEFORE anything is saved
            except BadImage as e:
                flash(str(e))
                return redirect(back)
        nid = str(uuid.uuid4())
        path = f"{tid}/{nid}.jpg" if image else None
        if image:
            try:
                client.upload(BUCKET, path, token, image)
            except SupabaseError as e:
                flash(str(e))
                return redirect(back)
        siblings = client.select("nodes", token, {"select": "id", "trip_id": f"eq.{tid}", "parent_id": f"eq.{parent_id}" if parent_id else "is.null"})
        try:
            client.insert("nodes", token, {"id": nid, "trip_id": tid, "parent_id": parent_id,
                                           "is_box": request.form.get("kind") == "box", "name": name, "tags": tags,
                                           "description": description, "weight_kg": weight, "photo_path": path,
                                           "position": len(siblings) + 1, **embedding_field(name, tags, description)})
        except SupabaseError:
            if path:                                              # don't leave an orphan photo behind
                try:
                    client.remove(BUCKET, [path], token)
                except SupabaseError:
                    pass
            flash("Couldn't save that. Please try again.")
            return redirect(back)
        flash(f"Added “{name}”.")
        return redirect(back)

    def embedding_field(name, tags, description):
        """{'embedding': '[...]'} when the model is available, else {} (search then uses words only)."""
        try:
            vec = make_doc_vector(f"{name} {tags} {description}")
        except Exception:                      # a model problem must never stop someone adding an item
            vec = None
        return {"embedding": vector_text(vec)} if vec else {}

    def backfill_embeddings(tid, token, limit=50):
        """Give any item that is missing its search numbers (added before the model was ready, or by an older
        version) its numbers now, so meaning search covers everything. Words-only search still works if this fails."""
        try:
            rows = client.select("nodes", token, {"select": "id,name,tags,description", "trip_id": f"eq.{tid}",
                                                  "embedding": "is.null", "limit": str(limit)})
            for r in rows:
                vec = make_doc_vector(f"{r['name']} {r['tags']} {r['description']}")
                if vec:
                    client.update("nodes", token, {"id": f"eq.{r['id']}"}, {"embedding": vector_text(vec)})
        except Exception:
            pass

    @app.route("/trips/<tid>/search")
    @login_required
    def trip_search(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        q = " ".join(request.args.get("q", "").split())[:100]
        results, urls = [], {}
        if q:
            backfill_embeddings(tid, token)
            nodes = load_nodes(tid, token)
            score = {}
            for n in nodes:                                            # 1) words, plurals and typos
                lex = searchlib.lexical_score(q, f"{n['name']} {n['tags']} {n['description']}")
                if lex > 0:
                    score[n["id"]] = 1.0 + lex
            try:                                                       # 2) meaning (needs the model)
                qv = make_query_vector(q)
            except Exception:
                qv = None
            if qv:
                rows = client.rpc("match_nodes", token, {"p_trip": tid, "p_query": vector_text(qv), "p_limit": 20}) or []
                if rows:
                    floor = max(searchlib.SEMANTIC_MIN, max(r["similarity"] for r in rows) - searchlib.SEMANTIC_BAND)
                    for r in rows:
                        if r["similarity"] >= floor:
                            score[r["id"]] = score.get(r["id"], 0) + r["similarity"]
            by_id = {n["id"]: n for n in nodes}
            ranked = sorted((i for i in score if i in by_id), key=lambda i: -score[i])[:30]
            results = [{"node": by_id[i], "path": [c["name"] for c in crumbs_for(nodes, by_id[i])]} for i in ranked]
            urls = client.sign_many(BUCKET, [r["node"].get("photo_path") for r in results], token)
        return render_template("auth/trip_search.html", trip=trip, q=q, results=results, urls=urls)

    @app.route("/trips/<tid>/n/<nid>/edit", methods=["GET", "POST"])
    @login_required
    def edit_node(tid, nid):
        token, tid, nid = request.user["token"], trip_id_or_404(tid), trip_id_or_404(nid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        nodes = load_nodes(tid, token)
        node = next((n for n in nodes if n["id"] == nid), None)
        if not node:
            abort(404)
        inside = descendants(nodes, nid)                      # a box can't be moved into itself or anything inside it
        choices = [("", "Top level of the trip")] + [
            (n["id"], " › ".join([c["name"] for c in crumbs_for(nodes, n)] + [n["name"]]))
            for n in nodes if n["is_box"] and n["id"] not in inside]
        edit_url = url_for("edit_node", tid=tid, nid=nid)
        if request.method == "POST":
            name, tags, description, weight, problem = read_fields()
            new_parent = (request.form.get("parent_id") or "").strip() or None
            if not problem and new_parent not in {c[0] or None for c in choices}:
                problem = "You can't move it there."
            if problem:
                flash(problem)
                return redirect(edit_url)
            image = None
            upload = request.files.get("photo")
            if upload and upload.filename:
                try:
                    image = clean_photo(upload.read())        # checked and stripped BEFORE anything changes
                except BadImage as e:
                    flash(str(e))
                    return redirect(edit_url)
            photo_path = node.get("photo_path")
            if request.form.get("remove_photo") and photo_path and not image:
                try:                                           # file first: if this fails nothing changes
                    client.remove(BUCKET, [photo_path], token)
                except SupabaseError:
                    flash("Couldn't remove the photo, so nothing was changed. Please try again.")
                    return redirect(edit_url)
                photo_path = None
            if image:
                path = f"{tid}/{nid}.jpg"                      # same place: the new photo replaces the old file
                try:
                    client.upload(BUCKET, path, token, image)
                except SupabaseError as e:
                    flash(str(e))
                    return redirect(edit_url)
                photo_path = path
            changes = {"name": name, "tags": tags, "description": description, "weight_kg": weight,
                       "photo_path": photo_path, "parent_id": new_parent,
                       "embedding": embedding_field(name, tags, description).get("embedding")}   # refreshed; cleared if no model
            if new_parent != node.get("parent_id"):
                changes["position"] = len([n for n in nodes if n.get("parent_id") == new_parent]) + 1
            try:
                client.update("nodes", token, {"id": f"eq.{nid}", "trip_id": f"eq.{tid}"}, changes)
            except SupabaseError:
                flash("Couldn't save the changes. Please try again.")
                return redirect(edit_url)
            flash("Saved.")
            return redirect(url_for("node_view", tid=tid, nid=nid))
        urls = client.sign_many(BUCKET, [node.get("photo_path")], token)
        return render_template("auth/edit_node.html", trip=get_trip(tid, token), node=node, crumbs=crumbs_for(nodes, node),
                               choices=choices, urls=urls)

    MAX_BATCH = 20

    def photo_to_item(token, tid, parent_id, raw, position):
        """One photo becomes one item named by the AI's best guess. Returns its details, or None if it couldn't be saved."""
        try:
            image = clean_photo(raw)
        except BadImage:
            return None
        try:
            guesses = suggest_names(image)
        except Exception:                                          # a model problem must never lose the photo
            guesses = []
        name = naming.best_name(guesses)
        nid = str(uuid.uuid4())
        path = f"{tid}/{nid}.jpg"
        try:
            client.upload(BUCKET, path, token, image)
            client.insert("nodes", token, {"id": nid, "trip_id": tid, "parent_id": parent_id, "is_box": False, "name": name,
                                           "tags": "", "description": "", "weight_kg": None, "photo_path": path,
                                           "position": position, **embedding_field(name, "", "")})
        except SupabaseError:
            try:
                client.remove(BUCKET, [path], token)
            except SupabaseError:
                pass
            return None
        return {"id": nid, "name": name, "path": path, "guesses": [g[0] for g in guesses],
                "check": not guesses or guesses[0][1] < naming.CHECK}

    @app.route("/trips/<tid>/capture-one", methods=["POST"])
    @login_required
    def capture_one(tid):
        """The page sends photos one at a time (so it can show progress and never runs into a time limit)."""
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token, as_json=True)
        if blocked:
            return blocked
        parent_id = (request.form.get("parent_id") or "").strip() or None
        if parent_id:
            parent = client.select("nodes", token, {"select": "id,is_box", "id": f"eq.{trip_id_or_404(parent_id)}", "trip_id": f"eq.{tid}"})
            if not parent or not parent[0]["is_box"]:
                return jsonify(error="Items can only be added inside a box."), 400
        photo = request.files.get("photo")
        siblings = client.select("nodes", token, {"select": "id", "trip_id": f"eq.{tid}", "parent_id": f"eq.{parent_id}" if parent_id else "is.null"})
        item = photo_to_item(token, tid, parent_id, photo.read(), len(siblings) + 1) if photo else None
        if not item:
            return jsonify(error="That photo couldn't be used."), 400
        return jsonify(id=item["id"], name=item["name"], guesses=item["guesses"], check=item["check"])

    @app.route("/trips/<tid>/review-show", methods=["POST"])
    @login_required
    def review_show(tid):
        """The review screen for photos the page just sent one by one. Names and photos come from the database;
        only the suggestion text comes from the page, and it is just shown as text."""
        import json
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        try:
            sent = json.loads(request.form.get("results", "[]"))[:MAX_BATCH]
            wanted = {trip_id_or_404(r["id"]): r for r in sent}
        except (ValueError, TypeError, KeyError, AttributeError):
            abort(400)
        rows = [n for n in load_nodes(tid, token) if n["id"] in wanted and not n["is_box"]]
        if not rows:
            return redirect(url_for("trip_view", tid=tid))
        urls = client.sign_many(BUCKET, [n.get("photo_path") for n in rows], token)
        made = [{"id": n["id"], "name": n["name"], "path": n.get("photo_path"),
                 "guesses": [str(g)[:80] for g in (wanted[n["id"]].get("guesses") or [])[:3]],
                 "check": bool(wanted[n["id"]].get("check"))} for n in rows]
        back = safe_next(request.form.get("back", ""), url_for("trip_view", tid=tid))
        return render_template("auth/review.html", trip=get_trip(tid, token), made=made, urls=urls, back=back,
                               unidentified=naming.UNKNOWN)

    @app.route("/trips/<tid>/capture", methods=["POST"])
    @login_required
    def capture(tid):
        """Several photos at once: each becomes an item named by the AI's best guess, then one review screen."""
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        parent_id = (request.form.get("parent_id") or "").strip() or None
        back = url_for("node_view", tid=tid, nid=parent_id) if parent_id else url_for("trip_view", tid=tid)
        if parent_id:
            parent = client.select("nodes", token, {"select": "id,is_box", "id": f"eq.{trip_id_or_404(parent_id)}", "trip_id": f"eq.{tid}"})
            if not parent or not parent[0]["is_box"]:
                flash("Items can only be added inside a box.")
                return redirect(url_for("trip_view", tid=tid))
        files = [f for f in request.files.getlist("photos") if f and f.filename]
        if not files:
            flash("Choose at least one photo.")
            return redirect(back)
        if len(files) > MAX_BATCH:
            flash(f"Up to {MAX_BATCH} photos at a time, please. Add the rest in another batch.")
            return redirect(back)
        position = len(client.select("nodes", token, {"select": "id", "trip_id": f"eq.{tid}", "parent_id": f"eq.{parent_id}" if parent_id else "is.null"}))
        made, skipped = [], 0
        for f in files:
            item = photo_to_item(token, tid, parent_id, f.read(), position + 1)
            if item:
                position += 1
                made.append(item)
            else:
                skipped += 1
        if skipped:
            flash(f"{skipped} photo{'s' if skipped != 1 else ''} couldn't be used and {'were' if skipped != 1 else 'was'} skipped.")
        if not made:
            return redirect(back)
        urls = client.sign_many(BUCKET, [m["path"] for m in made], token)
        return render_template("auth/review.html", trip=get_trip(tid, token), made=made, urls=urls, back=back,
                               unidentified=naming.UNKNOWN)

    @app.route("/trips/<tid>/review", methods=["POST"])
    @login_required
    def review(tid):
        """Save the names the person corrected on the review screen."""
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        changed, bad_weight = 0, 0
        for raw in request.form.getlist("id")[:MAX_BATCH]:
            nid = trip_id_or_404(raw)
            name = " ".join(request.form.get(f"name_{nid}", "").split())
            old = request.form.get(f"old_{nid}", "")
            changes = {}
            if 1 <= len(name) <= 80 and name != old:
                changes["name"] = name
                changes["embedding"] = embedding_field(name, "", "").get("embedding")
            raw_weight = request.form.get(f"weight_{nid}", "").strip()
            if raw_weight:
                kg, problem = parse_weight(raw_weight)
                if problem:
                    bad_weight += 1
                else:
                    changes["weight_kg"] = kg
            if changes:
                client.update("nodes", token, {"id": f"eq.{nid}", "trip_id": f"eq.{tid}"}, changes)
                changed += 1
        if bad_weight:
            flash("Some weights weren't numbers (like 1.5) and were left out.")
        flash(f"Saved {changed} change{'s' if changed != 1 else ''}." if changed else "All names kept.")
        back = request.form.get("back", "")
        return redirect(safe_next(back, url_for("trip_view", tid=tid)))

    # ---------------- owner: viewer password and printable QR labels ----------------
    @app.route("/trips/<tid>/lock", methods=["POST"])
    @login_required
    def lock_trip(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        locking = request.form.get("lock") == "1"
        try:
            client.update("trips", token, {"id": f"eq.{tid}"}, {"locked_at": datetime.now(timezone.utc).isoformat() if locking else None})
            flash("Locked. The trip is now view-only." if locking else "Unlocked. You can edit again.")
        except SupabaseError:
            flash("Couldn't change the lock. The database may need its latest update first.")
        return redirect(url_for("trip_view", tid=tid))

    # ---------------- trip codes: put a copy of a trip into someone else's account ----------------
    SHARE_DAYS, MAX_COPY_NODES = 7, 300
    SHARE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"                 # no 0/O or 1/I to mix up
    wrong_trip_codes = {}                                                # user id -> times of wrong guesses (memory only)

    def code_hash(code):
        clean = "".join(ch for ch in (code or "").upper() if ch.isalnum())
        return hmac.new(secret.encode(), clean.encode(), hashlib.sha256).hexdigest()

    @app.route("/trips/<tid>/share-code", methods=["POST"])
    @login_required
    def share_code(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        try:
            if request.form.get("off"):
                client.update("trips", token, {"id": f"eq.{tid}"}, {"share_code_hash": None, "share_code_expires_at": None})
                flash("The trip code is turned off.")
            else:
                raw = "".join(secrets.choice(SHARE_ALPHABET) for _ in range(10))
                code = raw[:5] + "-" + raw[5:]
                client.update("trips", token, {"id": f"eq.{tid}"}, {
                    "share_code_hash": code_hash(code),
                    "share_code_expires_at": (datetime.now(timezone.utc) + timedelta(days=SHARE_DAYS)).isoformat()})
                session["new_code"] = [tid, code]
        except SupabaseError:
            flash("Couldn't do that. The database may need its latest update first.")
        return redirect(url_for("trip_settings", tid=tid, section="copy"))

    @app.route("/trips/import", methods=["POST"])
    @login_required
    def import_trip():
        user = request.user
        token = user["token"]
        now = time.time()
        recent = [t for t in wrong_trip_codes.get(user["id"], []) if now - t < 900]
        if len(recent) >= 10:
            flash("Too many wrong codes. Please wait 15 minutes.")
            return redirect(url_for("trips"))
        code = request.form.get("code", "")
        source = None
        if "".join(ch for ch in code if ch.isalnum()):
            try:
                rows = client.admin_select("trips", {"select": "id,name", "share_code_hash": f"eq.{code_hash(code)}",
                                                     "share_code_expires_at": f"gte.{datetime.now(timezone.utc).isoformat()}"})
            except SupabaseError:
                flash("Trip codes aren't available right now. Please try again later.")
                return redirect(url_for("trips"))
            source = rows[0] if rows else None
        if not source:
            wrong_trip_codes[user["id"]] = recent + [now]
            flash("That code isn't right, or it has expired.")
            return redirect(url_for("trips"))
        try:
            nodes = client.admin_select("nodes", {"select": "id,parent_id,is_box,name,description,tags,weight_kg,photo_path,position,embedding",
                                                  "trip_id": f"eq.{source['id']}"})
        except SupabaseError:
            flash("Couldn't read that trip. Please try again.")
            return redirect(url_for("trips"))
        if len(nodes) > MAX_COPY_NODES:
            flash(f"That trip is too big to copy ({len(nodes)} items; the limit is {MAX_COPY_NODES}).")
            return redirect(url_for("trips"))
        try:
            mine = client.insert("trips", token, {"owner_id": user["id"], "name": source["name"]})
        except SupabaseError:
            flash("Couldn't create the copy. Please try again.")
            return redirect(url_for("trips"))
        new_tid, copied = mine["id"], []
        try:
            fresh = {n["id"]: str(uuid.uuid4()) for n in nodes}
            ordered, placed = [], set()
            while len(ordered) < len(nodes):                              # parents before the things inside them
                before = len(ordered)
                for n in nodes:
                    if n["id"] not in placed and (not n.get("parent_id") or n["parent_id"] in placed or n["parent_id"] not in fresh):
                        ordered.append(n); placed.add(n["id"])
                if len(ordered) == before:
                    break
            rows = []
            for n in ordered:
                path = None
                if n.get("photo_path"):
                    target = f"{new_tid}/{fresh[n['id']]}.jpg"
                    try:
                        client.admin_copy(BUCKET, n["photo_path"], target)
                        path = target
                        copied.append(target)
                    except SupabaseError:
                        path = None                                       # the item is copied without its photo
                row = {"id": fresh[n["id"]], "trip_id": new_tid, "parent_id": fresh.get(n.get("parent_id")), "is_box": n["is_box"],
                       "name": n["name"], "description": n.get("description") or "", "tags": n.get("tags") or "",
                       "weight_kg": n.get("weight_kg"), "photo_path": path, "position": n.get("position") or 0}
                if n.get("embedding"):
                    row["embedding"] = n["embedding"]
                rows.append(row)
            client.insert_many("nodes", token, rows)
        except SupabaseError:
            try:                                                          # leave nothing half-copied behind
                client.remove(BUCKET, copied, token)
                client.delete("trips", token, {"id": f"eq.{new_tid}"})
            except SupabaseError:
                pass
            flash("Couldn't finish copying the trip. Nothing was added. Please try again.")
            return redirect(url_for("trips"))
        flash(f"Copied “{source['name']}” into your account ({len([n for n in nodes if not n['is_box']])} items).")
        return redirect(url_for("trip_view", tid=new_tid))

    @app.route("/trips/<tid>/settings")
    @login_required
    def trip_settings(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        section = request.args.get("section")
        new_code = None
        pending = session.get("new_code")
        if pending and pending[0] == tid and section == "copy":
            new_code = pending[1]
            session.pop("new_code", None)                                  # shown once; only a scrambled version is kept
        return render_template("auth/trip_settings.html", trip=trip, section=section if section in ("rename", "copy") else "share",
                               new_code=new_code)

    @app.route("/trips/<tid>/viewer-password", methods=["POST"])
    @login_required
    def set_viewer_password(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        pw = request.form.get("password", "")
        if request.form.get("remove"):
            client.update("trips", token, {"id": f"eq.{tid}"}, {"viewer_pw_hash": None, "viewer_pw_set_at": None})
            flash("Sharing is off. Scanning a label now shows nothing.")
        elif not 6 <= len(pw) <= 100:
            flash("The viewer password needs to be 6 to 100 characters.")
        else:
            client.update("trips", token, {"id": f"eq.{tid}"}, {"viewer_pw_hash": generate_password_hash(pw),
                                                               "viewer_pw_set_at": datetime.now(timezone.utc).isoformat()})
            flash("Viewer password saved. Anyone who scanned with the old one needs the new one.")
        return redirect(url_for("trip_settings", tid=tid, section="share"))

    @app.route("/trips/<tid>/labels")
    @login_required
    def labels(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        nodes = load_nodes(tid, token)
        everything = request.args.get("all") == "1"
        boxes = [n for n in nodes if n["is_box"] and (everything or not n.get("parent_id"))]
        base = request.url_root.rstrip("/")
        cards = []
        for b in boxes:
            link = base + url_for("viewer_view", tid=tid, nid=b["id"])
            cards.append({"node": b, "count": rollup(nodes, b["id"])[0],
                          "svg": segno.make(link, error="m").svg_inline(scale=8, border=4, light="#fff")})
        local = request.host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]")
        return render_template("auth/labels.html", trip=trip, cards=cards, everything=everything, local=local,
                               shared=bool(trip.get("viewer_pw_set_at")), base=base)

    # ---------------- viewers: no account, just the trip's viewer password ----------------
    VIEWER_DAYS = 7
    DUMMY_HASH = generate_password_hash("not a real password")      # so a missing trip takes as long as a wrong password
    VIEWER_NODE_COLUMNS = "id,parent_id,is_box,name,description,tags,weight_kg,photo_path,position"

    def viewer_trip(tid):
        rows = client.admin_select("trips", {"select": "id,name,viewer_pw_hash,viewer_pw_set_at", "id": f"eq.{tid}"})
        return rows[0] if rows else None

    def viewer_allowed(trip):
        """A viewer pass lasts a week, and stops working the moment the owner changes or removes the password."""
        grant = (session.get("viewer") or {}).get(trip["id"]) if trip else None
        return bool(grant and trip.get("viewer_pw_hash") and grant[1] == str(trip.get("viewer_pw_set_at"))
                    and time.time() - grant[0] < VIEWER_DAYS * 86400)

    def ask_for_password(tid, nid, message=None, status=401):
        if message:
            flash(message)
        return render_template("viewer/password.html", tid=tid, nid=nid), status

    @app.route("/v/<tid>")
    @app.route("/v/<tid>/<nid>")
    def viewer_view(tid, nid=None):
        tid = trip_id_or_404(tid)
        nid = trip_id_or_404(nid) if nid else None
        try:
            trip = viewer_trip(tid)
        except SupabaseError:
            return "This page is not available right now. Please try again in a minute.", 503
        if not viewer_allowed(trip):
            return ask_for_password(tid, nid)                       # looks the same whether or not the trip exists
        nodes = sorted(client.admin_select("nodes", {"select": VIEWER_NODE_COLUMNS, "trip_id": f"eq.{tid}"}),
                       key=lambda n: (n.get("position") or 0, n["name"].lower()))
        node = None
        if nid:
            node = next((n for n in nodes if n["id"] == nid), None)
            if not node:
                abort(404)
        parent = node["id"] if node else None
        kids = [n for n in nodes if n.get("parent_id") == parent] if (not node or node["is_box"]) else []
        urls = client.admin_sign_many(BUCKET, [n.get("photo_path") for n in kids + ([node] if node else [])])
        stats = {n["id"]: rollup(nodes, n["id"]) for n in kids if n["is_box"]}
        total_items, total_kg = (rollup(nodes, node["id"]) if node and node["is_box"] else
                                 (sum(1 for n in nodes if not n["is_box"]), 0) if not node else (0, 0))
        return render_template("viewer/box.html", trip=trip, node=node, crumbs=crumbs_for(nodes, node) if node else [],
                               boxes=[n for n in kids if n["is_box"]], items=[n for n in kids if not n["is_box"]],
                               urls=urls, stats=stats, total_items=total_items, total_kg=total_kg)

    @app.route("/v/<tid>/unlock", methods=["POST"])
    def viewer_unlock(tid):
        tid = trip_id_or_404(tid)
        raw_nid = (request.form.get("nid") or "").strip()
        nid = trip_id_or_404(raw_nid) if raw_nid else None
        key = hashlib.sha256(f"{secret}|{request.remote_addr}".encode()).hexdigest()[:32]
        try:
            since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
            tries = client.admin_select("viewer_attempts", {"select": "id", "trip_id": f"eq.{tid}", "client_key": f"eq.{key}",
                                                            "attempted_at": f"gte.{since}"})
            if len(tries) >= 8:
                return ask_for_password(tid, nid, "Too many wrong tries. Please wait 15 minutes.", 429)
            trip = viewer_trip(tid)
        except SupabaseError:
            return "This page is not available right now. Please try again in a minute.", 503
        stored = (trip or {}).get("viewer_pw_hash")
        ok = check_password_hash(stored or DUMMY_HASH, request.form.get("password", "")) and bool(stored)
        if not ok:
            if trip:
                try:
                    client.admin_insert("viewer_attempts", {"trip_id": tid, "client_key": key})
                except SupabaseError:
                    pass
            return ask_for_password(tid, nid, "That password isn't right.")
        grants = dict(session.get("viewer") or {})
        grants[tid] = [time.time(), str(trip.get("viewer_pw_set_at"))]
        session["viewer"] = dict(list(grants.items())[-10:])
        session.permanent = True
        return redirect(url_for("viewer_view", tid=tid, nid=nid) if nid else url_for("viewer_view", tid=tid))

    # ---------------- "Add your items": choose where, then photos or typing ----------------
    def target_box(tid, token, nodes=None):
        """The box named by ?box=..., or None for the trip itself. 404 if it isn't a box in this trip."""
        raw = (request.values.get("box") or "").strip()
        if not raw:
            return None
        box = next((n for n in (nodes or load_nodes(tid, token)) if n["id"] == trip_id_or_404(raw) and n["is_box"]), None)
        if not box:
            abort(404)
        return box

    @app.route("/trips/<tid>/add")
    @login_required
    def add_choose(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        return render_template("auth/add_choose.html", trip=trip, box=target_box(tid, token))

    @app.route("/trips/<tid>/add/storage", methods=["GET", "POST"])
    @login_required
    def add_storage(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        nodes = load_nodes(tid, token)
        parent = target_box(tid, token, nodes)
        if request.method == "POST":
            name = " ".join(request.form.get("name", "").split())
            if not 1 <= len(name) <= 80:
                flash("Give it a name (up to 80 characters).")
            else:
                parent_id = parent["id"] if parent else None
                position = len([n for n in nodes if n.get("parent_id") == parent_id]) + 1
                row = client.insert("nodes", token, {"trip_id": tid, "parent_id": parent_id, "is_box": True, "name": name, "tags": "",
                                                     "description": "", "weight_kg": None, "photo_path": None, "position": position,
                                                     **embedding_field(name, "", "")})
                if request.form.get("next") == "items":
                    flash(f"Created “{name}”. Now add what's inside it.")
                    return redirect(url_for("add_items", tid=tid, box=row["id"]))
                flash(f"Created “{name}”. Drag items onto it, or open it to add some.")
                return redirect(url_for("node_view", tid=tid, nid=parent_id) if parent_id else url_for("trip_view", tid=tid))
        return render_template("auth/add_storage.html", trip=trip, box=parent)

    @app.route("/trips/<tid>/add/items")
    @login_required
    def add_items(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        trip = get_trip(tid, token)
        if not trip:
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        box = target_box(tid, token)
        done = url_for("node_view", tid=tid, nid=box["id"]) if box else url_for("trip_view", tid=tid)
        return render_template("auth/add_items.html", trip=trip, box=box, done=done)

    @app.route("/trips/<tid>/n/<nid>/quick", methods=["POST"])
    @login_required
    def quick_edit(tid, nid):
        """Add or change just the weight and the note of one item (used from the swipe view)."""
        token, tid, nid = request.user["token"], trip_id_or_404(tid), trip_id_or_404(nid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token, as_json=True)
        if blocked:
            return blocked
        node = next((n for n in load_nodes(tid, token) if n["id"] == nid and not n["is_box"]), None)
        if not node:
            abort(404)
        kg, problem = parse_weight(request.form.get("weight_kg"))
        note = " ".join(request.form.get("description", "").split())
        if not problem and len(note) > 300:
            problem = "The note can be up to 300 characters."
        if problem:
            return jsonify(error=problem), 400
        client.update("nodes", token, {"id": f"eq.{nid}", "trip_id": f"eq.{tid}"},
                      {"weight_kg": kg, "description": note,
                       "embedding": embedding_field(node["name"], node.get("tags", ""), note).get("embedding")})
        return jsonify(ok=True, weight_kg=kg, description=note)

    @app.route("/trips/<tid>/n/<nid>/move", methods=["POST"])
    @login_required
    def move_item(tid, nid):
        """Move one item into a box (used when someone drags it onto a box)."""
        token, tid, nid = request.user["token"], trip_id_or_404(tid), trip_id_or_404(nid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token, as_json=True)
        if blocked:
            return blocked
        nodes = load_nodes(tid, token)
        item = next((n for n in nodes if n["id"] == nid and not n["is_box"]), None)
        box = next((n for n in nodes if n["id"] == trip_id_or_404(request.form.get("parent_id", "")) and n["is_box"]), None)
        if not item or not box:
            return jsonify(error="That can't be moved there."), 400
        client.update("nodes", token, {"id": f"eq.{nid}", "trip_id": f"eq.{tid}"},
                      {"parent_id": box["id"], "position": len([n for n in nodes if n.get("parent_id") == box["id"]]) + 1})
        return jsonify(ok=True, item=item["name"], box=box["name"])

    @app.route("/trips/<tid>/rename", methods=["POST"])
    @login_required
    def rename_trip(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        name = " ".join(request.form.get("name", "").split())
        if not 1 <= len(name) <= 80:
            flash("Give the trip a name (up to 80 characters).")
        else:
            client.update("trips", token, {"id": f"eq.{tid}"}, {"name": name})
            flash("Trip renamed.")
        return redirect(url_for("trip_settings", tid=tid, section="rename"))

    @app.route("/trips/<tid>/n/<nid>/delete", methods=["POST"])
    @login_required
    def delete_node(tid, nid):
        token, tid, nid = request.user["token"], trip_id_or_404(tid), trip_id_or_404(nid)
        if not get_trip(tid, token):
            abort(404)
        blocked = lock_guard(tid, token)
        if blocked:
            return blocked
        nodes = load_nodes(tid, token)
        node = next((n for n in nodes if n["id"] == nid), None)
        if not node:
            abort(404)
        gone = descendants(nodes, nid)
        paths = [n["photo_path"] for n in nodes if n["id"] in gone and n.get("photo_path")]
        try:                                                      # files first; if it fails nothing is deleted
            client.remove(BUCKET, paths, token)
        except SupabaseError:
            flash("Couldn't delete the photos, so nothing was deleted. Please try again.")
            return redirect(url_for("node_view", tid=tid, nid=nid))
        client.delete("nodes", token, {"id": f"eq.{nid}", "trip_id": f"eq.{tid}"})
        flash(f"Deleted “{node['name']}”" + (f" and {len(gone) - 1} thing(s) inside it." if len(gone) > 1 else "."))
        parent = node.get("parent_id")
        return redirect(url_for("node_view", tid=tid, nid=parent) if parent else url_for("trip_view", tid=tid))

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
