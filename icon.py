"""Draws the SyncMate icon: a retro striped sync loop in blues, on a cream tile.

The logo itself never changes; a small coloured dot in the corner shows the status
(no dot = everything is in sync).
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

# Status colours (also used for the dots in the main window).
COLORS = {
    "synced": "#2F6FB0",
    "syncing": "#E59E1A",
    "paused": "#8B929C",
    "error": "#D64541",
}

CREAM = "#F3E6CF"
STRIPES = ("#1F3A5F", "#2F6FB0", "#7DB8E3")  # outer → inner: navy, blue, sky
ARROW = "#1F3A5F"

_S = 1024
_cache: dict = {}


def _logo() -> Image.Image:
    if "logo" in _cache:
        return _cache["logo"]
    S = _S
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=int(S * 0.22), fill=CREAM)

    c, w = S / 2, 58
    radii = (330, 272, 214)
    r_in, r_out = radii[-1] - w / 2, radii[0] + w / 2
    for start in (205, 25):          # two arcs, each with an arrowhead at its end
        end = start + 118
        for col, r in zip(STRIPES, radii):
            d.arc((c - r, c - r, c + r, c + r), start, end, fill=col, width=w)
        a = math.radians(end)
        spread = (r_out - r_in) * 0.22
        b_in, b_out = r_in - spread, r_out + spread
        rm = (r_in + r_out) / 2
        t = a + math.pi / 2           # clockwise direction of travel
        d.polygon([
            (c + b_in * math.cos(a), c + b_in * math.sin(a)),
            (c + b_out * math.cos(a), c + b_out * math.sin(a)),
            (c + rm * math.cos(a) + 115 * math.cos(t), c + rm * math.sin(a) + 115 * math.sin(t)),
        ], fill=ARROW)
    _cache["logo"] = im
    return im


def make_icon(state: str = "synced", size: int = 64) -> Image.Image:
    """The logo at `size` px, with a status dot unless everything is in sync."""
    im = _logo().copy()
    if state != "synced" and state in COLORS:
        d = ImageDraw.Draw(im)
        r, cx, cy = _S * 0.21, _S * 0.77, _S * 0.77
        ring = _S * 0.05
        d.ellipse((cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring), fill="white")
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=COLORS[state])
    return im.resize((size, size), Image.LANCZOS)


def save_ico(path: str = "syncmate.ico") -> None:
    make_icon("synced", 256).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    save_ico()
    print("wrote syncmate.ico")
