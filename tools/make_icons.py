"""Draws the app icon (a cube on black) in the coffee-shop colours: static/icons/*.png.  Run:  python tools/make_icons.py"""
import math
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "icons")
TOP, LEFT, RIGHT, TAPE = (254, 250, 240), (183, 152, 88), (161, 58, 30), (241, 193, 102)


def cube(size, pad):
    im = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = size / 2
    r = size * (0.5 - pad)
    top, tr, br, bot, bl, tl = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in (-90, -30, 30, 90, 150, 210)]
    c = (cx, cy)
    d.polygon([top, tr, c, tl], fill=TOP)
    d.polygon([tl, c, bot, bl], fill=LEFT)
    d.polygon([tr, br, bot, c], fill=RIGHT)
    lerp = lambda a, b, t: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)       # a strip of tape across the lid
    d.polygon([lerp(top, tl, .38), lerp(top, tl, .62), lerp(tr, c, .62), lerp(tr, c, .38)], fill=TAPE)
    return im


for name, size, pad in (("icon-512.png", 512, 0.2), ("icon-192.png", 192, 0.2), ("apple-touch-icon.png", 180, 0.2), ("icon-maskable-512.png", 512, 0.27)):
    cube(size, pad).save(os.path.join(OUT, name))
print("icons written")
