"""Draws the four illustrations for the first-time introduction (static/intro/*.svg). Plain Python, no extra libraries.
Run:  python tools/make_intro_art.py
"""
import math
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "intro")
CREAM, MID, DARK = "#FEFAF0", "#B79858", "#A13A1E"               # nuage de lait, eau trouble, terre cuite
ORANGE, TEAL = "#F1C166", "#88B8CE"                                 # miel dore, bleu porcelaine
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


STYLE = """<style>
.bx{transform-box:fill-box;transform-origin:center}
.bob{animation:bob 3.2s ease-in-out infinite alternate}.bob2{animation:bob 4.1s ease-in-out -1s infinite alternate}.bob3{animation:bob 3.6s ease-in-out -2s infinite alternate}
@keyframes bob{from{transform:translateY(0)}to{transform:translateY(-9px)}}
.pulse{animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(.93)}}
.tw{animation:tw 2s ease-in-out infinite}
@keyframes tw{0%,100%{opacity:.35}50%{opacity:1}}
.dropin{animation:dropin 3.6s ease-in infinite}.dropin.d2{animation-delay:-1.2s}.dropin.d3{animation-delay:-2.4s}
@keyframes dropin{0%{transform:translateY(-14px);opacity:0}15%{opacity:1}70%{transform:translateY(64px);opacity:1}85%,100%{transform:translateY(74px);opacity:0}}
.flow{animation:flow 1.4s linear infinite}
@keyframes flow{to{stroke-dashoffset:-22}}
.scan{animation:scan 2.6s ease-in-out infinite alternate}
@keyframes scan{from{transform:translateY(0)}to{transform:translateY(78px)}}
.lidmove{transform-box:view-box;transform-origin:200px 217px;animation:lidmove 10s ease-in-out infinite}
@keyframes lidmove{0%,6%{transform:translateY(0) rotate(0)}16%,70%{transform:translateY(-58px) rotate(-12deg)}82%,100%{transform:translateY(0) rotate(0)}}
.gin{animation:gin1 10s ease-in infinite}.gin.g2{animation-name:gin2}.gin.g3{animation-name:gin3}
@keyframes gin1{0%,19%{opacity:0;transform:translate(0,0)}20%{opacity:1}34%{transform:translate(80px,192px);opacity:1}35%,100%{transform:translate(80px,192px);opacity:0}}
@keyframes gin2{0%,31%{opacity:0;transform:translate(0,0)}32%{opacity:1}46%{transform:translate(0,212px);opacity:1}47%,100%{transform:translate(0,212px);opacity:0}}
@keyframes gin3{0%,43%{opacity:0;transform:translate(0,0)}44%{opacity:1}58%{transform:translate(-82px,192px);opacity:1}59%,100%{transform:translate(-82px,192px);opacity:0}}
.shackle{animation:shackle 3.6s ease-in-out infinite}
@keyframes shackle{0%,30%{transform:translateY(-16px)}45%,85%{transform:translateY(0)}100%{transform:translateY(-16px)}}
.shake{animation:shake 3.6s ease-in-out infinite}
@keyframes shake{0%,44%,56%,100%{transform:rotate(0)}48%{transform:rotate(-2.5deg)}52%{transform:rotate(2.5deg)}}
@media (prefers-reduced-motion: reduce){*{animation:none!important}}
</style>"""


def g(cls, body):
    return f'<g class="bx {cls}">{body}</g>'


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" fill="none">\n{STYLE}\n{body}\n</svg>\n'


def corners(x0, y0, x1, y1, n=26, color=TEAL):
    d = ""
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d += f'M{x + sx * n},{y} L{x},{y} L{x},{y + sy * n} '
    return f'<path d="{d}" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'


# 1: one parcel inside camera focus brackets, with a few small items floating around it
a1 = (g("pulse", corners(70, 70, 330, 330)) + g("bob", cube(200, 215, 78))
      + g("bob2", f'<circle cx="92" cy="108" r="11" fill="{CREAM}"/>') + g("tw", f'<circle cx="312" cy="96" r="7" fill="{ORANGE}"/>')
      + g("bob3", f'<rect x="292" y="288" width="22" height="22" rx="5" fill="{MID}" transform="rotate(18 303 299)"/>')
      + g("bob2", f'<rect x="84" y="276" width="16" height="30" rx="5" fill="{DARK}" transform="rotate(-12 92 291)"/>'))
# 2: the big box opens, three small parcels fall in one by one, and the box closes again (10 s loop)
def box_pieces(cx, cy, size):
    A, B, C, D = (cx, cy - size), (cx + K * size, cy - size / 2), (cx, cy), (cx - K * size, cy - size / 2)
    L1, L2, R2 = (cx - K * size, cy + size / 2), (cx, cy + size), (cx + K * size, cy + size / 2)
    return A, B, C, D, L1, L2, R2


def small(cx, cy, cls):
    return f'<g class="{cls}">{cube(cx, cy, 28, top=MID, left=DARK, right="#542916", tape=False)}</g>'


A_, B_, C_, D_, L1_, L2_, R2_ = box_pieces(200, 262, 90)
lid_up = 8
lid = (f'<polygon points="{pts(D_, C_, (C_[0], C_[1] + 9), (D_[0], D_[1] + 9))}" fill="#8a6c3a"/>'
       f'<polygon points="{pts(C_, B_, (B_[0], B_[1] + 9), (C_[0], C_[1] + 9))}" fill="#7a2c16"/>'
       f'<polygon points="{pts(A_, B_, C_, D_)}" fill="{CREAM}"/>'
       f'<polygon points="{pts((A_[0] + (D_[0] - A_[0]) * .38, A_[1] + (D_[1] - A_[1]) * .38), (A_[0] + (D_[0] - A_[0]) * .62, A_[1] + (D_[1] - A_[1]) * .62), (B_[0] + (C_[0] - B_[0]) * .62, B_[1] + (C_[1] - B_[1]) * .62), (B_[0] + (C_[0] - B_[0]) * .38, B_[1] + (C_[1] - B_[1]) * .38))}" fill="{ORANGE}"/>')
a2 = (f'<polygon points="{pts(A_, B_, C_, D_)}" fill="#1a0d06"/>'                                   # the inside of the box
      + small(120, 100, "gin g1") + small(200, 84, "gin g2") + small(282, 100, "gin g3")             # they fall behind the front of the box
      + f'<polygon points="{pts(D_, C_, L2_, L1_)}" fill="{MID}"/><polygon points="{pts(C_, B_, R2_, L2_)}" fill="{DARK}"/>'
      + f'<g class="lidmove">{lid}</g>')
# 3: a parcel with a QR label that is being scanned
a3 = (g("bob", cube(200, 205, 110, qr=True))
      + f'<path d="M119.9 175.3 L184.8 212.6" stroke="{TEAL}" stroke-width="5" stroke-linecap="round" class="bx scan"/>'
      + f'<path d="M64 330 H336" stroke="{TEAL}" stroke-width="3" stroke-linecap="round" stroke-dasharray="12 10" class="flow"/>')
# 4: a padlock that locks and unlocks
a4 = (g("shackle", f'<path d="M140 190 V138 a60 60 0 0 1 120 0 V190" stroke="{CREAM}" stroke-width="26" stroke-linecap="round"/>')
      + g("shake", f'<rect x="100" y="180" width="200" height="150" rx="26" fill="{CREAM}"/><rect x="100" y="250" width="200" height="80" rx="26" fill="{MID}"/>'
          f'<rect x="100" y="250" width="200" height="30" fill="{MID}"/>'
          f'<circle cx="200" cy="248" r="20" fill="#0b0b0b"/><rect x="192" y="252" width="16" height="42" rx="6" fill="#0b0b0b"/>')
      + g("tw", f'<circle cx="305" cy="110" r="9" fill="{ORANGE}"/>'))
for i, art in enumerate((a1, a2, a3, a4), 1):
    open(os.path.join(OUT, f"art{i}.svg"), "w").write(svg(art))
print("done")
