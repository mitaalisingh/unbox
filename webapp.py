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
import search as searchlib
from images import BadImage, clean_photo
from sbclient import Supabase, SupabaseError

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


def create_app(sb=None, testing=False, embed_doc=None, embed_query=None):
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
        PERMANENT_SESSION_LIFETIME=14 * 24 * 3600, MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    )
    if os.environ.get("BEHIND_PROXY") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    client = sb or Supabase()
    app.sb = client
    make_doc_vector = embed_doc or searchlib.embed_doc          # tests pass simple stand-ins
    make_query_vector = embed_query or searchlib.embed_query

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

    def get_trip(tid, token):
        rows = client.select("trips", token, {"select": "id,name,archived_at", "id": f"eq.{tid}"})
        return rows[0] if rows else None

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
        return render_template("auth/trip.html", trip=trip, node=node, crumbs=crumbs_for(nodes, node) if node else [],
                               boxes=[n for n in kids if n["is_box"]], items=[n for n in kids if not n["is_box"]],
                               urls=urls, stats=stats, total_items=total_items, total_kg=total_kg)

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

    @app.route("/trips/<tid>/add", methods=["POST"])
    @login_required
    def add_node(tid):
        token, tid = request.user["token"], trip_id_or_404(tid)
        if not get_trip(tid, token):
            abort(404)
        parent_id = (request.form.get("parent_id") or "").strip() or None
        back = url_for("node_view", tid=tid, nid=parent_id) if parent_id else url_for("trip_view", tid=tid)
        if parent_id:
            parent = client.select("nodes", token, {"select": "id,is_box", "id": f"eq.{trip_id_or_404(parent_id)}", "trip_id": f"eq.{tid}"})
            if not parent or not parent[0]["is_box"]:
                flash("Items can only be added inside a box.")
                return redirect(url_for("trip_view", tid=tid))
        name = " ".join(request.form.get("name", "").split())
        tags = " ".join(request.form.get("tags", "").split())
        description = " ".join(request.form.get("description", "").split())
        weight, werr = parse_weight(request.form.get("weight_kg"))
        problem = ("Give it a name (up to 80 characters)." if not 1 <= len(name) <= 80 else
                   "Tags can be up to 200 characters." if len(tags) > 200 else
                   "The note can be up to 300 characters." if len(description) > 300 else werr)
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

    @app.route("/trips/<tid>/n/<nid>/delete", methods=["POST"])
    @login_required
    def delete_node(tid, nid):
        token, tid, nid = request.user["token"], trip_id_or_404(tid), trip_id_or_404(nid)
        if not get_trip(tid, token):
            abort(404)
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
