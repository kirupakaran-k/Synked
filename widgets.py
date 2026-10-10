"""Small custom widgets: a sliding segmented switch and a gear icon."""
from __future__ import annotations

import math
import time
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


def dots(size: int, color: str) -> Image.Image:
    """Three vertical dots (the 'more options' icon)."""
    S = size * SUPER
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = S * 0.095
    for cy in (S * 0.2, S * 0.5, S * 0.8):
        d.ellipse((S / 2 - r, cy - r, S / 2 + r, cy + r), fill=color)
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


def red_x(w: int, h: int, fill: str, mark: str = "#FFFFFF") -> Image.Image:
    """A red rounded box with a bold white cross (the 'remove' button)."""
    im = Image.new("RGBA", (w * SUPER, h * SUPER), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w * SUPER - 1, h * SUPER - 1), radius=int(h * 0.15 * SUPER),
                        fill=fill)
    cx, cy, arm = w * SUPER / 2, h * SUPER / 2, h * SUPER * 0.2
    t = max(2.4, h * 0.095) * SUPER                       # stroke width: bold
    for dx in (arm, -arm):
        d.line((cx - dx, cy - arm, cx + dx, cy + arm), fill=mark, width=int(t))
        for ex, ey in ((cx - dx, cy - arm), (cx + dx, cy + arm)):   # round ends
            d.ellipse((ex - t / 2, ey - t / 2, ex + t / 2, ey + t / 2), fill=mark)
    return im.resize((w, h), Image.LANCZOS)


def _arrow_glyph(S: int, kind: str, color: str) -> Image.Image:
    """Bold arrow(s) on a transparent S×S square, pointing right ('right'), left ('left'),
    or one each way, stacked like ⇄ ('both')."""
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    t = S * 0.1                                     # stroke thickness

    def arrow(y, x0, x1, head, half):
        """Shaft from x0 to the tip at x1 (pointing right when x1 > x0)."""
        sign = 1 if x1 > x0 else -1
        base = x1 - sign * head
        d.line((x0, y, base + sign * t * 0.5, y), fill=color, width=int(t))
        d.ellipse((x0 - t / 2, y - t / 2, x0 + t / 2, y + t / 2), fill=color)
        d.polygon([(x1, y), (base, y - half), (base, y + half)], fill=color)

    if kind == "both":
        arrow(S * 0.32, S * 0.14, S * 0.88, S * 0.25, S * 0.155)
        arrow(S * 0.68, S * 0.86, S * 0.12, S * 0.25, S * 0.155)
    elif kind == "left":
        arrow(S * 0.5, S * 0.86, S * 0.12, S * 0.32, S * 0.21)
    else:
        arrow(S * 0.5, S * 0.14, S * 0.88, S * 0.32, S * 0.21)
    return im


def sync_arrow(w: int, h: int, kind: str, fill: str, mark: str, vertical=False) -> Image.Image:
    """The arrow between a sync's folders: a filled accent box (like the Add folder sync
    button) with bold arrows in it. vertical=True turns them to point down/up."""
    W, H = w * SUPER, h * SUPER
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, W - 1, H - 1), radius=int(h * 0.18 * SUPER),
                                         fill=fill)
    S = int(H * 0.92)
    glyph = _arrow_glyph(S, kind, mark)
    if vertical:
        glyph = glyph.rotate(-90)                   # right → down, left → up
    im.alpha_composite(glyph, ((W - S) // 2, (H - S) // 2))
    return im.resize((w, h), Image.LANCZOS)


class ImageButton(tk.Label):
    """A button drawn from images: normal, hover and pressed looks."""

    def __init__(self, master, images, command, *, bg):
        self.images, self.command = images, command
        super().__init__(master, image=images["normal"], background=bg, borderwidth=0,
                         highlightthickness=0, padx=0, pady=0, cursor="hand2", takefocus=1)
        self._inside = False
        self.bind("<Enter>", lambda e: self._look("hover", inside=True))
        self.bind("<Leave>", lambda e: self._look("normal", inside=False))
        self.bind("<ButtonPress-1>", lambda e: self._look("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())

    def _look(self, name, inside=None):
        if inside is not None:
            self._inside = inside
        self.configure(image=self.images[name])

    def _release(self, e):
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._look("hover" if inside else "normal")
        if inside:
            self.invoke()

    def invoke(self):
        if self.command:
            self.command()


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


class FlowRow(tk.Frame):
    """A row of widgets that rearranges itself when the window gets narrow.

    mode "flow":  items fill a line and wrap onto the next line when they don't fit.
    mode "stack": everything on one line if it fits, otherwise one item per line.

    Items must be children of this frame; add() them in order.
    A "stretch" item takes the line's spare room (and shares it equally with other
    stretch items of the same `uniform` group); `min_width` is the narrowest a
    stretch item may get before the row wraps.
    """

    def __init__(self, master, *, bg, gap=8, vgap=6, mode="flow", on_layout=None):
        super().__init__(master, background=bg)
        self.bg, self.gap, self.vgap, self.mode = bg, gap, vgap, mode
        self.on_layout = on_layout
        self.items: list[dict] = []
        self._lines: list[tk.Frame] = []
        self._sig = None
        self._width = 0
        self._pending = None
        self.stacked = False
        self._told = None
        self.bind("<Configure>", self._on_configure, add="+")

    def add(self, widget, *, stretch=False, min_width=0, sticky="w", uniform="",
            visible=True):
        self.items.append({"w": widget, "stretch": stretch, "min": min_width,
                           "sticky": sticky, "uniform": uniform, "visible": visible})
        self.relayout()
        return widget

    def set_visible(self, widget, visible: bool) -> None:
        for it in self.items:
            if it["w"] is widget and it["visible"] != visible:
                it["visible"] = visible
                if not visible:
                    widget.grid_forget()
                self.relayout()

    def set_bg(self, bg: str) -> None:
        self.bg = bg
        for w in [self, *self._lines]:
            w.configure(background=bg)

    def relayout(self) -> None:
        """Re-arrange soon (sizes settle first; several calls collapse into one)."""
        if self._pending is None:
            self._pending = self.after_idle(self._run)

    # ---- internals ------------------------------------------------------------
    def _on_configure(self, e):
        if e.width != self._width:
            self._width = e.width
            self.relayout()

    def _need(self, it) -> int:
        return max(it["min"], it["w"].winfo_reqwidth()) if it["stretch"] else it["w"].winfo_reqwidth()

    def _line_width(self, line) -> int:
        widths = [self._need(it) for it in line]
        for group in {it["uniform"] for it in line if it["stretch"] and it["uniform"]}:
            same = [i for i, it in enumerate(line) if it["stretch"] and it["uniform"] == group]
            top = max(widths[i] for i in same)
            for i in same:
                widths[i] = top
        return sum(widths) + self.gap * (len(line) - 1)

    def _plan(self, width):
        shown = [it for it in self.items if it["visible"]]
        if not shown:
            return []
        if width <= 1 or self._line_width(shown) <= width:
            return [shown]
        if self.mode == "stack":
            return [[it] for it in shown]
        lines, line = [], []
        for it in shown:
            if line and self._line_width(line + [it]) > width:
                lines.append(line)
                line = []
            line.append(it)
        lines.append(line)
        return lines

    def _place(self, holder, line):
        for c, it in enumerate(line):
            it["w"].grid(in_=holder, row=0, column=c, sticky=it["sticky"],
                         padx=(self.gap if c else 0, 0), pady=0)
            if holder is not self:
                it["w"].lift(holder)     # line frames are made later; keep items on top
            if it["stretch"]:
                holder.columnconfigure(c, weight=1, uniform=it["uniform"], minsize=it["min"])

    def _run(self):
        self._pending = None
        try:
            lines = self._plan(self.winfo_width())
        except tk.TclError:
            return
        sig = tuple(tuple(id(it) for it in line) for line in lines)
        if sig == self._sig:
            return
        self._sig = sig
        n = len(self.items) + 1
        for c in range(n):                      # start from a clean grid
            self.columnconfigure(c, weight=0, uniform="", minsize=0)
        if len(lines) == 1:
            # one line (the usual case): items sit straight in this frame, no extra frames
            for holder in self._lines:
                holder.grid_forget()
            self._place(self, lines[0])
        else:
            while len(self._lines) < len(lines):
                self._lines.append(tk.Frame(self, background=self.bg))
            self.columnconfigure(0, weight=1)
            for i, holder in enumerate(self._lines):
                if i < len(lines):
                    for c in range(n):
                        holder.columnconfigure(c, weight=0, uniform="", minsize=0)
                    holder.grid(row=i, column=0, sticky="ew", pady=(self.vgap if i else 0, 0))
                    self._place(holder, lines[i])
                else:
                    holder.grid_forget()
        stacked = len(lines) > 1
        if stacked != self._told:
            self.stacked = self._told = stacked
            if self.on_layout:
                self.on_layout(stacked)


class Tooltip:
    """A small hint that appears next to the mouse after a short pause.

    Each hint belongs to the widget under the mouse. While it is up, it checks a few
    times a second that the mouse is still over that widget and the window is still
    showing, so it never lingers (minimised window, Alt+Tab, a widget that vanished).
    """

    def __init__(self, root, delay_ms: int = 450, wrap_px: int = 320):
        self.root, self.delay, self.wrap = root, delay_ms, wrap_px
        self.win = None
        self.owner = None          # Tk path of the widget the hint belongs to
        self._after = None
        self._watch = None
        self.colors = {"bg": "#FFFFFF", "fg": "#1C1C1C", "border": "#C9CDD3"}
        # anything that moves or hides the window takes the hint away
        root.bind("<Unmap>", lambda e: e.widget is root and self.hide(), add="+")
        root.bind("<FocusOut>", lambda e: self._focus_out(e), add="+")

    def set_colors(self, bg, fg, border):
        self.colors = {"bg": bg, "fg": fg, "border": border}
        self.hide()

    def schedule(self, text_fn, at_fn=None, owner=None):
        """Show text_fn() after the delay (text_fn may return '' for nothing), as long as
        the mouse is still over `owner` then. at_fn() may give the (x, y) screen position;
        otherwise the hint follows the mouse."""
        self.hide()
        owner = str(owner) if owner is not None else None
        self._after = self.root.after(
            self.delay, lambda: self.show(text_fn(), at_fn() if at_fn else None, owner))

    def cancel(self):
        if self._after:
            try:
                self.root.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    # ---- where the mouse is ---------------------------------------------------
    def _app_showing(self) -> bool:
        try:
            return self.root.state() not in ("iconic", "withdrawn") and \
                bool(self.root.winfo_viewable())
        except tk.TclError:
            return False

    def _over(self, owner) -> bool:
        """Is the mouse over `owner` (a Tk path) or something inside it?"""
        if owner is None:
            return True
        try:
            if not int(self.root.tk.call("winfo", "exists", owner)) or \
                    not int(self.root.tk.call("winfo", "viewable", owner)):
                return False
            x, y = self.root.winfo_pointerxy()
            under = str(self.root.tk.call("winfo", "containing", x, y))
        except (tk.TclError, ValueError):
            return False
        return under == owner or under.startswith(owner + ".")

    def _focus_out(self, e):
        # only when the whole app loses focus (Alt+Tab, clicking another app)
        def check():
            try:
                if str(self.root.tk.call("focus")) in ("", "none"):
                    self.hide()
            except tk.TclError:
                pass
        self.root.after(50, check)

    def _check(self):
        self._watch = None
        if self.win is None:
            return
        if not self._app_showing() or not self._over(self.owner):
            self.hide()
            return
        self._watch = self.root.after(200, self._check)

    # ---- showing ---------------------------------------------------------------
    def show(self, text: str, at=None, owner=None):
        self.hide()
        if not text or not self._app_showing() or not self._over(owner):
            return
        self.owner = owner
        c = self.colors
        self.win = w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(w, background=c["border"], padx=1, pady=1)
        frame.pack()
        tk.Label(frame, text=text, background=c["bg"], foreground=c["fg"], justify="left",
                 wraplength=self.wrap, padx=10, pady=7,
                 font=("Segoe UI", 9)).pack()
        x, y = at if at else (self.root.winfo_pointerx() + 14, self.root.winfo_pointery() + 18)
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        x = min(x, sw - w.winfo_reqwidth() - 8)
        if y + w.winfo_reqheight() > sh - 8:
            y = self.root.winfo_pointery() - w.winfo_reqheight() - 10
        w.geometry(f"+{x}+{y}")
        fade_in(w, 120)
        self._watch = self.root.after(200, self._check)

    def hide(self):
        self.cancel()
        if self._watch:
            try:
                self.root.after_cancel(self._watch)
            except tk.TclError:
                pass
            self._watch = None
        self.owner = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    # ---- attaching to widgets -------------------------------------------------
    def attach(self, widget, text_fn):
        """Hint shown while hovering over `widget`; text_fn() gives the current text."""
        widget.bind("<Enter>", lambda e: self.schedule(text_fn, owner=widget), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def attach_list(self, combobox, text_for_index):
        """Hints for each entry of a ttk.Combobox's open dropdown list."""
        try:
            pop = combobox.tk.call("ttk::combobox::PopdownWindow", combobox)
        except tk.TclError:
            return
        lb = f"{pop}.f.l"
        state = {"index": None}

        def beside(i):
            # just to the right of the open list, level with the entry under the mouse
            call = combobox.tk.call
            try:
                item_y = int(call(lb, "bbox", i)[1])
            except (tk.TclError, IndexError, ValueError):
                item_y = 0
            x = int(call("winfo", "rootx", pop)) + int(call("winfo", "width", pop)) + 6
            return x, int(call("winfo", "rooty", lb)) + item_y - 4

        def motion(y):
            try:
                i = int(combobox.tk.call(lb, "nearest", int(y)))
            except (tk.TclError, ValueError):
                return
            if i != state["index"]:
                state["index"] = i
                self.schedule(lambda i=i: text_for_index(i), lambda i=i: beside(i), owner=lb)

        def leave(*_):
            state["index"] = None
            self.hide()

        combobox.tk.call("bind", lb, "<Motion>", combobox.register(motion) + " %y")
        combobox.tk.call("bind", lb, "<Leave>", combobox.register(leave))
        combobox.tk.call("bind", pop, "<Unmap>", combobox.register(leave))
        combobox.bind("<<ComboboxSelected>>", lambda e: leave(), add="+")


def no_activate(win) -> None:
    """Windows: clicking or showing this borderless window never takes focus away from
    the main window (like a real menu or tooltip)."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def fade_in(win, ms: int = 150) -> None:
    """Show a (withdrawn) top-level window by fading it in, drawn in full first."""
    def alpha(a):
        try:
            win.attributes("-alpha", a)
        except tk.TclError:
            pass
    alpha(0.0)
    win.deiconify()
    win.update_idletasks()
    start = time.monotonic()

    def step():
        try:
            t = min(1.0, (time.monotonic() - start) * 1000 / ms)
            alpha(ease_out(t))
            if t < 1.0:
                win.after(12, step)
        except tk.TclError:
            pass
    step()


class Flyout:
    """A panel that drops down from a button: fades and slides in, fades out.

    It lives in its own borderless window (so it can fade) that follows the main
    window, and closes when the app is minimised or loses focus.
    """

    def __init__(self, root, bg: str):
        self.root = root
        self.win = w = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        try:
            w.transient(root)
        except tk.TclError:
            pass
        w.configure(background=bg)
        # map it once, invisibly, so Windows has made its window and we can mark it
        # as never taking focus
        self._alpha_on(w, 0.0)
        w.deiconify()
        no_activate(w)
        w.withdraw()
        self._alpha_on(w, 1.0)
        self.shown = False
        self._anchor = None          # function giving (right x, top y) on screen
        self._job = None
        root.bind("<Configure>", lambda e: e.widget is root and self._follow(), add="+")
        root.bind("<Unmap>", lambda e: e.widget is root and self.hide(animate=False), add="+")

    @staticmethod
    def _alpha_on(w, a):
        try:
            w.attributes("-alpha", a)
        except tk.TclError:
            pass

    def set_bg(self, bg):
        self.win.configure(background=bg)

    def _place(self, dy=0):
        right, top = self._anchor()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        self.win.geometry(f"{w}x{h}+{int(right - w)}+{int(top + dy)}")

    def _follow(self):
        if self.shown and self._job is None:
            self._place()

    def _cancel(self):
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _alpha(self, a):
        try:
            self.win.attributes("-alpha", a)
        except tk.TclError:
            pass

    def show(self, anchor, ms: int = 160, slide: int = 10):
        self._cancel()
        self._anchor = anchor
        self.shown = True
        self._alpha(0.0)
        self._place(-slide)
        self.win.deiconify()
        self.win.lift()
        self.win.update_idletasks()             # draw everything while still invisible
        start = time.monotonic()

        def step():
            t = min(1.0, (time.monotonic() - start) * 1000 / ms)
            e = ease_out(t)
            self._alpha(e)
            self._place(-slide * (1 - e))
            if t < 1.0:
                self._job = self.root.after(12, step)
            else:
                self._job = None
        step()

    def hide(self, animate=True, ms: int = 110):
        if not self.shown:
            return
        self.shown = False
        self._cancel()
        if not animate:
            self.win.withdraw()
            return
        start = time.monotonic()

        def step():
            t = min(1.0, (time.monotonic() - start) * 1000 / ms)
            self._alpha(1 - ease_out(t))
            if t < 1.0:
                self._job = self.root.after(12, step)
            else:
                self._job = None
                self.win.withdraw()
                self._alpha(1.0)
        step()

    def is_shown(self) -> bool:
        return self.shown
