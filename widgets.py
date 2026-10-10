"""Small custom widgets: a sliding segmented switch and a gear icon."""
from __future__ import annotations

import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

SUPER = 4  # draw shapes at 4x and scale down, for smooth edges


def rounded(w: int, h: int, r: int, fill: str) -> Image.Image:
    im = Image.new("RGBA", (w * SUPER, h * SUPER), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w * SUPER - 1, h * SUPER - 1),
                                         radius=r * SUPER, fill=fill)
    return im.resize((w, h), Image.LANCZOS)


def gear(size: int, color: str) -> Image.Image:
    S = size * SUPER
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = S / 2
    r_out, r_in, teeth = S * 0.47, S * 0.36, 8
    pts = []
    for i in range(teeth * 4):
        a = 2 * math.pi * i / (teeth * 4) - math.pi / 2
        r = r_out if i % 4 in (1, 2) else r_in
        pts.append((c + r * math.cos(a), c + r * math.sin(a)))
    d.polygon(pts, fill=color)
    d.ellipse((c - S * 0.33, c - S * 0.33, c + S * 0.33, c + S * 0.33), fill=color)
    hole = S * 0.15
    d.ellipse((c - hole, c - hole, c + hole, c + hole), fill=(0, 0, 0, 0))
    return im.resize((size, size), Image.LANCZOS)


def plus(size: int, color: str) -> Image.Image:
    """A big, bold plus sign filling one text line."""
    S = size * SUPER
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    arm, t = S * 0.42, S * 0.095         # half-length and half-thickness of each bar
    c = S / 2
    d.rounded_rectangle((c - arm, c - t, c + arm, c + t), radius=int(t * 0.6), fill=color)
    d.rounded_rectangle((c - t, c - arm, c + t, c + arm), radius=int(t * 0.6), fill=color)
    return im.resize((size, size), Image.LANCZOS)


class SlideSwitch(tk.Canvas):
    """A pill-shaped multi-way switch whose highlight slides to the chosen option.

    Click an option, drag the highlight, or use the arrow keys.
    """

    def __init__(self, master, options, value, command, *, scale=1.0, font=None, colors=None):
        self.options = list(options)              # [(value, label), ...]
        self.command = command
        self.seg_w = int(round(70 * scale))
        self.h = int(round(32 * scale))
        self.pad = max(2, int(round(3 * scale)))
        width = self.seg_w * len(self.options) + 2 * self.pad
        super().__init__(master, width=width, height=self.h, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        self.font = font
        self.index = next((i for i, (v, _) in enumerate(self.options) if v == value), 0)
        self._x = self._slot_x(self.index)       # thumb's current x (animated)
        self._anim = None
        self._drag_from = None
        self._track = self.create_image(0, 0, anchor="nw")
        self._thumb = self.create_image(self._x, self.pad, anchor="nw")
        self._texts = [self.create_text(self._slot_x(i) + self.seg_w / 2, self.h / 2,
                                        text=label, font=font)
                       for i, (_, label) in enumerate(self.options)]
        self.set_colors(colors or {})
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Left>", lambda e: self.select(max(0, self.index - 1)))
        self.bind("<Right>", lambda e: self.select(min(len(self.options) - 1, self.index + 1)))

    # ----------------------------------------------------------------- public
    def set_colors(self, colors: dict) -> None:
        self.colors = {"bg": "#fafafa", "track": "#E9E9EC", "thumb": "#005FB8",
                       "text": "#5F6670", "text_on": "#FFFFFF", **colors}
        self.configure(background=self.colors["bg"])
        w = int(self.cget("width"))
        self._track_img = ImageTk.PhotoImage(rounded(w, self.h, self.h // 2, self.colors["track"]))
        th = self.h - 2 * self.pad
        self._thumb_img = ImageTk.PhotoImage(rounded(self.seg_w, th, th // 2, self.colors["thumb"]))
        self.itemconfigure(self._track, image=self._track_img)
        self.itemconfigure(self._thumb, image=self._thumb_img)
        self._paint_labels(self.index)

    def select(self, index: int, notify: bool = True) -> None:
        changed = index != self.index
        self.index = index
        self._animate_to(self._slot_x(index))
        self._paint_labels(index)
        if changed and notify and self.command:
            self.command(self.options[index][0])

    # ----------------------------------------------------------------- internals
    def _slot_x(self, i: int) -> float:
        return self.pad + i * self.seg_w

    def _slot_at(self, x: float) -> int:
        i = int((x - self.pad) // self.seg_w)
        return max(0, min(len(self.options) - 1, i))

    def _paint_labels(self, active: int) -> None:
        for i, t in enumerate(self._texts):
            self.itemconfigure(t, fill=self.colors["text_on"] if i == active else self.colors["text"])

    def _move_thumb(self, x: float) -> None:
        self._x = x
        self.coords(self._thumb, x, self.pad)

    def _animate_to(self, target: float, ms: int = 160) -> None:
        if self._anim:
            self.after_cancel(self._anim)
        start, steps = self._x, max(1, ms // 12)

        def step(k=1):
            t = k / steps
            ease = 1 - (1 - t) ** 3            # ease-out
            self._move_thumb(start + (target - start) * ease)
            if k < steps:
                self._anim = self.after(12, step, k + 1)
            else:
                self._anim = None
        step()

    def _press(self, e) -> None:
        self.focus_set()
        if self._x <= e.x <= self._x + self.seg_w:
            self._drag_from = (e.x, self._x)       # grabbed the highlight: drag it
        else:
            self._drag_from = None
            self.select(self._slot_at(e.x))

    def _drag(self, e) -> None:
        if not self._drag_from:
            return
        x0, thumb0 = self._drag_from
        x = max(self._slot_x(0), min(self._slot_x(len(self.options) - 1), thumb0 + e.x - x0))
        self._move_thumb(x)
        self._paint_labels(self._slot_at(x + self.seg_w / 2))

    def _release(self, e) -> None:
        if self._drag_from:
            self._drag_from = None
            self.select(self._slot_at(self._x + self.seg_w / 2))
