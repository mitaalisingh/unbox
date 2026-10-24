"""A tiny stand-in for the AI text model so tests are fast and predictable.

Words in the same group "mean" the same thing (cup, mug, bottle...), so a query for one finds the others,
which is what the real model does for real language.
"""
import math
import re

DIM = 384
GROUPS = [
    {"cup", "mug", "tumbler", "bottle", "flask", "drink", "drinks", "drinking", "thermos"},
    {"lamp", "light", "bulb", "lighting"},
    {"cable", "wire", "hdmi", "cord", "charger"},
    {"blanket", "quilt", "bedding", "throw"},
]


def vec(text):
    words = set(re.findall(r"[a-z]+", text.lower()))
    v = [0.0] * DIM
    for i, group in enumerate(GROUPS):
        if words & group:
            v[i] = 1.0
    for w in words:                                    # every other word gets its own weak direction
        if not any(w in g for g in GROUPS):
            v[10 + (sum(map(ord, w)) % (DIM - 10))] += 0.25
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norm, 6) for x in v]
