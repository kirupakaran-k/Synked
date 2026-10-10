"""A sync-job card: shows status and live progress, and expands in place to edit.

A job has one Main folder and one or more connected folders; each connected folder
has its own sync type and its own old-versions setting. The same card, blank, is used
for adding a new folder sync.
"""
from __future__ import annotations

import math
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk

from store import (KEEP_DAY_STOPS, KEEP_DAYS_HELP, MODE_HELP, MODE_LABELS, MODES,
                   OLD_FILES_HELP, OLD_FILES_LABELS, Member, nearest_stop, parse_days)
from ui_common import (ONEDRIVE, PALETTE, STATE_COLOR, describe, elide, folder_name,
                       open_folder, pretty_path, progress_detail)
from widgets import FlowRow, ImageButton

ARROW_KIND = {"two-way": "both", "mirror": "right", "update": "right", "collect": "left"}
SHORT_MODE = {"two-way": "Two-way", "mirror": "Mirror", "update": "Update",
              "collect": "Collect"}


BOX_HINT = ("Type a folder path, use Browse, or drag a folder here from File Explorer. "
            "A folder that doesn't exist yet is created.")


def set_if_changed(widget, **options) -> None:
    """configure() only the options whose value is different."""
    changed = {}
    for k, v in options.items():
        try:
            same = str(widget.cget(k)) == str(v)
        except tk.TclError:
            same = False
        if not same:
            changed[k] = v
    if changed:
        widget.configure(**changed)


def _px(value) -> int:
    try:
        return int(float(str(value) or 0))
    except ValueError:
        return 0


def chars_for(widget, text: str) -> int:
    """Width (in Tk's character units) a ttk entry/dropdown needs to show `text` in full."""
    try:
        f = tkfont.nametofont(str(widget.cget("font")) or "TkTextFont")
    except tk.TclError:
        f = tkfont.nametofont("TkTextFont")
    return math.ceil((f.measure(text) + 2) / f.measure("0"))


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class JobCard:
    def __init__(self, app, parent, row: int, job=None):
        self.app, self.job = app, job
        self.is_new = job is None
        self.key = "new" if self.is_new else job.id
        px, bg = app.px, app.card_bg
        self.rows: list = []        # connected-folder rows in the editor
        self._activity_sig = None
        self._rows_for = None       # which job version the editor rows were filled from

        f = self.frame = ttk.Frame(parent, style="Card.TFrame", padding=(px(16), px(12)))
        f.grid(row=row, column=0, sticky="ew", pady=(0, px(10)))
        f.columnconfigure(1, weight=1)
        self._click = []  # widgets that toggle the card when clicked

        # ---- header: Main on top, each connected folder below it ----------------
        #   ●  Language                                   ⋮
        #      Main · D:\KK
        #      [⇅]  Language
        #           Two-way · OneDrive
        #      [↓]  New folder
        #           Update · D:\
        self.dot = tk.Canvas(f, width=px(10), height=px(10), highlightthickness=0, background=bg)
        self.dot.create_oval(1, 1, px(10) - 1, px(10) - 1, tags="dot")
        self.dot.grid(row=0, column=0, sticky="nw", padx=(0, px(12)), pady=(px(5), 0))
        self.badges = []            # (label, mode) for each connected folder
        if self.is_new:
            box = tk.Frame(f, background=bg)
            box.grid(row=0, column=1, sticky="ew")
            ttk.Label(box, text="New folder sync", style="Card.Name.TLabel").pack(anchor="w")
            sub = app.tint(ttk.Label(box, text="Choose a Main folder and the folders to keep in "
                                               "sync with it", style="Card.Muted.TLabel"))
            sub.pack(anchor="w")
            box.bind("<Configure>", lambda e: e.width > 50 and _px(sub.cget("wraplength")) != e.width
                     and sub.configure(wraplength=e.width), add="+")
        else:
            head = self.head = tk.Frame(f, background=bg)
            head.grid(row=0, column=1, sticky="ew")
            head.columnconfigure(2, weight=1)
            self._text_block(head, 0, 0, folder_name(job.main),
                             "Main · " + elide(pretty_path(os.path.dirname(job.main)) or job.main, 60),
                             columnspan=3)
            # a tree: a line runs down from Main and branches off to each connected folder
            self.branches = []
            for i, m in enumerate(job.members, start=1):
                branch = tk.Canvas(head, width=px(26), height=1, background=bg,
                                   highlightthickness=0, borderwidth=0)
                branch.grid(row=i, column=0, sticky="ns")
                badge = tk.Label(head, background=bg, borderwidth=0, padx=0, pady=0)
                badge.grid(row=i, column=1, sticky="w", pady=(px(8), 0), padx=(0, px(12)))
                self.badges.append((badge, m.mode))
                self.branches.append((branch, badge, i == len(job.members)))
                self._text_block(head, i, 2, folder_name(m.path),
                                 f"{SHORT_MODE[m.mode]} · "
                                 + elide(pretty_path(os.path.dirname(m.path)) or m.path, 56),
                                 pady=(px(8), 0))
                self._click += [badge, branch]
                branch.bind("<Configure>", lambda e: self._draw_tree(), add="+")
            self._click += [head, f, self.dot]
            # the ⋮ "more" button: just the icon, no box (a soft highlight on hover)
            self.more = tk.Label(f, image=app._dots_img, background=bg, cursor="hand2",
                                 padx=px(8), pady=px(6), borderwidth=0)
            self.more.grid(row=0, column=2, sticky="ne", padx=(px(10), 0))
            self.more.bind("<Button-1>", lambda e: self.toggle())
            self.more.bind("<Enter>", lambda e: self.more.configure(
                background=PALETTE[self.app.theme]["track"]))
            self.more.bind("<Leave>", lambda e: self.more.configure(background=self.bg))
        self.retheme()

        # ---- status, live progress -----------------------------------------
        if not self.is_new:
            self.status = app.tint(ttk.Label(f, text="", style="Card.Muted.TLabel"))
            self.status.grid(row=1, column=1, sticky="w", pady=(px(10), 0))
            self.bar = ttk.Progressbar(f, mode="determinate", maximum=1000)
            self.bar.grid(row=2, column=1, sticky="ew", pady=(px(6), 0))
            self.detail = app.tint(ttk.Label(f, text="", style="Card.Muted.TLabel"))
            self.detail.grid(row=3, column=1, sticky="w", pady=(px(3), 0))
            self.bar.grid_remove()
            self.detail.grid_remove()
            self._bar_shown = False
            self._click += [self.status, self.detail]
            f.bind("<Configure>", lambda e: self._wrap_status(), add="+")

            # "folder suddenly empty" choices
            self.fix = tk.Frame(f, background=bg)
            self.fix.grid(row=4, column=1, sticky="w", pady=(px(8), 0))
            ttk.Button(self.fix, text="Restore files", style="Accent.TButton",
                       command=lambda: app.ctrl.resolve_empty(job.id, False)).pack(
                side="left", padx=(0, px(8)))
            self.fix_delete = ttk.Button(self.fix, text="Apply the deletion",
                                         command=lambda: app.confirm_delete(job.id))
            self.fix_delete.pack(side="left")
            self.fix.grid_remove()

        for w in self._click:
            w.bind("<Button-1>", lambda e: self.toggle(), add="+")
            try:
                w.configure(cursor="hand2")
            except tk.TclError:
                pass

        self._build_editor()

    def _wrap_status(self) -> None:
        """Long status lines wrap inside the card instead of running off its edge."""
        try:
            width = self.frame.grid_bbox(1, 0, 1, 0)[2]
        except (tk.TclError, IndexError):
            return
        if width > 50:
            for lab in (self.status, self.detail):
                if _px(lab.cget("wraplength")) != width:
                    lab.configure(wraplength=width)

    @property
    def bg(self) -> str:
        return self.app.card_bg

    def retheme(self) -> None:
        """Swap in the current theme's icons (colours are handled by the app)."""
        if self.is_new:
            c = PALETTE[self.app.theme]["thumb"]
            self.dot.itemconfigure("dot", fill=c, outline=c)
            return
        self.more.configure(image=self.app._dots_img)
        for badge, mode in self.badges:
            badge.configure(image=self.app._badge_imgs[ARROW_KIND[mode]])
        self._draw_tree()

    def _draw_tree(self) -> None:
        """The tree lines: down from Main, with a rounded branch to each folder's badge."""
        if not getattr(self, "branches", None):
            return
        px = self.app.px
        color = PALETTE[self.app.theme]["line"]
        lw = max(1, round(px(1.6)))
        x, r = px(7), px(7)                           # the trunk, and the corner's radius
        try:
            for canvas, badge, last in self.branches:
                canvas.delete("all")
                w, h = canvas.winfo_width(), canvas.winfo_height()
                mid = badge.winfo_y() - canvas.winfo_y() + badge.winfo_height() / 2
                if h <= 1 or mid <= r:
                    continue
                canvas.create_line(x, 0, x, mid - r, fill=color, width=lw)
                canvas.create_arc(x, mid - 2 * r, x + 2 * r, mid, start=180, extent=90,
                                  style="arc", outline=color, width=lw)
                canvas.create_line(x + r, mid, w, mid, fill=color, width=lw)
                if not last:                          # the trunk carries on to the next one
                    canvas.create_line(x, mid - r, x, h, fill=color, width=lw)
        except tk.TclError:
            pass

    def _text_block(self, parent, row, col, title, sub, columnspan=1, pady=0):
        box = tk.Frame(parent, background=self.bg)
        box.grid(row=row, column=col, columnspan=columnspan, sticky="w", pady=pady)
        a = ttk.Label(box, text=title, style="Card.Name.TLabel")
        a.pack(anchor="w")
        b = self.app.tint(ttk.Label(box, text=sub, style="Card.Muted.TLabel"))
        b.pack(anchor="w")
        self._click += [box, a, b]

    # ======================================================================= editor
    def _build_editor(self):
        app, px, bg = self.app, self.app.px, self.bg
        # The editor opens like a curtain: it sits in a "curtain" frame whose height is
        # animated from 0 to the editor's full height (and back when closing).
        self.curtain = tk.Frame(self.frame, background=bg, height=1)
        self.curtain.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.curtain.pack_propagate(False)
        self.curtain.grid_remove()
        self._curtain_job = None
        self.opened = False
        ed = self.editor = tk.Frame(self.frame, background=bg)
        ed.columnconfigure(0, weight=1)
        ttk.Separator(ed).grid(row=0, column=0, sticky="ew", pady=(px(12), px(12)))

        # Main folder
        self._section(ed, 1, "Main folder", "the master copy")
        mrow = FlowRow(ed, bg=bg, gap=px(8), vgap=px(6))
        mrow.grid(row=2, column=0, sticky="ew")
        self.main = tk.StringVar()
        self.main_box = mrow.add(ttk.Entry(mrow, textvariable=self.main),
                                 stretch=True, min_width=px(240), sticky="ew")
        btns = tk.Frame(mrow, background=bg)
        ttk.Button(btns, text="Browse", command=lambda: self._browse(self.main, "Main folder")
                   ).pack(side="left")
        ttk.Button(btns, text="Open", command=lambda: self._open(self.main.get())).pack(
            side="left", padx=(px(8), 0))
        mrow.add(btns)
        app.tooltip.attach(self.main_box, lambda: BOX_HINT)
        app.drop_target(self.main_box, lambda paths: self.on_drop(None, paths),
                        lambda over: self.main_box.state(["focus"] if over else ["!focus"]))
        self.main.trace_add("write", lambda *_: self._suggest())

        # Connected folders
        self._section(ed, 3, "Connected folders", "each one syncs with Main its own way",
                      pady=(px(14), px(4)))
        self.rows_frame = tk.Frame(ed, background=bg)
        self.rows_frame.grid(row=4, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)
        add_row = tk.Frame(ed, background=bg)
        add_row.grid(row=5, column=0, sticky="w", pady=(px(8), 0))
        ttk.Button(add_row, text="＋ Add folder", command=lambda: self._add_row(focus=True)).pack(
            side="left")

        # Recent activity (existing jobs)
        if not self.is_new:
            self._section(ed, 7, "Recent activity", "", pady=(px(14), px(4)))
            self.activity = tk.Frame(ed, background=bg)
            self.activity.grid(row=8, column=0, sticky="ew")
            self.activity.columnconfigure(1, weight=1)
            self._activity_msgs = []
            self.activity.bind("<Configure>", lambda e: self._wrap_activity(), add="+")

        self.err = app.tint(ttk.Label(ed, text="", style="Card.Error.TLabel"), "error")
        self.err.grid(row=9, column=0, sticky="w", pady=(px(6), 0))

        # actions (left: pause / sync now; right: remove / cancel / apply)
        self.actions = act = FlowRow(ed, bg=bg, gap=px(8), vgap=px(8))
        act.grid(row=10, column=0, sticky="ew", pady=(px(10), 0))
        if not self.is_new:
            left = tk.Frame(act, background=bg)
            self.pause_btn = ttk.Button(left, text="Pause", command=self._toggle_pause)
            self.pause_btn.pack(side="left", padx=(0, px(8)))
            self.sync_btn = ttk.Button(left, text="Sync now",
                                       command=lambda: app.ctrl.sync_now(self.job.id))
            self.sync_btn.pack(side="left")
            act.add(left)
        right = tk.Frame(act, background=bg)
        if not self.is_new:
            ttk.Button(right, text="Remove", command=self._ask_remove).pack(
                side="left", padx=(0, px(8)))
        ttk.Button(right, text="Cancel", command=self.cancel).pack(side="left", padx=(0, px(8)))
        ttk.Button(right, text="Start syncing" if self.is_new else "Apply",
                   style="Accent.TButton", command=self.apply).pack(side="left")
        act.add(right, stretch=True, sticky="e")

        # inline "are you sure?" for Remove
        self.confirm = cf = FlowRow(ed, bg=bg, gap=px(8), vgap=px(8))
        cf.grid(row=10, column=0, sticky="ew", pady=(px(10), 0))
        cf.add(app.tint(ttk.Label(cf, text="Stop this folder sync? Your files stay where they are.",
                                  style="Card.Body.TLabel"), "fg"))
        sure = tk.Frame(cf, background=bg)
        ttk.Button(sure, text="Keep syncing", command=self._hide_confirm).pack(
            side="left", padx=(0, px(8)))
        ttk.Button(sure, text="Stop syncing", style="Accent.TButton",
                   command=self._remove).pack(side="left")
        cf.add(sure, stretch=True, sticky="e")
        cf.grid_remove()

        self.main_box.bind("<Return>", lambda e: self.apply())
        self.main_box.bind("<Escape>", lambda e: self.cancel())

    def _section(self, ed, row, title, hint, pady=(0, 4)):
        top = FlowRow(ed, bg=self.bg, gap=self.app.px(10), vgap=0)
        top.grid(row=row, column=0, sticky="ew", pady=pady)
        top.add(ttk.Label(top, text=title, style="Card.Name.TLabel"), sticky="sw")
        if hint:
            top.add(self.app.tint(ttk.Label(top, text=hint, style="Card.Muted.TLabel")),
                    sticky="sw")

    # ---- connected-folder rows -------------------------------------------------
    def _add_row(self, member: Member | None = None, focus=False) -> dict:
        app, px, bg = self.app, self.app.px, self.bg
        fr = tk.Frame(self.rows_frame, background=bg)
        fr.grid(row=len(self.rows), column=0, sticky="ew", pady=(0 if not self.rows else px(10), 0))
        fr.columnconfigure(0, weight=1)
        keep = bool(member and member.versions)
        days0 = member.keep_days if member else 30
        row = {"frame": fr, "id": member.id if member else None,
               "path": tk.StringVar(value=member.path if member else ""),
               "mode": tk.StringVar(value=MODE_LABELS[member.mode if member else "two-way"]),
               "versions": tk.StringVar(value=OLD_FILES_LABELS[keep]),
               "keep_days": max(0, days0),
               "pos": tk.DoubleVar(value=nearest_stop(days0)),
               "days_text": tk.StringVar()}

        # [folder path ..............] [Browse] [Open] [✕]
        top = FlowRow(fr, bg=bg, gap=px(8), vgap=px(6))
        top.grid(row=0, column=0, sticky="ew")
        box = top.add(ttk.Combobox(top, textvariable=row["path"], values=self._suggestions()),
                      stretch=True, min_width=px(240), sticky="ew")
        row["box"] = box
        btns = tk.Frame(top, background=bg)
        ttk.Button(btns, text="Browse", command=lambda: self._browse(row["path"], "folder")).pack(
            side="left")
        ttk.Button(btns, text="Open", command=lambda: self._open(row["path"].get())).pack(
            side="left", padx=(px(8), 0))
        remove = ImageButton(btns, app._remove_imgs, lambda: self._remove_row(row), bg=bg)
        remove.pack(side="left", padx=(px(8), 0))
        app.tooltip.attach(remove, lambda: "Remove this folder from the sync "
                                           "(its files stay where they are)")
        row["remove"] = remove
        top.add(btns)

        # [Two-way ▾] [Keep old versions ▾] for [slider] [30] days
        opts = FlowRow(fr, bg=bg, gap=px(8), vgap=px(6))
        opts.grid(row=1, column=0, sticky="ew", pady=(px(6), 0))
        mode_box = opts.add(ttk.Combobox(opts, textvariable=row["mode"], state="readonly",
                                         values=[MODE_LABELS[m] for m in MODES]))
        old_box = opts.add(ttk.Combobox(opts, textvariable=row["versions"], state="readonly",
                                        values=[OLD_FILES_LABELS[False], OLD_FILES_LABELS[True]]))
        days = tk.Frame(opts, background=bg)
        for_label = app.tint(ttk.Label(days, text="for", style="Card.Body.TLabel"), "fg")
        for_label.pack(side="left", padx=(px(2), 0))
        last = len(KEEP_DAY_STOPS) - 1
        slider = ttk.Scale(days, from_=0, to=last, orient="horizontal", length=px(110),
                           variable=row["pos"])
        slider.pack(side="left", padx=(px(10), px(10)))
        days_entry = ttk.Entry(days, textvariable=row["days_text"], justify="center")
        days_entry.configure(width=chars_for(days_entry, "forever"))
        days_entry.pack(side="left")
        unit = app.tint(ttk.Label(days, text="days", style="Card.Body.TLabel"), "fg")
        opts.add(days, visible=keep)
        row.update(days=days, mode_box=mode_box, old_box=old_box, slider=slider,
                   days_entry=days_entry, opts=opts)

        def show_days(n):
            """Put n days (0 = forever) in the text box; the unit hides for 'forever'."""
            row["days_text"].set("forever" if n == 0 else str(n))
            if n == 0:
                unit.pack_forget()
            else:
                unit.configure(text="day" if n == 1 else "days")
                unit.pack(side="left", padx=(px(6), 0))
            opts.relayout()

        def on_slide(v):
            # dragging: snap to the nearest stop; the far right is "forever"
            n = KEEP_DAY_STOPS[int(round(float(v)))]
            if n != row["keep_days"]:
                row["keep_days"] = n
                show_days(n)
        slider.configure(command=on_slide)
        slider.bind("<ButtonRelease-1>",
                    lambda e: row["pos"].set(round(row["pos"].get())), add="+")

        def typed(*_):
            """Typing a number keeps it exactly and moves the slider to the closest stop."""
            n = parse_days(row["days_text"].get())
            if n is not None and n != row["keep_days"]:
                row["keep_days"] = n
                row["pos"].set(nearest_stop(n))
                if n == 0:
                    unit.pack_forget()
                else:
                    unit.configure(text="day" if n == 1 else "days")
                    unit.pack(side="left", padx=(px(6), 0))
        row["days_text"].trace_add("write", typed)

        def settle(_=None):
            show_days(row["keep_days"])     # tidy what was typed, or undo a bad entry
        row["settle"] = settle
        days_entry.bind("<FocusOut>", settle, add="+")
        days_entry.bind("<Return>", lambda e: (settle(), self.apply()))
        days_entry.bind("<Escape>", lambda e: self.cancel())
        show_days(row["keep_days"])

        def fit_width(box, text):
            """Size a dropdown to its current text (the arrow's room is added by Tk)."""
            box.configure(width=max(4, chars_for(box, text)))
            opts.relayout()

        def on_old_files(*_):
            fit_width(old_box, row["versions"].get())
            opts.set_visible(days, row["versions"].get() == OLD_FILES_LABELS[True])
        row["versions"].trace_add("write", on_old_files)
        on_old_files()
        row["mode"].trace_add("write", lambda *_: fit_width(mode_box, row["mode"].get()))
        fit_width(mode_box, row["mode"].get())

        # hover hints: on each choice in the open lists, and on the closed boxes
        mode_of = {v: k for k, v in MODE_LABELS.items()}
        tip = app.tooltip
        tip.attach(mode_box, lambda: MODE_HELP[mode_of.get(row["mode"].get(), "two-way")])
        tip.attach_list(mode_box, lambda i: MODE_HELP[MODES[i]] if 0 <= i < len(MODES) else "")
        tip.attach(old_box, lambda: OLD_FILES_HELP[row["versions"].get() == OLD_FILES_LABELS[True]])
        tip.attach_list(old_box, lambda i: OLD_FILES_HELP.get(bool(i), "") if i in (0, 1) else "")
        for w in (slider, days_entry, for_label, unit):
            tip.attach(w, lambda: KEEP_DAYS_HELP)
        tip.attach(box, lambda: BOX_HINT)
        app.drop_target(box, lambda paths: self.on_drop(row, paths),
                        lambda over: box.state(["focus"] if over else ["!focus"]))
        box.bind("<Return>", lambda e: self.apply())
        box.bind("<Escape>", lambda e: self.cancel())
        self.rows.append(row)
        if focus:
            box.focus_set()
            self.app.ensure_visible(self.frame)
        return row

    def _remove_row(self, row) -> None:
        row["frame"].destroy()
        self.rows.remove(row)
        for i, r in enumerate(self.rows):
            r["frame"].grid(row=i, pady=(0 if i == 0 else self.app.px(10), 0))

    def _clear_rows(self) -> None:
        for r in self.rows:
            r["frame"].destroy()
        self.rows = []

    def _suggestions(self):
        """Offer OneDrive\\<name of Main> in each folder's dropdown (never filled in automatically)."""
        main = self.main.get().strip().strip('"') if hasattr(self, "main") else ""
        name = os.path.basename(os.path.normpath(main)) if main else ""
        return [os.path.join(ONEDRIVE, name)] if ONEDRIVE and name else []

    def _suggest(self):
        values = self._suggestions()
        for r in self.rows:
            r["box"].configure(values=values)

    # ======================================================================= state
    def toggle(self):
        if self.is_new:
            return
        self.app.set_expanded(None if self.app.expanded == self.key else self.key)

    def expand(self, reset: bool = True):
        if reset and not self.is_new:
            self.main.set(self.job.main)
            self._fill_rows(self.job.members)
        if not self.rows:
            self._add_row()
        self.err.configure(text="")
        self._hide_confirm()
        self._activity_sig = None
        self._curtain(True)

    def collapse(self, then=None):
        self._curtain(False, then)

    # ---- the curtain ---------------------------------------------------------
    OPEN_MS, CLOSE_MS = 260, 200

    def _curtain(self, opening: bool, then=None) -> None:
        """Slide the editor open (down) or closed (up)."""
        c, ed = self.curtain, self.editor
        if self._curtain_job is not None:
            self.app.root.after_cancel(self._curtain_job)
            self._curtain_job = None
        if not opening and not c.winfo_ismapped() and not self.opened:
            if then:
                then()
            return
        start = c.winfo_height() if c.winfo_ismapped() else 0
        # while moving, the editor keeps its full size and the curtain clips it
        ed.pack_forget()
        c.pack_propagate(False)
        c.configure(height=max(1, start))
        c.grid()
        ed.place(in_=c, x=0, y=0, relwidth=1)
        self.opened = opening
        t0 = time.monotonic()
        ms = self.OPEN_MS if opening else self.CLOSE_MS

        def step():
            self._curtain_job = None
            try:
                t = min(1.0, (time.monotonic() - t0) * 1000 / ms)
                if opening:
                    e = 1 - (1 - t) ** 3                 # fast start, gentle stop
                    full = ed.winfo_reqheight()
                    c.configure(height=max(1, round(start + (full - start) * e)))
                    self.app.ensure_visible(self.frame)  # the list follows the card down
                else:
                    e = t * t * t                        # gentle start, quick finish
                    c.configure(height=max(1, round(start * (1 - e))))
                if t < 1.0:
                    self._curtain_job = self.app.root.after(12, step)
                    return
                ed.place_forget()
                if opening:                              # from now on it just fits its content
                    ed.pack(in_=c, fill="x")
                    c.pack_propagate(True)
                else:
                    c.grid_remove()
                if then:
                    then()
            except tk.TclError:
                pass                                     # the card went away meanwhile
        step()

    def _fill_rows(self, members) -> None:
        """Show the job's folders in the editor, re-using the rows already built when they
        are the same folders (building widgets is the slow part of opening a card)."""
        if [r["id"] for r in self.rows] != [m.id for m in members]:
            self._clear_rows()
            for m in members:
                self._add_row(m)
            return
        for r, m in zip(self.rows, members):
            r["path"].set(m.path)
            r["mode"].set(MODE_LABELS[m.mode])
            r["versions"].set(OLD_FILES_LABELS[bool(m.versions)])
            r["keep_days"] = max(0, m.keep_days)
            r["pos"].set(nearest_stop(m.keep_days))
            r["settle"]()

    def update(self, st, job):
        """Refresh status, progress, buttons and recent activity from the latest snapshot."""
        self.job = job
        # only touch what changed: every change makes Tk lay out and redraw again
        color = STATE_COLOR.get(st.state, STATE_COLOR["paused"])
        if color != getattr(self, "_dot_color", None):
            self._dot_color = color
            self.dot.itemconfigure("dot", fill=color, outline=color)
        bad = st.state in ("error", "empty")
        set_if_changed(self.status, text=describe(st, job),
                       foreground=PALETTE[self.app.theme]["error" if bad else "muted"])
        p = st.progress
        busy = st.state == "syncing" and p is not None and p.items_total > 0
        if busy:
            set_if_changed(self.bar, value=int(p.fraction * 1000))
            set_if_changed(self.detail, text=progress_detail(p))
        if busy != self._bar_shown:   # track it ourselves: Tk maps widgets lazily
            for w in (self.bar, self.detail):
                w.grid() if busy else w.grid_remove()
            self._bar_shown = busy
        empty = st.state == "empty"
        if empty != getattr(self, "_fix_shown", None):
            self._fix_shown = empty
            self.fix.grid() if empty else self.fix.grid_remove()
        set_if_changed(self.pause_btn, text="Resume" if job.paused else "Pause")
        if job.paused != getattr(self, "_paused_shown", None):
            self._paused_shown = job.paused
            self.sync_btn.state(["disabled"] if job.paused else ["!disabled"])
        if self.app.expanded == self.key:
            self._show_activity()

    def _show_activity(self):
        items = self.app.ctrl.recent_activity(self.job.id, n=6)
        sig = tuple(items)
        if sig == self._activity_sig:
            return
        self._activity_sig = sig
        for w in self.activity.winfo_children():
            w.destroy()
        self._activity_msgs = []
        pal = PALETTE[self.app.theme]
        if not items:
            self.app.tint(ttk.Label(self.activity, text="Nothing yet.",
                                    style="Card.Muted.TLabel")).grid(row=0, column=0, sticky="w")
            return
        for i, (t, level, msg) in enumerate(items):
            when = time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")
            self.app.tint(ttk.Label(self.activity, text=when, style="Card.Muted.TLabel")).grid(
                row=i, column=0, sticky="nw", padx=(0, self.app.px(12)))
            lab = ttk.Label(self.activity, text=elide(msg, 110), style="Card.Muted.TLabel",
                            foreground=pal["error"] if level == "error" else pal["muted"])
            lab._tint_role = "error" if level == "error" else "muted"
            lab.grid(row=i, column=1, sticky="w")
            self._activity_msgs.append(lab)
        self._wrap_activity()

    def _wrap_activity(self) -> None:
        """Wrap activity lines to the space beside the times."""
        try:
            bbox = self.activity.grid_bbox(1, 0)
        except tk.TclError:
            return
        width = self.activity.winfo_width() - bbox[0]
        if width > 50:
            for lab in self._activity_msgs:
                if _px(lab.cget("wraplength")) != width:
                    lab.configure(wraplength=width, justify="left")

    # ======================================================================= actions
    def values(self):
        main = self.main.get().strip().strip('"')
        members = []
        mode_of = {v: k for k, v in MODE_LABELS.items()}
        for r in self.rows:
            path = r["path"].get().strip().strip('"')
            if not path:
                continue
            versions = r["versions"].get() == OLD_FILES_LABELS[True]
            r["settle"]()
            keep_days = r["keep_days"]
            members.append(Member(path=path, mode=mode_of.get(r["mode"].get(), "two-way"),
                                  versions=versions, keep_days=keep_days,
                                  **({"id": r["id"]} if r["id"] else {})))
        return main, members

    def fill(self, folders) -> None:
        """Put dropped folders into empty boxes: Main first, then connected folders."""
        for p in folders:
            if not self.main.get().strip():
                self.main.set(p)
                continue
            empty = next((r for r in self.rows if not r["path"].get().strip()), None)
            (empty or self._add_row())["path"].set(p)

    def on_drop(self, row, paths) -> None:
        """Folders dropped on a box (row=None means the Main box). Extra folders are
        added as more connected folders."""
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders:
            self.err.configure(text="That's a file. Drop a folder instead.")
            return
        self.err.configure(text="")
        first, rest = folders[0], folders[1:]
        (self.main if row is None else row["path"]).set(first)
        for p in rest:
            empty = next((r for r in self.rows if not r["path"].get().strip()), None)
            (empty or self._add_row())["path"].set(p)

    def _browse(self, var, what):
        start = var.get().strip() if os.path.isdir(var.get().strip()) else None
        p = filedialog.askdirectory(parent=self.app.root, initialdir=start, title=f"Choose {what}")
        if p:
            var.set(os.path.normpath(p))

    def _open(self, path):
        path = path.strip().strip('"')
        if os.path.isdir(path):
            open_folder(path)

    def _toggle_pause(self):
        self.app.ctrl.set_paused(self.job.id, not self.job.paused)

    def _ask_remove(self):
        self.actions.grid_remove()
        self.confirm.grid()

    def _hide_confirm(self):
        self.confirm.grid_remove()
        self.actions.grid()

    def _remove(self):
        self.app.ctrl.remove_job(self.job.id)
        self.app.set_expanded(None)

    def cancel(self):
        if self.is_new:
            self.collapse(then=self.app.close_draft)
        else:
            self.app.set_expanded(None)

    def _unchanged(self, main, members) -> bool:
        j = self.job
        if not same_path(main or ".", j.main) or len(members) != len(j.members):
            return False
        return all(m.id == o.id and same_path(m.path, o.path) and m.mode == o.mode
                   and m.versions == o.versions and (not m.versions or m.keep_days == o.keep_days)
                   for m, o in zip(members, j.members))

    def apply(self):
        main, members = self.values()
        if not self.is_new and self._unchanged(main, members):
            self.app.set_expanded(None)
            return
        problem = self.app.prepare_folders(main, [m.path for m in members],
                                           ignore=None if self.is_new else self.job.id)
        if problem:
            self.err.configure(text=problem)
            return
        if self.is_new:
            self.app.ctrl.add_job(main, members)
            self.app.close_draft()
        else:
            self.app.ctrl.update_job(self.job.id, main, members)
            self.app.set_expanded(None)
