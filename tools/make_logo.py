"""Draws the Unbox logo options as SVG (coffee-shop colours). Run:  python tools/make_logo.py [out_dir]
Writes logo-a.svg (lid lifting), logo-b.svg (open flaps with a glow) and logo-c.svg (QR label box)."""
import math
import os
import random
import sys

K = math.sqrt(3) / 2
CREAM, TAN, TERRA, HONEY, ESPRESSO, SKY, DARK = "#FEFAF0", "#B79858", "#A13A1E", "#F1C166", "#542916", "#88B8CE", "#2E1509"


def P(*pts):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def corners(cx, cy, s):
    return (cx, cy - s), (cx + K * s, cy - s / 2), (cx, cy), (cx - K * s, cy - s / 2), (cx - K * s, cy + s / 2), (cx, cy + s), (cx + K * s, cy + s / 2)


def wrap(body, bg=DARK):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="{bg}"/>{body}</svg>\n'


def logo_a():
    A, B, C, D, L1, L2, R2 = corners(256, 300, 128)
    body = [f'<polygon points="{P(D, C, L2, L1)}" fill="{TAN}"/><polygon points="{P(C, B, R2, L2)}" fill="{TERRA}"/><polygon points="{P(A, B, C, D)}" fill="{ESPRESSO}"/>']
    up = 54                                          # the lid, lifted
    lid = [(x * 1.0, y - up) for x, y in (A, B, C, D)]
    a, b, c, d = lid
    body.append(f'<polygon points="{P(d, c, (c[0], c[1] + 12), (d[0], d[1] + 12))}" fill="#8a6c3a"/><polygon points="{P(c, b, (b[0], b[1] + 12), (c[0], c[1] + 12))}" fill="#7a2c16"/>')
    body.append(f'<polygon points="{P(a, b, c, d)}" fill="{CREAM}"/>')
    body.append(f'<polygon points="{P(lerp(a, d, .4), lerp(a, d, .6), lerp(b, c, .6), lerp(b, c, .4))}" fill="{HONEY}"/>')
    cx, cy = 256, 150                                # rays above the lid
    for ang in (-130, -90, -50):
        r1, r2 = 58, 88
        x1, y1 = cx + r1 * math.cos(math.radians(ang)), cy + r1 * math.sin(math.radians(ang))
        x2, y2 = cx + r2 * math.cos(math.radians(ang)), cy + r2 * math.sin(math.radians(ang))
        body.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{HONEY}" stroke-width="12" stroke-linecap="round"/>')
    return wrap("".join(body))


def logo_b():
    A, B, C, D, L1, L2, R2 = corners(256, 300, 126)
    out = [f'<polygon points="{P(D, C, L2, L1)}" fill="{TAN}"/><polygon points="{P(C, B, R2, L2)}" fill="{TERRA}"/>']
    out.append(f'<polygon points="{P((A[0], A[1] - 30), (B[0] + 20, B[1] - 40), (B[0] + 20, B[1] - 10), (A[0], A[1] + 20))}" fill="none"/>')   # (spacer)
    out.append(f'<polygon points="{P(A, B, C, D)}" fill="{HONEY}"/>')                                                                           # the glow inside
    out.append(f'<polygon points="{P(lerp(A, D, .5), lerp(A, B, .5), lerp(C, B, .5), lerp(C, D, .5))}" fill="#FFE3A0"/>')
    fl = (-34, -70)
    out.append(f'<polygon points="{P(D, A, (A[0] + fl[0], A[1] + fl[1]), (D[0] + fl[0], D[1] + fl[1]))}" fill="{CREAM}"/>')                      # back-left flap
    out.append(f'<polygon points="{P(A, B, (B[0] - fl[0], B[1] + fl[1]), (A[0] - fl[0], A[1] + fl[1]))}" fill="#E9DFC4"/>')                    # back-right flap
    out.append(f'<polygon points="{P(D, C, (C[0] - 26, C[1] + 34), (D[0] - 26, D[1] + 34))}" fill="#EADBB5"/>')                                # front flaps folded down
    out.append(f'<polygon points="{P(C, B, (B[0] + 26, B[1] + 34), (C[0] + 26, C[1] + 34))}" fill="#D9BE86"/>')
    return wrap("".join(out))


def shapes_c(cx=256, cy=270, size=140):
    """The QR-label box as a list of (corner points, colour), drawn back to front."""
    A, B, C, D, L1, L2, R2 = corners(cx, cy, size)
    out = [([D, C, L2, L1], TAN), ([C, B, R2, L2], TERRA), ([A, B, C, D], CREAM),
           ([lerp(A, D, .38), lerp(A, D, .62), lerp(B, C, .62), lerp(B, C, .38)], HONEY)]

    def face(u, v):                                  # a point on the left face
        return (D[0] + (C[0] - D[0]) * u + (L1[0] - D[0]) * v, D[1] + (C[1] - D[1]) * u + (L1[1] - D[1]) * v)
    out.append(([face(.14, .14), face(.86, .14), face(.86, .86), face(.14, .86)], CREAM))
    rnd, n = random.Random(11), 7
    for i in range(n):
        for j in range(n):
            finder = (i < 3 and j < 3) or (i > 3 and j < 3) or (i < 3 and j > 3)
            ring = (i in (0, 2) or j in (0, 2)) if (i < 3 and j < 3) else (i in (4, 6) or j in (0, 2)) if (i > 3 and j < 3) else (i in (0, 2) or j in (4, 6))
            if (finder and ring) or (not finder and rnd.random() < .5):
                u0, v0 = .2 + .6 * i / n, .2 + .6 * j / n
                u1, v1 = u0 + .6 / n * .92, v0 + .6 / n * .92
                out.append(([face(u0, v0), face(u1, v0), face(u1, v1), face(u0, v1)], DARK))
    return out


def logo_c():
    return wrap("".join(f'<polygon points="{P(*pts)}" fill="{fill}"/>' for pts, fill in shapes_c()))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for name, fn in (("logo-a.svg", logo_a), ("logo-b.svg", logo_b), ("logo-c.svg", logo_c)):
        open(os.path.join(out, name), "w").write(fn())
    print("logos written to", out)
