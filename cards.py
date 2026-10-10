"""A folder-pair card: shows status and live progress, and expands in place to edit.

The same card, blank, is used for adding a new folder sync.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk

from ui_common import (ONEDRIVE, PALETTE, STATE_COLOR, describe, elide, folder_name,
                       open_folder, pretty_path, progress_detail)


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class PairCard:
    def __init__(self, app, parent, row: int, pair=None):
        self.app, self.pair = app, pair
        self.is_new = pair is None
        self.key = "new" if self.is_new else pair.id
        px, bg = app.px, app.card_bg
        self.bg = bg

        f = self.frame = ttk.Frame(parent, style="Card.TFrame", padding=(px(16), px(12)))
        f.grid(row=row, column=0, sticky="ew", pady=(0, px(10)))
        f.columnconfigure(1, weight=1, uniform="side")
        f.columnconfigure(3, weight=1, uniform="side")
        self._click = []  # widgets that toggle the card when clicked

        # ---- header -------------------------------------------------------
        self.dot = tk.Canvas(f, width=px(10), height=px(10), highlightthickness=0, background=bg)
        self.dot.create_oval(1, 1, px(10) - 1, px(10) - 1, tags="dot")
        self.dot.grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        if self.is_new:
            c = PALETTE[app.theme]["thumb"]
            self.dot.itemconfigure("dot", fill=c, outline=c)
            box = tk.Frame(f, background=bg)
            box.grid(row=0, column=1, columnspan=3, sticky="ew")
            ttk.Label(box, text="New folder sync", style="Card.Name.TLabel").pack(anchor="w")
            app.tint(ttk.Label(box, text="Choose two folders to keep identical",
                               style="Card.Muted.TLabel")).pack(anchor="w")
        else:
            for col, path in ((1, pair.left), (3, pair.right)):
                box = tk.Frame(f, background=bg)
                box.grid(row=0, column=col, sticky="ew")
                a = ttk.Label(box, text=folder_name(path), style="Card.Name.TLabel")
                a.pack(anchor="w")
                b = app.tint(ttk.Label(box, text=elide(pretty_path(os.path.dirname(path)) or path, 44),
                                       style="Card.Muted.TLabel"))
                b.pack(anchor="w")
                self._click += [box, a, b]
            arrow = app.tint(ttk.Label(f, text="⇄", style="Card.Arrow.TLabel"))
            arrow.grid(row=0, column=2, padx=px(14))
            self.more = ttk.Button(f, image=app._dots_img, style="Icon.TButton",
                                   command=self.toggle)
            self.more.grid(row=0, column=4, sticky="e", padx=(px(10), 0))
            self._click += [f, self.dot, arrow]

        # ---- status, live progress -----------------------------------------
        if not self.is_new:
            self.status = app.tint(ttk.Label(f, text="", style="Card.Muted.TLabel"))
            self.status.grid(row=1, column=1, columnspan=3, sticky="w", pady=(px(8), 0))
            self.bar = ttk.Progressbar(f, mode="determinate", maximum=1000)
            self.bar.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(px(6), 0))
            self.detail = app.tint(ttk.Label(f, text="", style="Card.Muted.TLabel"))
            self.detail.grid(row=3, column=1, columnspan=3, sticky="w", pady=(px(3), 0))
            self.bar.grid_remove()
            self.detail.grid_remove()
            self._bar_shown = False
            self._click += [self.status, self.detail]

            # "folder suddenly empty" choices
            self.fix = tk.Frame(f, background=bg)
            self.fix.grid(row=4, column=1, columnspan=3, sticky="w", pady=(px(8), 0))
            self.fix_restore = ttk.Button(self.fix, text="Restore files", style="Accent.TButton",
                                          command=lambda: app.ctrl.resolve_empty(pair.id, False))
            self.fix_restore.pack(side="left", padx=(0, px(8)))
            self.fix_delete = ttk.Button(self.fix, command=lambda: app.confirm_delete(pair.id))
            self.fix_delete.pack(side="left")
            self.fix.grid_remove()

        for w in self._click:
            w.bind("<Button-1>", lambda e: self.toggle(), add="+")
            try:
                w.configure(cursor="hand2")
            except tk.TclError:
                pass

        self._build_editor()

    # ======================================================================= editor
    def _build_editor(self):
        app, px, bg = self.app, self.app.px, self.bg
        ed = self.editor = tk.Frame(self.frame, background=bg)
        ed.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(px(12), 0))
        ed.columnconfigure(0, weight=1)
        ttk.Separator(ed).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, px(12)))

        self.a = tk.StringVar()
        self.b = tk.StringVar()
        self.a_box = self._field(ed, 1, "Folder A", "This folder…", self.a, "a")
        self.b_box = self._field(ed, 3, "Folder B", "…stays identical to this one", self.b, "b",
                                 combo=True)
        self.a.trace_add("write", lambda *_: self._suggest())

        tip = ("Tip: choose a folder inside OneDrive as Folder B to back Folder A up to the "
               "cloud. A folder that doesn't exist yet will be created. You can also drag "
               "folders from File Explorer onto the boxes.\n"
               "If both already contain files, they're merged: where a file differs, the "
               "newer one is kept and the older one goes to the Recycle Bin."
               if self.is_new else
               "Changing a folder merges the two sides afresh: where a file differs, the "
               "newer one is kept and the older one goes to the Recycle Bin.")
        self.tip = app.tint(ttk.Label(ed, text=tip, style="Card.Muted.TLabel", justify="left"))
        self.tip.grid(row=5, column=0, columnspan=4, sticky="w", pady=(px(12), 0))
        ed.bind("<Configure>", lambda e: self.tip.configure(wraplength=max(200, e.width - 4)))

        self.err = app.tint(ttk.Label(ed, text="", style="Card.Error.TLabel"), "error")
        self.err.grid(row=6, column=0, columnspan=4, sticky="w", pady=(px(6), 0))

        # actions
        self.actions = act = tk.Frame(ed, background=bg)
        act.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(px(10), 0))
        act.columnconfigure(2, weight=1)
        if not self.is_new:
            self.pause_btn = ttk.Button(act, text="Pause", command=self._toggle_pause)
            self.pause_btn.grid(row=0, column=0, padx=(0, px(8)))
            self.sync_btn = ttk.Button(act, text="Sync now",
                                       command=lambda: app.ctrl.sync_now(self.pair.id))
            self.sync_btn.grid(row=0, column=1)
            ttk.Button(act, text="Remove", command=self._ask_remove).grid(
                row=0, column=3, padx=(0, px(8)))
        ttk.Button(act, text="Cancel", command=self.cancel).grid(row=0, column=4, padx=(0, px(8)))
        ttk.Button(act, text="Start syncing" if self.is_new else "Apply",
                   style="Accent.TButton", command=self.apply).grid(row=0, column=5)

        # inline "are you sure?" for Remove
        self.confirm = cf = tk.Frame(ed, background=bg)
        cf.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(px(10), 0))
        cf.columnconfigure(0, weight=1)
        app.tint(ttk.Label(cf, text="Stop syncing this pair? Your files stay where they are.",
                           style="Card.Body.TLabel"), "fg").grid(row=0, column=0, sticky="w")
        ttk.Button(cf, text="Keep syncing", command=self._hide_confirm).grid(
            row=0, column=1, padx=(px(8), px(8)))
        ttk.Button(cf, text="Stop syncing", style="Accent.TButton",
                   command=self._remove).grid(row=0, column=2)
        cf.grid_remove()

        for box in (self.a_box, self.b_box):
            box.bind("<Return>", lambda e: self.apply())
            box.bind("<Escape>", lambda e: self.cancel())
        ed.grid_remove()

    def _field(self, ed, row, label, hint, var, which, combo=False):
        app, px, bg = self.app, self.app.px, self.bg
        top = tk.Frame(ed, background=bg)
        top.grid(row=row, column=0, columnspan=4, sticky="w", pady=(px(4) if row > 1 else 0, px(4)))
        ttk.Label(top, text=label, style="Card.Name.TLabel").pack(side="left")
        app.tint(ttk.Label(top, text="   " + hint, style="Card.Muted.TLabel")).pack(side="left")
        box = (ttk.Combobox(ed, textvariable=var, values=[]) if combo
               else ttk.Entry(ed, textvariable=var))
        box.grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(ed, text="Browse…", command=lambda: self._browse(which)).grid(
            row=row + 1, column=1, padx=(px(8), 0))
        if not self.is_new:
            ttk.Button(ed, text="Open", command=lambda: self._open(which)).grid(
                row=row + 1, column=2, padx=(px(8), 0))
        app.drop_target(box, lambda paths: self.on_drop(which, paths),
                        lambda over: box.state(["focus"] if over else ["!focus"]))
        return box

    # ======================================================================= state
    def toggle(self):
        if self.is_new:
            return
        self.app.set_expanded(None if self.app.expanded == self.key else self.key)

    def expand(self, reset: bool = True):
        if reset and not self.is_new:
            self.a.set(self.pair.left)
            self.b.set(self.pair.right)
        self.err.configure(text="")
        self._hide_confirm()
        self.editor.grid()

    def collapse(self):
        self.editor.grid_remove()

    def update(self, st, pair):
        """Refresh status, progress and buttons from the latest snapshot."""
        self.pair = pair
        color = STATE_COLOR.get(st.state, STATE_COLOR["paused"])
        self.dot.itemconfigure("dot", fill=color, outline=color)
        bad = st.state in ("error", "empty")
        self.status.configure(text=describe(st, pair),
                              foreground=PALETTE[self.app.theme]["error" if bad else "muted"])
        p = st.progress
        busy = st.state == "syncing" and p is not None and p.items_total > 0
        if busy:
            self.bar.configure(value=int(p.fraction * 1000))
            self.detail.configure(text=progress_detail(p))
        if busy != self._bar_shown:   # track it ourselves: Tk maps widgets lazily
            for w in (self.bar, self.detail):
                w.grid() if busy else w.grid_remove()
            self._bar_shown = busy
        if st.state == "empty":
            other = pair.right if st.empty_side == 0 else pair.left
            self.fix_delete.configure(text=f"Delete in {elide(folder_name(other), 20)} too")
            self.fix.grid()
        else:
            self.fix.grid_remove()
        self.pause_btn.configure(text="Resume" if pair.paused else "Pause")
        self.sync_btn.state(["disabled"] if pair.paused else ["!disabled"])

    # ======================================================================= actions
    def values(self):
        return self.a.get().strip().strip('"'), self.b.get().strip().strip('"')

    def fill(self, folders) -> None:
        """Put dropped folders into the empty boxes (A first)."""
        for p in folders:
            if not self.a.get().strip():
                self.a.set(p)
            elif not self.b.get().strip():
                self.b.set(p)

    def on_drop(self, which: str, paths) -> None:
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders:
            self.err.configure(text="That's a file. Drop a folder instead.")
            return
        self.err.configure(text="")
        if len(folders) >= 2:
            self.a.set(folders[0])
            self.b.set(folders[1])
        else:
            (self.a if which == "a" else self.b).set(folders[0])

    def _suggest(self):
        """Offer OneDrive\\<name of Folder A> in Folder B's dropdown (never filled in automatically)."""
        a = self.a.get().strip().strip('"')
        name = os.path.basename(os.path.normpath(a)) if a else ""
        self.b_box.configure(values=[os.path.join(ONEDRIVE, name)] if ONEDRIVE and name else [])

    def _browse(self, which):
        var = self.a if which == "a" else self.b
        start = var.get().strip() if os.path.isdir(var.get().strip()) else None
        p = filedialog.askdirectory(parent=self.app.root, initialdir=start,
                                    title=f"Choose Folder {which.upper()}")
        if p:
            var.set(os.path.normpath(p))

    def _open(self, which):
        path = (self.a if which == "a" else self.b).get().strip()
        if not os.path.isdir(path):
            path = self.pair.left if which == "a" else self.pair.right
        open_folder(path)

    def _toggle_pause(self):
        self.app.ctrl.set_paused(self.pair.id, not self.pair.paused)

    def _ask_remove(self):
        self.actions.grid_remove()
        self.confirm.grid()

    def _hide_confirm(self):
        self.confirm.grid_remove()
        self.actions.grid()

    def _remove(self):
        self.app.ctrl.remove_pair(self.pair.id)
        self.app.set_expanded(None)

    def cancel(self):
        if self.is_new:
            self.app.close_draft()
        else:
            self.app.set_expanded(None)

    def apply(self):
        a, b = self.values()
        if not self.is_new and same_path(a or ".", self.pair.left) and same_path(b or ".", self.pair.right):
            self.app.set_expanded(None)
            return
        problem = self.app.prepare_folders(a, b, ignore=None if self.is_new else self.pair.id)
        if problem:
            self.err.configure(text=problem)
            return
        if self.is_new:
            self.app.ctrl.add_pair(a, b)
            self.app.close_draft()
        else:
            self.app.ctrl.update_pair(self.pair.id, a, b)
            self.app.set_expanded(None)
