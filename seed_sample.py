"""Create a small made-up inventory so Unbox runs without anyone's real photos.

    python seed_sample.py            # writes ./data (inventory.db + photos/)
    EDIT_MODE=1 python app.py        # then open http://127.0.0.1:5000

Everything here is invented: item names, weights and the placeholder pictures (drawn with Pillow).
"""
import os
import secrets
import sqlite3
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import search as searchlib

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
PHOTOS = DATA / "photos"

SCHEMA = (Path(__file__).parent / "app.py").read_text().split('SCHEMA = """')[1].split('"""')[0]

# (kind, name, tags, weight_kg, colour, parent name or None)
BOXES = [
    ("Big Box 1", None), ("Big Box 2", None), ("Medium Box", None),
]
SMALL = [("Kitchen box", "Big Box 1", 3.2), ("Cables and chargers", "Big Box 2", 1.4)]
ITEMS = [
    ("Blue blanket", "throw, bedding, quilt, cover", None, "#6f8fb5", "Big Box 1"),
    ("Desk lamp", "light, lamp, study lamp, reading light", 0.9, "#d9b44a", "Big Box 1"),
    ("Board game", "game, cards, family game, tabletop", 0.6, "#b5655f", "Big Box 1"),
    ("Glass jars (4)", "jars, storage, containers, kitchen", None, "#7fb5a4", "Kitchen box"),
    ("Steel water bottle", "flask, bottle, drinks, thermos", None, "#9aa5b1", "Kitchen box"),
    ("Coffee mug", "cup, mug, tea cup, ceramic", None, "#c9885a", "Kitchen box"),
    ("Chopping board", "wooden board, cutting board, kitchen", None, "#a58862", "Kitchen box"),
    ("Phone charger", "charger, adapter, usb, power", None, "#555b66", "Cables and chargers"),
    ("HDMI cable", "hdmi, monitor cable, video cable, display", None, "#2f3640", "Cables and chargers"),
    ("Extension board", "power strip, multi plug, sockets", None, "#3d4452", "Cables and chargers"),
    ("Yoga mat", "exercise, fitness, mat, workout", 1.1, "#8e7cc3", "Big Box 2"),
    ("Paperback books (6)", "books, novels, reading", 2.0, "#c27c7c", "Medium Box"),
]


def placeholder(name, colour):
    """A plain coloured picture with the item's first letter, so tiles show the name only once."""
    img = Image.new("RGB", (900, 700), colour)
    d = ImageDraw.Draw(img)
    d.ellipse((290, 130, 610, 450), fill=(255, 255, 255), outline=(255, 255, 255), width=6)
    try:
        big = ImageFont.load_default(size=190)
    except TypeError:                        # very old Pillow
        big = ImageFont.load_default()
    letter = name[0].upper()
    w = d.textlength(letter, font=big)
    d.text((450 - w / 2, 175), letter, fill=colour, font=big)
    fname = secrets.token_hex(8) + ".jpg"
    img.save(PHOTOS / fname, "JPEG", quality=80)
    return fname


def embed(text):
    return searchlib.embed_one(text)


def main():
    if (DATA / "inventory.db").exists():
        sys.exit(f"{DATA / 'inventory.db'} already exists. Remove the data folder first if you want a fresh sample.")
    PHOTOS.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / "inventory.db")
    db.executescript(SCHEMA)
    ids = {}
    for pos, (name, _) in enumerate(BOXES):
        ids[name] = db.execute("INSERT INTO nodes (parent_id,is_box,name,position) VALUES (NULL,1,?,?)", (name, pos)).lastrowid
    for pos, (name, parent, kg) in enumerate(SMALL, 1):
        ids[name] = db.execute("INSERT INTO nodes (parent_id,is_box,name,weight_kg,position) VALUES (?,1,?,?,?)",
                               (ids[parent], name, kg, pos)).lastrowid
    for pos, (name, tags, kg, colour, parent) in enumerate(ITEMS, 10):
        db.execute("INSERT INTO nodes (parent_id,is_box,name,tags,weight_kg,photo,position,emb) VALUES (?,0,?,?,?,?,?,?)",
                   (ids[parent], name, tags, kg, placeholder(name, colour), pos, embed(f"{name} {tags}")))
    db.commit()
    print(f"Sample data written to {DATA}: {len(BOXES) + len(SMALL)} boxes, {len(ITEMS)} items.")


if __name__ == "__main__":
    main()
