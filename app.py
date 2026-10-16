import hmac
import re
import os
import secrets
import sqlite3
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

import segno
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from PIL import Image, ImageOps

import search as searchlib

try:  # iPhone photos (HEIC)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

BASE = Path(__file__).parent
EDIT_MODE = os.environ.get("EDIT_MODE") == "1"


def load_encrypted_bundle():
    """Live site: unlock deploy_data/bundle.enc with DATA_KEY into a temp folder (nothing readable is stored in git)."""
    bundle = BASE / "deploy_data" / "bundle.enc"
    if EDIT_MODE or not bundle.exists():
        return None
    import io
    import tempfile
    import zipfile
    from cryptography.fernet import Fernet, InvalidToken
    phrase = os.environ.get("DATA_PASSPHRASE", "")
    if phrase.strip():
        from bundlekey import derive_key
        key = derive_key(phrase).decode()
    else:
        key = "".join(os.environ.get("DATA_KEY", "").split()).strip("'\"")
    if not key:
        raise RuntimeError("deploy_data/bundle.enc exists but the DATA_PASSPHRASE setting is missing or empty.")
    try:
        fernet = Fernet(key.encode())
    except ValueError:
        raise RuntimeError(f"DATA_KEY is not a valid key (it has {len(key)} characters, a valid one has 44). "
                           "Use the DATA_PASSPHRASE setting instead.") from None
    try:
        raw = fernet.decrypt(bundle.read_bytes())
    except InvalidToken:
        raise RuntimeError("Wrong passphrase (or key) for deploy_data/bundle.enc. Check DATA_PASSPHRASE matches the "
                           "one you typed when you ran: python deploy_tools.py pack") from None
    dest = Path(tempfile.mkdtemp(prefix="unbox_"))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(dest)
    return dest


DATA = load_encrypted_bundle() or Path(os.environ.get("DATA_DIR", BASE / "data"))
PHOTOS = DATA / "photos"
DB_PATH = DATA / "inventory.db"
PHOTOS.mkdir(parents=True, exist_ok=True)

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
DEFAULT_TOP_BOXES = ["Big Box 1", "Big Box 2", "Medium Box"]

app = Flask(__name__)
if os.environ.get("BEHIND_PROXY") == "1":   # on the host, TLS is handled by their proxy in front of us
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not (EDIT_MODE or os.environ.get("DEV_HTTP") == "1"),  # DEV_HTTP=1 only for testing on home Wi-Fi
    MAX_CONTENT_LENGTH=(1024 if EDIT_MODE else 1) * 1024 * 1024,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES nodes(id) ON DELETE CASCADE,
    is_box INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    weight_kg REAL,
    photo TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    emb BLOB
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0:
        for i, name in enumerate(DEFAULT_TOP_BOXES):
            conn.execute("INSERT INTO nodes (parent_id, is_box, name, position) VALUES (NULL, 1, ?, ?)", (name, i))
        conn.commit()
    conn.close()


init_db()


# ---------- auth ----------

def password_ok(candidate):
    return bool(APP_PASSWORD) and hmac.compare_digest(candidate.encode(), APP_PASSWORD.encode())


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if EDIT_MODE or session.get("ok"):
            return view(*a, **kw)
        return redirect(url_for("login", next=request.full_path.rstrip("?")))
    return wrapped


def edit_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not EDIT_MODE:
            abort(404)
        return view(*a, **kw)
    return wrapped


_attempts = {}


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD and not EDIT_MODE:
        return "Server is missing APP_PASSWORD.", 500
    error = None
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        fails = [t for t in _attempts.get(ip, []) if time.time() - t < 300]
        if len(fails) >= 8:
            error = "Too many tries. Wait a few minutes."
        elif password_ok(request.form.get("password", "")):
            session.permanent = True
            session["ok"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        else:
            fails.append(time.time())
            error = "Wrong password."
        _attempts[ip] = fails
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ---------- helpers ----------

def get_node(node_id):
    row = db().execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def ancestors(node):
    chain = []
    cur = node
    while cur["parent_id"] is not None:
        cur = get_node(cur["parent_id"])
        chain.append(cur)
    return list(reversed(chain))


def children(node_id):
    return db().execute(
        "SELECT * FROM nodes WHERE parent_id = ? ORDER BY is_box DESC, position, name", (node_id,)
    ).fetchall()


def subtree_stats(node_id):
    row = db().execute(
        """WITH RECURSIVE t(id, is_box, weight_kg) AS (
               SELECT id, is_box, weight_kg FROM nodes WHERE parent_id = ?
               UNION ALL
               SELECT n.id, n.is_box, n.weight_kg FROM nodes n JOIN t ON n.parent_id = t.id)
           SELECT SUM(is_box = 0) AS items, SUM(is_box = 1) AS boxes FROM t""", (node_id,)
    ).fetchone()
    return {"items": row["items"] or 0, "boxes": row["boxes"] or 0}


def preview_photos(node_id, n=3):
    """A few photos from anywhere inside a box, for the stacked thumbnails on the home page."""
    rows = db().execute(
        """WITH RECURSIVE t(id, photo, is_box, position) AS (
               SELECT id, photo, is_box, position FROM nodes WHERE parent_id = ?
               UNION ALL
               SELECT n.id, n.photo, n.is_box, n.position FROM nodes n JOIN t ON n.parent_id = t.id)
           SELECT photo FROM t WHERE photo IS NOT NULL AND is_box = 0 ORDER BY position LIMIT ?""", (node_id, n)).fetchall()
    return [r["photo"] for r in rows]


def path_names(node):
    return [a["name"] for a in ancestors(node)] + [node["name"]]


def save_photo(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    try:
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        flash("That photo couldn't be read. Try a JPG or PNG.")
        return None
    img.thumbnail((1400, 1400))
    name = secrets.token_hex(8) + ".jpg"
    img.save(PHOTOS / name, "JPEG", quality=82, optimize=True)
    return name


def rotate_photo(filename, direction):
    """Rotate a stored photo and point every node that uses it at the new file."""
    angle = {"cw": -90, "ccw": 90, "180": 180}[direction]
    img = Image.open(PHOTOS / filename).rotate(angle, expand=True)
    new = secrets.token_hex(8) + ".jpg"
    img.save(PHOTOS / new, "JPEG", quality=82, optimize=True)
    db().execute("UPDATE nodes SET photo = ? WHERE photo = ?", (new, filename))
    db().commit()
    (PHOTOS / filename).unlink(missing_ok=True)
    return new


def delete_photo(name):
    if name:
        (PHOTOS / name).unlink(missing_ok=True)


def search_text(row):
    return " ".join(x for x in (row["name"], row["tags"], row["description"]) if x)


def reindex(node_id):
    row = get_node(node_id)
    vec = searchlib.embed_one(search_text(row))
    db().execute("UPDATE nodes SET emb = ? WHERE id = ?", (vec, node_id))
    db().commit()


def parse_weight(raw):
    try:
        return float(raw) if raw and raw.strip() else None
    except ValueError:
        return None


def box_choices(exclude_id=None):
    """All boxes with their full path, for the 'move to' dropdown."""
    rows = db().execute("SELECT * FROM nodes WHERE is_box = 1 ORDER BY name").fetchall()
    by_id = {r["id"]: r for r in rows}

    def label(r):
        parts = [r["name"]]
        while r["parent_id"] in by_id:
            r = by_id[r["parent_id"]]
            parts.append(r["name"])
        return " › ".join(reversed(parts))

    banned = set()
    if exclude_id is not None:
        banned.add(exclude_id)
        changed = True
        while changed:
            changed = False
            for r in rows:
                if r["parent_id"] in banned and r["id"] not in banned:
                    banned.add(r["id"])
                    changed = True
    return sorted(((r["id"], label(r)) for r in rows if r["id"] not in banned), key=lambda x: x[1])


@app.context_processor
def inject():
    return {"edit_mode": EDIT_MODE, "subtree_stats": subtree_stats, "preview_photos": preview_photos}


# ---------- browse ----------

@app.route("/")
@login_required
def home():
    tops = db().execute("SELECT * FROM nodes WHERE parent_id IS NULL ORDER BY position, id").fetchall()
    return render_template("home.html", boxes=tops)


@app.route("/box/<int:node_id>")
@login_required
def box(node_id):
    node = get_node(node_id)
    if not node["is_box"]:
        return redirect(url_for("item", node_id=node_id))
    return render_template("box.html", node=node, crumbs=ancestors(node), kids=children(node_id),
                           choices=box_choices() if EDIT_MODE else None)


@app.route("/item/<int:node_id>")
@login_required
def item(node_id):
    node = get_node(node_id)
    if node["is_box"]:
        return redirect(url_for("box", node_id=node_id))
    sibs = [r["id"] for r in children(node["parent_id"]) if not r["is_box"]]
    i = sibs.index(node_id)
    return render_template("item.html", node=node, crumbs=ancestors(node), pos=i + 1, total=len(sibs),
                           prev_id=sibs[i - 1] if i > 0 else None,
                           next_id=sibs[i + 1] if i < len(sibs) - 1 else None)


@app.route("/photos/<path:filename>")
@login_required
def photo(filename):
    resp = send_from_directory(PHOTOS, filename)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    results = []
    if q:
        rows = db().execute("SELECT * FROM nodes WHERE parent_id IS NOT NULL").fetchall()
        for r, score in searchlib.rank(q, rows, search_text)[:40]:
            results.append({"node": r, "path": path_names(r)[:-1], "score": score})
    return render_template("search.html", q=q, results=results)


# ---------- editing (local only) ----------

@app.route("/box/<int:parent_id>/add", methods=["POST"])
@edit_required
def add(parent_id):
    parent = get_node(parent_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Give it a name.")
        return redirect(url_for("box", node_id=parent["id"]))
    is_box = 1 if request.form.get("kind") == "box" else 0
    pos = db().execute("SELECT COALESCE(MAX(position), 0) + 1 FROM nodes WHERE parent_id = ?", (parent_id,)).fetchone()[0]
    cur = db().execute(
        "INSERT INTO nodes (parent_id, is_box, name, description, tags, weight_kg, photo, position) VALUES (?,?,?,?,?,?,?,?)",
        (parent_id, is_box, name, request.form.get("description", "").strip(), request.form.get("tags", "").strip(),
         parse_weight(request.form.get("weight_kg")), save_photo(request.files.get("photo")), pos))
    db().commit()
    reindex(cur.lastrowid)
    return redirect(url_for("box", node_id=parent_id))


@app.route("/box/<int:parent_id>/bulk", methods=["POST"])
@edit_required
def bulk_add(parent_id):
    """Add many items at once: one per line ("name: tag, tag"), and/or one item per photo."""
    get_node(parent_id)
    entries = []
    for line in request.form.get("lines", "").splitlines():
        name, _, tags = line.partition(":")
        if name.strip():
            entries.append((name.strip(), tags.strip(), None))
    for f in request.files.getlist("photos"):
        if f and f.filename:
            stem = Path(f.filename).stem.replace("_", " ").replace("-", " ").strip()
            entries.append((stem or "Item", "", f))
    pos = db().execute("SELECT COALESCE(MAX(position), 0) FROM nodes WHERE parent_id = ?", (parent_id,)).fetchone()[0]
    added = 0
    for name, tags, f in entries:
        photo_name = save_photo(f) if f else None
        if f and not photo_name:
            continue
        pos += 1
        cur = db().execute(
            "INSERT INTO nodes (parent_id, is_box, name, tags, photo, position) VALUES (?, 0, ?, ?, ?, ?)",
            (parent_id, name, tags, photo_name, pos))
        db().commit()
        reindex(cur.lastrowid)
        added += 1
    flash(f"Added {added} item{'' if added == 1 else 's'}." if added else "Nothing to add.")
    return redirect(url_for("box", node_id=parent_id))


@app.route("/node/<int:node_id>/edit", methods=["GET", "POST"])
@edit_required
def edit(node_id):
    node = get_node(node_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip() or node["name"]
        photo_name = node["photo"]
        new_photo = save_photo(request.files.get("photo"))
        if new_photo:
            delete_photo(photo_name)
            photo_name = new_photo
        elif request.form.get("remove_photo"):
            delete_photo(photo_name)
            photo_name = None
        new_parent = request.form.get("parent_id", type=int) or node["parent_id"]
        if node["parent_id"] is not None and new_parent != node["parent_id"]:
            if new_parent not in {c[0] for c in box_choices(exclude_id=node_id)}:
                new_parent = node["parent_id"]
        db().execute(
            "UPDATE nodes SET name=?, description=?, tags=?, weight_kg=?, photo=?, parent_id=? WHERE id=?",
            (name, request.form.get("description", "").strip(), request.form.get("tags", "").strip(),
             parse_weight(request.form.get("weight_kg")), photo_name, new_parent, node_id))
        db().commit()
        reindex(node_id)
        return redirect(url_for("box", node_id=new_parent) if new_parent else url_for("home"))
    return render_template("edit.html", node=node, crumbs=ancestors(node),
                           choices=box_choices(exclude_id=node_id) if node["parent_id"] is not None else [])


@app.route("/node/<int:node_id>/rotate", methods=["POST"])
@edit_required
def rotate(node_id):
    node = get_node(node_id)
    direction = request.form.get("dir")
    if node["photo"] and direction in ("cw", "ccw", "180"):
        rotate_photo(node["photo"], direction)
    return redirect(url_for("edit", node_id=node_id))


@app.route("/node/<int:node_id>/crop", methods=["POST"])
@edit_required
def crop(node_id):
    """Crop this node's photo only (other items sharing the same photo keep the original)."""
    node = get_node(node_id)
    old = node["photo"]
    if old:
        try:
            x, y, w, h = (int(float(request.form[k])) for k in ("x", "y", "w", "h"))
            img = Image.open(PHOTOS / old)
            x, y = max(0, x), max(0, y)
            w, h = min(w, img.width - x), min(h, img.height - y)
            if w < 20 or h < 20:
                raise ValueError("crop too small")
            new = secrets.token_hex(8) + ".jpg"
            img.convert("RGB").crop((x, y, x + w, y + h)).save(PHOTOS / new, "JPEG", quality=85, optimize=True)
        except (KeyError, ValueError, OSError):
            flash("That crop didn't work. Try again.")
            return redirect(url_for("edit", node_id=node_id))
        db().execute("UPDATE nodes SET photo = ? WHERE id = ?", (new, node_id))
        db().commit()
        if not db().execute("SELECT 1 FROM nodes WHERE photo = ?", (old,)).fetchone():
            delete_photo(old)
    return redirect(url_for("edit", node_id=node_id))


@app.route("/node/<int:node_id>/delete", methods=["POST"])
@edit_required
def delete(node_id):
    node = get_node(node_id)
    if node["parent_id"] is None:
        flash("The three big boxes can't be deleted. Rename them instead.")
        return redirect(url_for("edit", node_id=node_id))
    photos = [r["photo"] for r in db().execute(
        """WITH RECURSIVE t(id, photo) AS (
               SELECT id, photo FROM nodes WHERE id = ?
               UNION ALL SELECT n.id, n.photo FROM nodes n JOIN t ON n.parent_id = t.id)
           SELECT photo FROM t""", (node_id,))]
    db().execute("DELETE FROM nodes WHERE id = ?", (node_id,))
    db().commit()
    for p in photos:
        delete_photo(p)
    return redirect(url_for("box", node_id=node["parent_id"]))


@app.route("/labels")
@edit_required
def labels():
    base = PUBLIC_URL or request.url_root.rstrip("/")
    which = request.args.get("which")          # "small" = small boxes only; "all" (or all=1) = everything; default = big boxes
    if request.args.get("all") == "1":
        which = "all"
    only = which not in ("all", "small")
    where = {"small": " AND parent_id IS NOT NULL", "all": ""}.get(which, " AND parent_id IS NULL")
    ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()]
    if ids:                                    # e.g. /labels?ids=60 for one specific box
        where = f" AND id IN ({','.join('?' * len(ids))})"
    q = "SELECT * FROM nodes WHERE is_box = 1" + where + " ORDER BY parent_id IS NOT NULL, position, id"
    items = []
    for r in db().execute(q, ids):
        url = f"{base}/box/{r['id']}"
        svg = segno.make(url, error="m").svg_inline(scale=8, border=4, light="#fff")
        side = re.search(r'width="(\d+)"', svg).group(1)
        svg = svg.replace("<svg ", f'<svg viewBox="0 0 {side} {side}" ', 1)   # lets CSS scale the whole code, not crop it
        items.append({"node": r, "url": url, "svg": svg, "path": " › ".join(path_names(r))})
    return render_template("labels.html", labels=items, base=base, all_boxes=not only,
                           weak_base=base.startswith("http://127.") or base.startswith("http://localhost"))


@app.route("/reindex")
@edit_required
def reindex_all():
    for r in db().execute("SELECT id FROM nodes").fetchall():
        reindex(r["id"])
    flash("Search index rebuilt.")
    return redirect(url_for("home"))


if not EDIT_MODE:   # live site: load the search model in the background so the first search isn't slow
    import threading
    threading.Thread(target=searchlib._get_model, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0" if os.environ.get("LAN") == "1" else "127.0.0.1",
            port=int(os.environ.get("PORT", 5000)), debug=False)
