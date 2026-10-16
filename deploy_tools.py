"""Prepare the encrypted data bundle that goes to GitHub / the live site.

    python deploy_tools.py pack     # whenever you change items: asks for your passphrase, writes deploy_data/bundle.enc

GitHub only ever sees bundle.enc, which is unreadable without the passphrase. You type the same passphrase
into the host's DATA_PASSPHRASE setting; the live app unlocks the bundle at start-up. The passphrase is
never printed or stored in files.
"""
import getpass
import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from bundlekey import derive_key

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
BUNDLE = Path(os.environ.get("BUNDLE_OUT", BASE / "deploy_data" / "bundle.enc"))


def ask_passphrase():
    phrase = os.environ.get("DATA_PASSPHRASE")          # only for automated tests
    if phrase:
        return phrase
    print("Choose a passphrase of 4 or more words (e.g. four unrelated words). You'll type the same one into Render.")
    a = getpass.getpass("Passphrase: ")
    if len(a.split()) < 3 or len(a) < 14:
        sys.exit("Too short. Use at least 3 words and 14 characters, e.g. four unrelated words.")
    if getpass.getpass("Type it again: ") != a:
        sys.exit("They didn't match. Nothing was changed.")
    return a


def pack():
    phrase = ask_passphrase()
    src = sqlite3.connect(DATA / "inventory.db")
    mem = sqlite3.connect(":memory:")
    src.backup(mem)                      # consistent copy even if the app is running
    used = {r[0] for r in mem.execute("SELECT DISTINCT photo FROM nodes WHERE photo IS NOT NULL")}
    db_bytes = mem.serialize()
    buf = io.BytesIO()
    missing = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("inventory.db", db_bytes)
        for name in sorted(used):
            p = DATA / "photos" / name
            if p.exists():
                z.write(p, f"photos/{name}")
            else:
                missing.append(name)
    token = Fernet(derive_key(phrase)).encrypt(buf.getvalue())
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_bytes(token)
    shown = BUNDLE.relative_to(BASE) if BASE in BUNDLE.parents else BUNDLE
    print(f"Packed {len(used) - len(missing)} photos + database -> {shown} ({len(token) / 1e6:.1f} MB, encrypted)")
    if missing:
        print("Warning: photo files missing on disk:", ", ".join(missing))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"pack": pack}.get(cmd, lambda: sys.exit(__doc__))()
