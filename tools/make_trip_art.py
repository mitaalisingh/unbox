"""Draws the 20 pictures used on the trip cards (static/trips/trip-01.svg ... trip-20.svg).
Five little scenes (boxes, a moving van, luggage, a house, a desk) in four pastel colour sets.
Run:  python tools/make_trip_art.py
"""
import math
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "trips")
K = math.sqrt(3) / 2
# bg, light, main, second, accent, ink (bold food-app colours: olive, tomato, bubblegum, mango)
PALETTES = [
    ("#9bb552", "#fff4d6", "#f2893d", "#c95f27", "#e24b8f", "#3b2a14"),     # olive green tile, orange things
    ("#e8603a", "#fff1dc", "#f4c23a", "#d99b1f", "#2f7a3e", "#43160c"),     # tomato tile, yolk yellow
    ("#ec93c9", "#fff0f6", "#e8553a", "#b83a2a", "#5fa046", "#4a1730"),     # bubblegum tile, tomato red
    ("#f4a93c", "#fff6e0", "#e24b8f", "#a82f68", "#4f9a3a", "#3a1d0a"),     # mango tile, pink
]


def pts(*p):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in p)


def cube(cx, cy, s, top, left, right, tape=None):
    A, B, C, D = (cx, cy - s), (cx + K * s, cy - s / 2), (cx, cy), (cx - K * s, cy - s / 2)
    L1, L2, R2 = (cx - K * s, cy + s / 2), (cx, cy + s), (cx + K * s, cy + s / 2)
    out = f'<polygon points="{pts(D, C, L2, L1)}" fill="{left}"/><polygon points="{pts(C, B, R2, L2)}" fill="{right}"/><polygon points="{pts(A, B, C, D)}" fill="{top}"/>'
    if tape:
        t = lambda a, b, f: (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        out += f'<polygon points="{pts(t(A, D, .38), t(A, D, .62), t(B, C, .62), t(B, C, .38))}" fill="{tape}"/>'
    return out


def plant(x, y, p):
    _, light, main, second, accent, ink = p
    return (f'<path d="M{x-14} {y} h28 l-4 24 h-20z" fill="{accent}"/>'
            f'<ellipse cx="{x-9}" cy="{y-14}" rx="7" ry="16" fill="{second}" transform="rotate(-24 {x-9} {y-14})"/>'
            f'<ellipse cx="{x+9}" cy="{y-16}" rx="7" ry="17" fill="{main}" transform="rotate(22 {x+9} {y-16})"/>'
            f'<ellipse cx="{x}" cy="{y-22}" rx="7" ry="19" fill="{second}"/>')


def ground(p, y=196):
    return f'<rect x="0" y="{y}" width="400" height="{240-y}" fill="{p[1]}" opacity=".55"/>'


def sun(p, x=330, y=52, r=26):
    return f'<circle cx="{x}" cy="{y}" r="{r}" fill="{p[1]}" opacity=".9"/>'


def scene_stack(p):
    bg, light, main, second, accent, ink = p
    return (sun(p) + ground(p) + cube(150, 160, 58, light, main, second, accent) + cube(238, 176, 44, light, second, ink, None)
            + cube(176, 100, 38, light, main, second, accent) + plant(318, 190, p))


def scene_van(p):
    bg, light, main, second, accent, ink = p
    wheels = "".join(f'<circle cx="{x}" cy="190" r="17" fill="{ink}"/><circle cx="{x}" cy="190" r="7" fill="{light}"/>' for x in (118, 282))
    clouds = (f'<ellipse cx="90" cy="52" rx="34" ry="12" fill="{light}" opacity=".85"/><ellipse cx="112" cy="44" rx="22" ry="12" fill="{light}" opacity=".85"/>'
              f'<ellipse cx="320" cy="76" rx="30" ry="10" fill="{light}" opacity=".7"/>')
    return (clouds + ground(p, 200) + f'<rect x="0" y="206" width="400" height="6" fill="{ink}" opacity=".18"/>'
            + f'<rect x="60" y="92" width="170" height="96" rx="10" fill="{main}"/><path d="M230 120 h46 q14 0 24 14 l16 24 v30 h-86z" fill="{light}"/>'
            + f'<path d="M244 128 h26 q8 0 14 9 l8 12 h-48z" fill="{bg}"/>' + f'<rect x="76" y="108" width="52" height="40" rx="6" fill="{accent}"/><rect x="138" y="108" width="40" height="40" rx="6" fill="{second}"/>'
            + wheels + f'<rect x="300" y="168" width="14" height="8" rx="3" fill="{accent}"/>')


def scene_luggage(p):
    bg, light, main, second, accent, ink = p
    def case(x, y, w, h, c, d):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{c}"/>'
                f'<rect x="{x + w * .3}" y="{y - 14}" width="{w * .4}" height="16" rx="6" fill="none" stroke="{d}" stroke-width="5"/>'
                f'<rect x="{x + 14}" y="{y}" width="8" height="{h}" fill="{d}" opacity=".35"/><rect x="{x + w - 22}" y="{y}" width="8" height="{h}" fill="{d}" opacity=".35"/>')
    return (sun(p, 70, 56, 24) + ground(p) + case(110, 90, 100, 106, main, second) + case(216, 118, 78, 78, accent, ink)
            + f'<ellipse cx="86" cy="178" rx="34" ry="20" fill="{second}"/><path d="M62 170 q24 -26 48 0" fill="none" stroke="{ink}" stroke-width="5" stroke-linecap="round"/>' + plant(334, 188, p))


def scene_house(p):
    bg, light, main, second, accent, ink = p
    return (sun(p, 72, 54, 24) + ground(p) + f'<rect x="228" y="62" width="18" height="40" fill="{second}"/>'
            + f'<rect x="140" y="104" width="160" height="92" fill="{light}"/><polygon points="126,106 220,44 314,106" fill="{main}"/>'
            + f'<rect x="196" y="140" width="38" height="56" rx="6" fill="{accent}"/><rect x="152" y="124" width="30" height="30" rx="4" fill="{bg}"/><rect x="252" y="124" width="30" height="30" rx="4" fill="{bg}"/>'
            + cube(96, 176, 26, light, main, second, accent) + f'<circle cx="340" cy="150" r="30" fill="{second}"/><rect x="336" y="170" width="8" height="26" fill="{ink}" opacity=".6"/>')


def scene_desk(p):
    bg, light, main, second, accent, ink = p
    books = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="12" rx="3" fill="{c}"/>' for x, y, w, c in ((70, 168, 72, main), (76, 156, 62, accent), (72, 144, 66, second)))
    return (f'<rect x="150" y="30" width="110" height="80" rx="8" fill="{light}" opacity=".8"/><path d="M205 30 v80 M150 70 h110" stroke="{bg}" stroke-width="5"/>'
            + ground(p, 190) + f'<rect x="40" y="184" width="320" height="14" rx="6" fill="{second}"/><rect x="60" y="198" width="10" height="30" fill="{second}"/><rect x="330" y="198" width="10" height="30" fill="{second}"/>'
            + books + f'<path d="M230 184 l14 -62 h28 l14 62z" fill="{accent}"/><rect x="254" y="104" width="4" height="20" fill="{ink}"/><circle cx="258" cy="100" r="9" fill="{light}"/>'
            + f'<rect x="292" y="160" width="30" height="24" rx="5" fill="{light}"/><path d="M322 166 q12 0 12 10 t-12 10" fill="none" stroke="{light}" stroke-width="5"/>'
            + cube(352, 176, 18, light, main, second, accent))


SCENES = [scene_stack, scene_van, scene_luggage, scene_house, scene_desk]
seen = set()
for i in range(20):
    s, c = i % 5, (i // 5 + i % 5) % 4
    assert (s, c) not in seen
    seen.add((s, c))
    p = PALETTES[c]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 240" preserveAspectRatio="xMidYMid slice">'
           f'<rect width="400" height="240" fill="{p[0]}"/>{SCENES[s](p)}</svg>\n')
    open(os.path.join(OUT, f"trip-{i + 1:02d}.svg"), "w").write(svg)
print("20 pictures written")
