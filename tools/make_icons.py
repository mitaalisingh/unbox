"""Draws the app icons from the chosen logo (the QR-label box): static/icons/*.png and logo.svg.  Run:  python tools/make_icons.py"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_logo as L  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "icons")


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def draw(size, rounded, scale):
    """The logo on a dark tile. rounded=True: rounded corners with transparent edges. scale<1 leaves room for phone masks."""
    big = size * 4
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bg = rgb(L.DARK) + (255,)
    if rounded:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=bg)
    else:
        d.rectangle([0, 0, big, big], fill=bg)
    k = big / 512
    cx = cy = 256
    for pts, fill in L.shapes_c():
        d.polygon([((x - cx) * scale * k + big / 2, (y - 280) * scale * k + big / 2) for x, y in pts], fill=rgb(fill))
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    draw(512, True, 1.0).save(os.path.join(OUT, "icon-512.png"))
    draw(192, True, 1.0).save(os.path.join(OUT, "icon-192.png"))
    draw(180, False, 0.9).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))       # iPhones round the corners themselves
    draw(512, False, 0.74).convert("RGB").save(os.path.join(OUT, "icon-maskable-512.png"))     # Android may crop to a circle or squircle
    open(os.path.join(OUT, "logo.svg"), "w").write(L.logo_c())
    print("icons written")
