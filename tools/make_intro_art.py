"""Draws the four illustrations for the first-time introduction (static/intro/*.svg). Plain Python, no extra libraries.
Run:  python tools/make_intro_art.py
"""
import math
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "intro")
CREAM, MID, DARK = "#e7e5d9", "#b0ac9e", "#78756a"
ORANGE, TEAL = "#e2693a", "#5ba39a"
K = math.sqrt(3) / 2


def pts(*p):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in p)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def cube(cx, cy, s, tape=True, top=CREAM, left=MID, right=DARK, qr=False):
    A, B, C, D = (cx, cy - s), (cx + K * s, cy - s / 2), (cx, cy), (cx - K * s, cy - s / 2)
    L1, L2 = (cx - K * s, cy + s / 2), (cx, cy + s)
    R2 = (cx + K * s, cy + s / 2)
    out = [f'<polygon points="{pts(D, C, L2, L1)}" fill="{left}"/>', f'<polygon points="{pts(C, B, R2, L2)}" fill="{right}"/>',
           f'<polygon points="{pts(A, B, C, D)}" fill="{top}"/>']
    if tape:
        p1, p2, p3, p4 = lerp(A, D, .38), lerp(A, D, .62), lerp(B, C, .62), lerp(B, C, .38)
        out.append(f'<polygon points="{pts(p1, p2, p3, p4)}" fill="{ORANGE}"/>')
    if qr:                                              # a QR-looking label on the left face
        rnd = random.Random(7)
        def P(u, v):
            return (D[0] + (C[0] - D[0]) * u + (L1[0] - D[0]) * v, D[1] + (C[1] - D[1]) * u + (L1[1] - D[1]) * v)
        out.append(f'<polygon points="{pts(P(.16, .16), P(.84, .16), P(.84, .84), P(.16, .84))}" fill="#ffffff"/>')
        n = 7
        for i in range(n):
            for j in range(n):
                finder = (i < 3 and j < 3) or (i > 3 and j < 3) or (i < 3 and j > 3)
                if finder and not ((i in (0, 2) or j in (0, 2)) if (i < 3 and j < 3) else True):
                    continue
                if finder or rnd.random() < .5:
                    u0, v0 = .22 + .56 * i / n, .22 + .56 * j / n
                    u1, v1 = u0 + .56 / n * .9, v0 + .56 / n * .9
                    out.append(f'<polygon points="{pts(P(u0, v0), P(u1, v0), P(u1, v1), P(u0, v1))}" fill="#0b0b0b"/>')
    return "\n".join(out)


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" fill="none">\n{body}\n</svg>\n'


def corners(x0, y0, x1, y1, n=26, color=TEAL):
    d = ""
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d += f'M{x + sx * n},{y} L{x},{y} L{x},{y + sy * n} '
    return f'<path d="{d}" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'


# 1: one parcel inside camera focus brackets, with a few small items floating around it
a1 = corners(70, 70, 330, 330) + cube(200, 215, 78) + (
    f'<circle cx="92" cy="108" r="11" fill="{CREAM}"/><circle cx="312" cy="96" r="7" fill="{ORANGE}"/>'
    f'<rect x="292" y="288" width="22" height="22" rx="5" fill="{MID}" transform="rotate(18 303 299)"/>'
    f'<rect x="84" y="276" width="16" height="30" rx="5" fill="{DARK}" transform="rotate(-12 92 291)"/>')
# 2: three small parcels dropping into a big one
a2 = (cube(200, 262, 90, top=CREAM) + cube(120, 130, 34, top=MID, left=DARK, right="#5b594f", tape=False)
      + cube(205, 95, 34, top=CREAM, left=MID, right=DARK) + cube(290, 140, 34, top=MID, left=DARK, right="#5b594f", tape=False)
      + f'<path d="M120 175 Q140 215 188 222 M205 140 L205 196 M290 185 Q272 215 222 222" stroke="{TEAL}" stroke-width="3" stroke-dasharray="3 8" stroke-linecap="round"/>')
# 3: a parcel with a QR label
a3 = cube(200, 205, 110, qr=True) + f'<path d="M64 330 H336" stroke="{TEAL}" stroke-width="3" stroke-linecap="round"/>'
# 4: a padlock
a4 = (f'<path d="M140 190 V138 a60 60 0 0 1 120 0 V190" stroke="{CREAM}" stroke-width="26" stroke-linecap="round"/>'
      f'<rect x="100" y="180" width="200" height="150" rx="26" fill="{CREAM}"/><rect x="100" y="250" width="200" height="80" rx="26" fill="{MID}"/>'
      f'<rect x="100" y="250" width="200" height="30" fill="{MID}"/>'
      f'<circle cx="200" cy="248" r="20" fill="#0b0b0b"/><rect x="192" y="252" width="16" height="42" rx="6" fill="#0b0b0b"/>'
      f'<circle cx="305" cy="110" r="9" fill="{ORANGE}"/>')
for i, art in enumerate((a1, a2, a3, a4), 1):
    open(os.path.join(OUT, f"art{i}.svg"), "w").write(svg(art))
print("done")
