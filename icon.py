"""Draws the SyncMate icon (two chasing arrows on a coloured disc) with Pillow."""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

COLORS = {
    "synced": "#2F6FEB",
    "syncing": "#E59E1A",
    "paused": "#8B929C",
    "error": "#D64541",
}


def make_icon(state: str = "synced", size: int = 64) -> Image.Image:
    s = 512  # draw large, then scale down for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, s - 8, s - 8), fill=COLORS.get(state, COLORS["synced"]))

    c, r, w = s / 2, s * 0.25, int(s * 0.085)
    box = (c - r, c - r, c + r, c + r)
    for start in (200, 20):  # two arcs, each ~125 degrees, with an arrow head
        end = start + 125
        d.arc(box, start, end, fill="white", width=w)
        a = math.radians(end)
        tip_x, tip_y = c + r * math.cos(a), c + r * math.sin(a)
        tang = a + math.pi / 2  # direction of travel (clockwise in screen coords)
        h = s * 0.13
        p1 = (tip_x + h * 0.95 * math.cos(tang), tip_y + h * 0.95 * math.sin(tang))
        p2 = (tip_x + h * 0.6 * math.cos(a), tip_y + h * 0.6 * math.sin(a))
        p3 = (tip_x - h * 0.6 * math.cos(a), tip_y - h * 0.6 * math.sin(a))
        d.polygon([p1, p2, p3], fill="white")
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str = "syncmate.ico") -> None:
    make_icon("synced", 256).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    save_ico()
    print("wrote syncmate.ico")
