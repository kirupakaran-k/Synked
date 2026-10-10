"""A sync-job card: shows status and live progress, and expands in place to edit.

A job has one Main folder and one or more connected folders; each connected folder
has its own sync type and its own old-versions setting. The same card, blank, is used
for adding a new folder sync.
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, ttk

from store import MODE_LABELS, MODES, VERSION_CHOICES, Member, version_choice, version_label
from ui_common import (ONEDRIVE, PALETTE, STATE_COLOR, describe, elide, folder_name,
                       open_folder, pretty_path, progress_detail)

ARROWS = {"two-way": "⇄", "mirror": "→", "update": "→", "collect": "←"}
SHORT_MODE = {"two-way": "Two-way", "mirror": "Mirror", "update": "Update",
              "collect": "Collect"}


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class JobCard:
    def __init__(self, app, parent, row: int, job=None):
        self.app, self.job = app, job
        self.is_new = job is None
        self.key = "new" if self.is_new else job.id
        px, bg = app.px, app.card_bg
        self.bg = bg
        self.rows: list = []        # connected-folder rows in the editor
        self._activity_sig = None

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
            app.tint(ttk.Label(box, text="Choose a Main folder and the folders to keep in sync "
                                         "with it", style="Card.Muted.TLabel")).pack(anchor="w")
        else:
            self._header_side(f, 1, folder_name(job.main),
                              "Main · " + elide(pretty_path(os.path.dirname(job.main)) or job.main, 36))
            ms = job.members
            if len(ms) == 1:
                m = ms[0]
                self._header_side(f, 3, folder_name(m.path),
                                  f"{SHORT_MODE[m.mode]} · "
                                  + elide(pretty_path(os.path.dirname(m.path)) or m.path, 34))
                arrow_text = ARROWS[m.mode]
            else:
                self._header_side(f, 3, f"{len(ms)} folders",
                                  elide(", ".join(folder_name(m.path) for m in ms), 44))
                arrow_text = "⇄"
            arrow = app.tint(ttk.Label(f, text=arrow_text, style="Card.Arrow.TLabel"))
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

    def _header_side(self, f, col, title, sub):
        box = tk.Frame(f, background=self.bg)
        box.grid(row=0, column=col, sticky="ew")
        a = ttk.Label(box, text=title, style="Card.Name.TLabel")
        a.pack(anchor="w")
        b = self.app.tint(ttk.Label(box, text=sub, style="Card.Muted.TLabel"))
        b.pack(anchor="w")
        self._click += [box, a, b]

    # ======================================================================= editor
    def _build_editor(self):
        app, px, bg = self.app, self.app.px, self.bg
        ed = self.editor = tk.Frame(self.frame, background=bg)
        ed.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(px(12), 0))
        ed.columnconfigure(0, weight=1)
        ttk.Separator(ed).grid(row=0, column=0, sticky="ew", pady=(0, px(12)))

        # Main folder
        self._section(ed, 1, "Main folder", "the master copy")
        mrow = tk.Frame(ed, background=bg)
        mrow.grid(row=2, column=0, sticky="ew")
        mrow.columnconfigure(0, weight=1)
        self.main = tk.StringVar()
        self.main_box = ttk.Entry(mrow, textvariable=self.main)
        self.main_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(mrow, text="Browse…", command=lambda: self._browse(self.main, "Main folder")
                   ).grid(row=0, column=1, padx=(px(8), 0))
        ttk.Button(mrow, text="Open", command=lambda: self._open(self.main.get())).grid(
            row=0, column=2, padx=(px(8), 0))
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

        tip = ("Two-way: changes flow both ways; if both sides changed, the newer one wins. "
               "Mirror: the folder becomes an exact copy of Main (its own changes are undone). "
               "Update / Collect: new and changed files are copied one way and nothing is ever "
               "deleted, good for backups. Replaced or removed files go to the Recycle Bin, or are "
               "kept in the folder's hidden .syncmate-versions folder: one dated folder per "
               "sync, kept for the days you choose. Folders that don't exist yet are created; "
               "you can also drag folders onto the boxes.")
        self.tip = app.tint(ttk.Label(ed, text=tip, style="Card.Muted.TLabel", justify="left"))
        self.tip.grid(row=6, column=0, sticky="w", pady=(px(12), 0))
        ed.bind("<Configure>", lambda e: self.tip.configure(wraplength=max(200, e.width - 4)))

        # Recent activity (existing jobs)
        if not self.is_new:
            self._section(ed, 7, "Recent activity", "", pady=(px(14), px(4)))
            self.activity = tk.Frame(ed, background=bg)
            self.activity.grid(row=8, column=0, sticky="ew")
            self.activity.columnconfigure(1, weight=1)

        self.err = app.tint(ttk.Label(ed, text="", style="Card.Error.TLabel"), "error")
        self.err.grid(row=9, column=0, sticky="w", pady=(px(6), 0))

        # actions
        self.actions = act = tk.Frame(ed, background=bg)
        act.grid(row=10, column=0, sticky="ew", pady=(px(10), 0))
        act.columnconfigure(2, weight=1)
        if not self.is_new:
            self.pause_btn = ttk.Button(act, text="Pause", command=self._toggle_pause)
            self.pause_btn.grid(row=0, column=0, padx=(0, px(8)))
            self.sync_btn = ttk.Button(act, text="Sync now",
                                       command=lambda: app.ctrl.sync_now(self.job.id))
            self.sync_btn.grid(row=0, column=1)
            ttk.Button(act, text="Remove", command=self._ask_remove).grid(
                row=0, column=3, padx=(0, px(8)))
        ttk.Button(act, text="Cancel", command=self.cancel).grid(row=0, column=4, padx=(0, px(8)))
        ttk.Button(act, text="Start syncing" if self.is_new else "Apply",
                   style="Accent.TButton", command=self.apply).grid(row=0, column=5)

        # inline "are you sure?" for Remove
        self.confirm = cf = tk.Frame(ed, background=bg)
        cf.grid(row=10, column=0, sticky="ew", pady=(px(10), 0))
        cf.columnconfigure(0, weight=1)
        app.tint(ttk.Label(cf, text="Stop this folder sync? Your files stay where they are.",
                           style="Card.Body.TLabel"), "fg").grid(row=0, column=0, sticky="w")
        ttk.Button(cf, text="Keep syncing", command=self._hide_confirm).grid(
            row=0, column=1, padx=(px(8), px(8)))
        ttk.Button(cf, text="Stop syncing", style="Accent.TButton",
                   command=self._remove).grid(row=0, column=2)
        cf.grid_remove()

        self.main_box.bind("<Return>", lambda e: self.apply())
        self.main_box.bind("<Escape>", lambda e: self.cancel())
        ed.grid_remove()

    def _section(self, ed, row, title, hint, pady=(0, 4)):
        top = tk.Frame(ed, background=self.bg)
        top.grid(row=row, column=0, sticky="w", pady=pady)
        ttk.Label(top, text=title, style="Card.Name.TLabel").pack(side="left")
        if hint:
            self.app.tint(ttk.Label(top, text="   " + hint, style="Card.Muted.TLabel")).pack(side="left")

    # ---- connected-folder rows -------------------------------------------------
    def _add_row(self, member: Member | None = None, focus=False) -> dict:
        app, px, bg = self.app, self.app.px, self.bg
        fr = tk.Frame(self.rows_frame, background=bg)
        fr.grid(row=len(self.rows), column=0, sticky="ew", pady=(0 if not self.rows else px(10), 0))
        fr.columnconfigure(0, weight=1)
        row = {"frame": fr, "id": member.id if member else None,
               "path": tk.StringVar(value=member.path if member else ""),
               "mode": tk.StringVar(value=MODE_LABELS[member.mode if member else "two-way"]),
               "versions": tk.StringVar(value=version_label(bool(member and member.versions),
                                                            member.keep_days if member else 30))}
        box = ttk.Combobox(fr, textvariable=row["path"], values=self._suggestions())
        box.grid(row=0, column=0, sticky="ew")
        row["box"] = box
        ttk.Button(fr, text="Browse…", command=lambda: self._browse(row["path"], "folder")).grid(
            row=0, column=1, padx=(px(8), 0))
        ttk.Button(fr, text="Open", command=lambda: self._open(row["path"].get())).grid(
            row=0, column=2, padx=(px(8), 0))
        ttk.Button(fr, text="✕", width=3, command=lambda: self._remove_row(row)).grid(
            row=0, column=3, padx=(px(8), 0))
        opts = tk.Frame(fr, background=bg)
        opts.grid(row=1, column=0, columnspan=4, sticky="w", pady=(px(6), 0))
        ttk.Combobox(opts, textvariable=row["mode"], state="readonly", width=38,
                     values=[MODE_LABELS[m] for m in MODES]).pack(side="left")
        ttk.Combobox(opts, textvariable=row["versions"], state="readonly", width=28,
                     values=[label for _, label in VERSION_CHOICES]).pack(
            side="left", padx=(px(8), 0))
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
            self._clear_rows()
            for m in self.job.members:
                self._add_row(m)
        if not self.rows:
            self._add_row()
        self.err.configure(text="")
        self._hide_confirm()
        self._activity_sig = None
        self.editor.grid()

    def collapse(self):
        self.editor.grid_remove()

    def update(self, st, job):
        """Refresh status, progress, buttons and recent activity from the latest snapshot."""
        self.job = job
        color = STATE_COLOR.get(st.state, STATE_COLOR["paused"])
        self.dot.itemconfigure("dot", fill=color, outline=color)
        bad = st.state in ("error", "empty")
        self.status.configure(text=describe(st, job),
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
            self.fix.grid()
        else:
            self.fix.grid_remove()
        self.pause_btn.configure(text="Resume" if job.paused else "Pause")
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

    # ======================================================================= actions
    def values(self):
        main = self.main.get().strip().strip('"')
        members = []
        mode_of = {v: k for k, v in MODE_LABELS.items()}
        for r in self.rows:
            path = r["path"].get().strip().strip('"')
            if not path:
                continue
            versions, keep_days = version_choice(r["versions"].get())
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
            self.app.close_draft()
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
