"""SyncMate — keep any two folders in sync, continuously, in both directions."""
from __future__ import annotations

import argparse
import os
import queue
import shutil
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sv_ttk  # noqa: E402
from PIL import ImageTk  # noqa: E402

from icon import COLORS, make_icon, save_ico  # noqa: E402
from store import (SETTINGS_FILES, app_dir, chosen_data_dir, data_dir,  # noqa: E402
                   data_dir_problem, set_data_dir)
from version import VERSION  # noqa: E402
from widgets import SlideSwitch, dots, gear, plus  # noqa: E402
from cards import JobCard  # noqa: E402
from ui_common import IS_WIN, PALETTE, open_folder, pretty_path  # noqa: E402
from watcher import Controller, close_file_log  # noqa: E402

try:  # drag-and-drop of folders from File Explorer (optional; app works without it)
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover
    TkinterDnD = None

try:
    import darkdetect
except Exception:  # pragma: no cover
    darkdetect = None

APP = "SyncMate"
SHOW_FLAG = os.path.join(app_dir(), "show.flag")   # "please show your window"
QUIT_FLAG = os.path.join(app_dir(), "quit.flag")   # "please quit" (used by build.bat)

# ============================================================== Windows helpers

def single_instance(signal: str = SHOW_FLAG) -> bool:
    """True if we're the only SyncMate running. Otherwise pass `signal` to the running one."""
    if not IS_WIN:
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    single_instance.handle = k32.CreateMutexW(None, False, "Local\\SyncMate.SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        open(signal, "w").close()
        return False
    return True


def dpi_aware() -> None:
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def dark_titlebar(win: tk.Misc, dark: bool) -> None:
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else exe}" "{os.path.abspath(__file__)}" --background'


def get_autostart() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ============================================================== UI

class App:
    def __init__(self, ctrl: Controller, start_hidden: bool = False):
        self.ctrl = ctrl
        self.q: queue.Queue = queue.Queue()
        self.cards: dict = {}
        self.card_sig = None
        self.expanded = None   # key of the expanded card: a pair id, "new", or None
        self._draft = False    # is the "New folder sync" card showing?
        self.card_ids = None
        self.activity_win = None
        self.tray = None

        self.root = root = self._make_root()
        root.withdraw()
        root.title(APP)
        self.S = root.winfo_fpixels("1i") / 96  # display scale
        root.geometry(f"{self.px(820)}x{self.px(640)}")
        root.minsize(self.px(560), self.px(380))
        self._set_window_icon()
        self._heal_autostart()

        self.theme = self._wanted_theme()
        sv_ttk.set_theme(self.theme)
        self._fonts()
        self._styles()
        self._build()
        self.refresh()

        root.protocol("WM_DELETE_WINDOW", self.hide)
        self._start_tray()
        if not start_hidden or self.tray is None:
            self.show()
        root.after(200, self._poll)

    # ---------------------------------------------------------------- look & feel

    @staticmethod
    def _make_root() -> tk.Tk:
        if TkinterDnD is not None:
            try:
                return TkinterDnD.Tk()
            except Exception:
                pass
        return tk.Tk()

    @property
    def can_drop(self) -> bool:
        return hasattr(self.root, "drop_target_register")

    def drop_target(self, widget, on_paths, on_hover=None) -> None:
        """Let folders be dragged from File Explorer onto `widget`.
        on_paths(list_of_paths) runs on drop; on_hover(True/False) while dragging over."""
        if not self.can_drop:
            return
        def enter(e):
            if on_hover:
                on_hover(True)
            return e.action
        def leave(e):
            if on_hover:
                on_hover(False)
            return e.action
        def drop(e):
            if on_hover:
                on_hover(False)
            on_paths([os.path.normpath(p) for p in self.root.tk.splitlist(e.data)])
            return e.action
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", enter)
            widget.dnd_bind("<<DropPosition>>", lambda e: e.action)
            widget.dnd_bind("<<DropLeave>>", leave)
            widget.dnd_bind("<<Drop>>", drop)
        except Exception:
            pass

    def on_main_drop(self, paths) -> None:
        """Folders dropped on the main window: open (or fill) the new folder sync card."""
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders:
            self._flash_summary("Drop folders, not files, to add a folder sync.")
            return
        self.new_sync(prefill=folders)

    def _flash_summary(self, text: str, ms: int = 2500) -> None:
        self._summary_override = text
        self.refresh()

        def clear():
            if self._summary_override == text:
                self._summary_override = None
                self.refresh()
        self.root.after(ms, clear)

    @staticmethod
    def _heal_autostart():
        """Builds now live in their own 'build <version>' folders. If Start with Windows is
        on, point it at whichever SyncMate.exe is running now."""
        try:
            if get_autostart():
                set_autostart(True)
        except OSError:
            pass

    def _set_window_icon(self):
        """Title bar / taskbar icon. On Windows use a real multi-size .ico (crisp at 16 px);
        it's generated fresh from icon.py on every start, so it always matches this build."""
        self._icon_img = ImageTk.PhotoImage(make_icon("synced", 64))
        if IS_WIN:
            try:
                path = os.path.join(app_dir(), "window.ico")
                save_ico(path)
                self.root.iconbitmap(default=path)
                return
            except Exception:
                pass
        self.root.iconphoto(True, self._icon_img)

    def px(self, n: float) -> int:
        return int(round(n * self.S))

    def _wanted_theme(self) -> str:
        """The theme to show: the user's choice, or Windows' own setting when on Auto."""
        pref = self.ctrl.config.theme
        return pref if pref in ("light", "dark") else self._system_theme()

    def set_theme_pref(self, pref: str) -> None:
        self.ctrl.remember(theme=pref)
        t = self._wanted_theme()
        if t != self.theme:
            self._apply_theme(t)

    def _system_theme(self) -> str:
        try:
            return "dark" if darkdetect and darkdetect.theme() == "Dark" else "light"
        except Exception:
            return "light"

    def _fonts(self):
        fams = set(tkfont.families(self.root))
        body = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Cantarell",
                                 "DejaVu Sans") if f in fams), "TkDefaultFont")
        disp = next((f for f in ("Segoe UI Variable Display", "Segoe UI Semibold", body)
                     if f in fams), body)
        self.f_title = tkfont.Font(family=disp, size=18, weight="bold")
        self.f_body = tkfont.Font(family=body, size=10)
        self.f_bold = tkfont.Font(family=body, size=10, weight="bold")
        self.f_small = tkfont.Font(family=body, size=9)
        self.f_arrow = tkfont.Font(family=body, size=14)

    def _line_height(self) -> int:
        """Height of one line of button text; icons this tall keep buttons the same height."""
        try:
            return tkfont.nametofont("SunValleyBodyFont").metrics("linespace")
        except tk.TclError:
            return self.f_body.metrics("linespace")

    def _styles(self):
        pal = PALETTE[self.theme]
        s = ttk.Style()
        s.configure("Title.TLabel", font=self.f_title)
        s.configure("Body.TLabel", font=self.f_body)
        s.configure("Muted.TLabel", font=self.f_small, foreground=pal["muted"])
        s.configure("Name.TLabel", font=self.f_bold)
        s.configure("Arrow.TLabel", font=self.f_arrow, foreground=pal["muted"])
        s.configure("Error.TLabel", font=self.f_small, foreground=pal["error"])
        self.bg = self.card_bg = pal["bg"]
        s.configure("Inner.TFrame", background=self.card_bg)
        for name in ("Name", "Muted", "Arrow", "Error", "Body"):
            s.configure(f"Card.{name}.TLabel", background=self.card_bg)
        s.configure("Card.Name.TLabel", font=self.f_bold)
        s.configure("Card.Body.TLabel", font=self.f_body)
        s.configure("Card.Muted.TLabel", font=self.f_small, foreground=pal["muted"])
        s.configure("Card.Arrow.TLabel", font=self.f_arrow, foreground=pal["muted"])
        s.configure("Card.Error.TLabel", font=self.f_small, foreground=pal["error"])

    def _apply_theme(self, theme: str):
        self.theme = theme
        sv_ttk.set_theme(theme)
        self._styles()
        self.canvas.configure(background=self.bg)
        self._retint()
        self._theme_custom_widgets()
        dark_titlebar(self.root, theme == "dark")
        self.card_sig = None  # force rebuild
        self.refresh()

    def tint(self, w, role="muted"):
        """Colour a label's text. (Sun Valley overrides style colours, so set them directly.)"""
        w.configure(foreground=PALETTE[self.theme][role])
        w._tint_role = role
        return w

    def _retint(self, parent=None):
        for w in (parent or self.root).winfo_children():
            role = getattr(w, "_tint_role", None)
            if role:
                w.configure(foreground=PALETTE[self.theme][role])
            self._retint(w)

    # ---------------------------------------------------------------- layout

    def _build(self):
        root, px = self.root, self.px
        outer = ttk.Frame(root, padding=(px(24), px(20), px(24), px(14)))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, px(14)))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=APP, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.summary = self.tint(ttk.Label(head, text="", style="Muted.TLabel"))
        self.summary.grid(row=1, column=0, sticky="w", pady=(px(2), 0))
        self.add_btn = ttk.Button(head, text=" Add folder sync", style="Accent.TButton",
                                  compound="left", command=self.new_sync)
        self.add_btn.grid(row=0, column=1, rowspan=2, sticky="e")
        self.gear_btn = ttk.Button(head, style="Icon.TButton", command=self.toggle_settings)
        self.gear_btn.grid(row=0, column=2, rowspan=2, sticky="e", padx=(px(8), 0))

        # Scrollable list of pairs
        body = ttk.Frame(outer)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0, background=self.bg)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.list = ttk.Frame(self.canvas)
        self.list.columnconfigure(0, weight=1)
        self._win = self.canvas.create_window(0, 0, window=self.list, anchor="nw")
        self.list.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        root.bind_all("<MouseWheel>", self._wheel)
        root.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        root.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))

        foot = ttk.Frame(outer)
        foot.grid(row=2, column=0, sticky="ew", pady=(px(12), 0))
        foot.columnconfigure(0, weight=1)
        ttk.Button(foot, text="Sync now", command=lambda: self.ctrl.sync_now()).grid(
            row=0, column=1)

        self._build_settings()
        self._theme_custom_widgets()
        root.bind_all("<Button-1>", self._click_anywhere, add="+")
        root.bind("<Escape>", lambda e: self.hide_settings())
        self._summary_override = None
        self.drop_target(root, self.on_main_drop, self._main_hover)

    def _main_hover(self, over: bool) -> None:
        self._summary_override = "Drop to add a folder sync" if over else None
        self.refresh()

    # ---------------------------------------------------------------- settings panel

    def _build_settings(self):
        px = self.px
        p = self.settings = ttk.Frame(self.root, style="Card.TFrame", padding=(px(18), px(14)))
        p.columnconfigure(0, weight=1)
        ttk.Label(p, text="Settings", style="Card.Name.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, px(10)))

        self.auto_var = tk.BooleanVar(value=get_autostart())
        auto = ttk.Checkbutton(p, text="Start with Windows", style="Switch.TCheckbutton",
                               variable=self.auto_var, command=self._toggle_autostart)
        auto.grid(row=1, column=0, sticky="w")
        if not IS_WIN:
            auto.state(["disabled"])

        ttk.Separator(p).grid(row=2, column=0, sticky="ew", pady=px(12))
        self.tint(ttk.Label(p, text="Theme", style="Card.Body.TLabel"), "fg").grid(
            row=3, column=0, sticky="w", pady=(0, px(8)))
        self.theme_switch = SlideSwitch(
            p, [("auto", "Auto"), ("light", "Light"), ("dark", "Dark")],
            self.ctrl.config.theme, self.set_theme_pref, scale=self.S, font=self.f_body)
        self.theme_switch.grid(row=4, column=0, sticky="w")

        ttk.Separator(p).grid(row=5, column=0, sticky="ew", pady=px(12))
        self.tint(ttk.Label(p, text="Settings folder", style="Card.Body.TLabel"), "fg").grid(
            row=6, column=0, sticky="w")
        self.loc_label = self.tint(ttk.Label(p, text="", style="Card.Muted.TLabel",
                                             justify="left", wraplength=px(250)))
        self.loc_label.grid(row=7, column=0, sticky="w", pady=(px(2), px(8)))
        locb = tk.Frame(p, background=self.card_bg)
        locb.grid(row=8, column=0, sticky="w")
        ttk.Button(locb, text="Change…", command=self.change_settings_folder).pack(side="left")
        ttk.Button(locb, text="Open", command=lambda: open_folder(data_dir())).pack(
            side="left", padx=(px(8), 0))
        self.loc_default_btn = ttk.Button(locb, text="Use default",
                                          command=self.use_default_settings_folder)
        self.loc_default_btn.pack(side="left", padx=(px(8), 0))
        self._refresh_location()

        ttk.Separator(p).grid(row=9, column=0, sticky="ew", pady=px(12))
        ttk.Button(p, text="View activity log", command=self._activity_from_settings).grid(
            row=10, column=0, sticky="ew")
        self.tint(ttk.Label(p, text=f"{APP} {VERSION}", style="Card.Muted.TLabel")).grid(
            row=11, column=0, sticky="e", pady=(px(10), 0))

    # ---------------------------------------------------------------- settings folder

    def _refresh_location(self):
        custom = chosen_data_dir()
        where = data_dir()
        text = pretty_path(where) + ("" if custom else "  (default)")
        problem = data_dir_problem()
        if problem:
            text = problem + "\nUsing the default folder for now."
        self.loc_label.configure(text=text)
        if custom:
            self.loc_default_btn.pack(side="left", padx=(self.px(8), 0))
        else:
            self.loc_default_btn.pack_forget()

    def change_settings_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="Choose a folder for SyncMate's settings",
                                         initialdir=data_dir())
        if folder:
            self.move_settings(os.path.normpath(folder))

    def use_default_settings_folder(self):
        self.move_settings(app_dir())

    def move_settings(self, folder: str, use_existing: bool | None = None) -> bool:
        """Keep SyncMate's settings (your syncs, sync history and log) in `folder`.
        If that folder already has SyncMate settings, ask whether to use them or replace
        them. Returns True if the settings folder changed."""
        old = data_dir()
        if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(old)):
            return False
        for j in self.ctrl.jobs():
            for p in [j.main] + [m.path for m in j.members]:
                try:
                    inside = os.path.commonpath([os.path.normcase(os.path.abspath(folder)),
                                                 os.path.normcase(os.path.abspath(p))])
                except ValueError:
                    continue
                if inside == os.path.normcase(os.path.abspath(p)):
                    messagebox.showwarning(APP, "Please choose a folder that SyncMate isn't "
                                                "syncing. Its settings change all the time.",
                                           parent=self.root)
                    return False
        try:  # can we write there?
            probe = os.path.join(folder, ".syncmate-write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as e:
            messagebox.showerror(APP, f"Can't save settings in that folder:\n{e.strerror or e}",
                                 parent=self.root)
            return False
        if use_existing is None and os.path.exists(os.path.join(folder, "config.json")):
            answer = messagebox.askyesnocancel(
                APP, "That folder already has SyncMate settings.\n\n"
                     "Yes: use the settings that are already there\n"
                     "No: replace them with your current settings", parent=self.root)
            if answer is None:
                return False
            use_existing = answer
        use_existing = bool(use_existing)

        # Stop syncing, move the files, start again from the new folder.
        self.ctrl.stop()
        close_file_log()
        if not use_existing:
            for name in SETTINGS_FILES:
                src = os.path.join(old, name)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(folder, name))
        set_data_dir(folder)
        if not use_existing:   # it's a move: tidy the old copies away
            for name in SETTINGS_FILES + ("state.db-wal", "state.db-shm"):
                try:
                    os.remove(os.path.join(old, name))
                except OSError:
                    pass
        self.ctrl = Controller()
        self.ctrl.start()
        self.ctrl._log("", "info", f"Settings folder is now {data_dir()}")
        self.expanded, self._draft, self.card_sig = None, False, None
        self.theme_switch.select(
            next(i for i, (v, _) in enumerate(self.theme_switch.options)
                 if v == self.ctrl.config.theme), notify=False)
        self._apply_theme(self._wanted_theme())
        self._refresh_location()
        self.refresh()
        return True

    def _activity_from_settings(self):
        self.hide_settings()
        self.show_activity()

    def _theme_custom_widgets(self):
        pal = PALETTE[self.theme]
        line = self._line_height()
        self._gear_img = ImageTk.PhotoImage(gear(line, pal["fg"]))
        self._dots_img = ImageTk.PhotoImage(dots(line, pal["fg"]))
        self.gear_btn.configure(image=self._gear_img)
        ttk.Style().configure("Icon.TButton", padding=(6, 2, 6, 3))
        self._plus_img = ImageTk.PhotoImage(plus(line, pal["on_thumb"]))
        self.add_btn.configure(image=self._plus_img)
        self.theme_switch.set_colors({"bg": self.card_bg, "track": pal["track"],
                                      "thumb": pal["thumb"], "text": pal["muted"],
                                      "text_on": pal["on_thumb"]})

    def toggle_settings(self):
        if self.settings.winfo_ismapped():
            self.hide_settings()
            return
        b = self.gear_btn
        y = b.winfo_rooty() - self.root.winfo_rooty() + b.winfo_height() + self.px(6)
        self.settings.place(relx=1.0, x=-self.px(24), y=y, anchor="ne")
        self.settings.lift()

    def hide_settings(self):
        self.settings.place_forget()

    def _click_anywhere(self, e):
        """Close the settings panel when clicking somewhere else in the window."""
        if not self.settings.winfo_ismapped():
            return
        w = str(e.widget)
        if w.startswith(str(self.settings)) or w == str(self.gear_btn):
            return
        self.hide_settings()

    def _on_yscroll(self, lo, hi):
        self.scroll.set(lo, hi)
        if float(lo) <= 0 and float(hi) >= 1:
            self.scroll.grid_remove()
        else:
            self.scroll.grid(row=0, column=1, sticky="ns", padx=(self.px(6), 0))

    def _wheel(self, e):
        if self.scroll.winfo_ismapped():
            self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    # ---------------------------------------------------------------- pair cards

    def refresh(self):
        jobs = self.ctrl.jobs()
        snap = self.ctrl.snapshot()
        sig = [(j.id, j.main, tuple((m.id, m.path, m.mode, m.versions) for m in j.members))
               for j in jobs] + (["new"] if self.draft_open else [])
        if sig != self.card_sig:
            self._rebuild(jobs)
            self.card_sig = sig
        for p in jobs:
            st, card = snap.get(p.id), self.cards.get(p.id)
            if st is not None and card is not None:
                card.update(st, p)
        self._update_summary(jobs, snap)

    def _rebuild(self, jobs):
        draft = self.cards.get("new")
        kept = draft.values() if draft else None   # (main, [Member]) typed so far
        for w in self.list.winfo_children():
            w.destroy()
        self.cards = {}
        row = 0
        if self.draft_open:
            card = self.cards["new"] = JobCard(self, self.list, row)
            if kept:
                card.main.set(kept[0])
                for m in kept[1]:
                    card._add_row(m)
            row += 1
        for j in jobs:
            self.cards[j.id] = JobCard(self, self.list, row, j)
            row += 1
        if not self.cards:
            self._empty_state()
        if self.expanded in self.cards:
            self.cards[self.expanded].expand(reset=self.expanded != "new")
        else:
            self.expanded = None

    def _empty_state(self):
        px = self.px
        box = ttk.Frame(self.list, padding=(0, px(50)))
        box.grid(row=0, column=0)
        self._big_icon = ImageTk.PhotoImage(make_icon("synced", px(72)))
        ttk.Label(box, image=self._big_icon).pack(pady=(0, px(14)))
        ttk.Label(box, text="Keep any two folders in sync", style="Name.TLabel").pack()
        self.tint(ttk.Label(box, text="Pick a folder on your PC and one in OneDrive (or anywhere).\n"
                                      "Changes flow both ways, automatically.\n\n"
                                      "Tip: you can drag folders onto this window.",
                            style="Muted.TLabel", justify="center")).pack(pady=(px(4), px(16)))
        ttk.Button(box, text="Add your first folder sync", style="Accent.TButton",
                   command=self.new_sync).pack()

    # ---------------------------------------------------------------- expanding cards

    @property
    def draft_open(self) -> bool:
        return self._draft

    def set_expanded(self, key) -> None:
        """Expand one card in place (only one at a time); None collapses all."""
        if self.expanded == key:
            return
        if self.expanded == "new" and key != "new":
            self._draft = False          # leaving the new-sync card discards it
        old = self.cards.get(self.expanded)
        if old is not None:
            old.collapse()
        self.expanded = key
        self.refresh()
        card = self.cards.get(key)
        if card is not None:
            card.expand()
            self.ensure_visible(card.frame)

    def new_sync(self, prefill=()) -> None:
        """Show the blank 'New folder sync' card at the top of the list."""
        self.hide_settings()
        if not self._draft:
            self._draft = True
            self.set_expanded(None)
            self._draft = True
            self.expanded = "new"
            self.refresh()
        card = self.cards["new"]
        card.expand(reset=False)
        if prefill:
            card.fill(prefill)
        self.canvas.yview_moveto(0)
        card.main_box.focus_set()

    def close_draft(self) -> None:
        self._draft = False
        if self.expanded == "new":
            self.expanded = None
        self.refresh()

    def ensure_visible(self, widget) -> None:
        self.root.update_idletasks()
        total = max(1, self.list.winfo_height())
        top = widget.winfo_y()
        bottom = top + widget.winfo_height()
        view_top, view_bottom = (f * total for f in self.canvas.yview())
        view_h = view_bottom - view_top
        if bottom > view_bottom:
            # show the whole card if it fits, otherwise its top (the header stays visible)
            self.canvas.yview_moveto((top if bottom - top > view_h else bottom - view_h) / total)
        elif top < view_top:
            self.canvas.yview_moveto(top / total)

    def prepare_folders(self, main: str, members: list, ignore=None):
        """Check a job's folders can be synced; create ones that don't exist yet.
        Returns an error message, or None if all is well."""
        created = []
        if main and members:
            for p in [main] + list(members):
                if (p and not os.path.exists(p) and p not in created
                        and os.path.isdir(os.path.dirname(os.path.abspath(p)))):
                    created.append(p)
        try:
            for p in created:
                os.makedirs(p)
        except OSError as e:
            return f"Couldn't create “{p}”: {e.strerror}"
        problem = self.ctrl.validate(main, list(members), ignore=ignore)
        if problem:
            for p in created:
                try:
                    os.rmdir(p)
                except OSError:
                    pass
        return problem

    def confirm_delete(self, jid):
        job = next((j for j in self.ctrl.jobs() if j.id == jid), None)
        st = self.ctrl.snapshot().get(jid)
        if not job or not st:
            return
        m = next((m for m in job.members if m.id == st.empty_link), None)
        if m is None:
            return
        empty = job.main if st.empty_side == 0 else m.path
        other = m.path if st.empty_side == 0 else job.main
        if messagebox.askyesno(
                APP, f"{empty} is empty.\n\nDelete the same files in\n{other}?\n\n"
                     "They'll go to the Recycle Bin (or old versions).", icon="warning",
                parent=self.root):
            self.ctrl.resolve_empty(jid, True)

    def _update_summary(self, pairs, snap):
        if not pairs:
            text = "Add a folder sync and SyncMate keeps your folders identical."
        else:
            overall = self.ctrl.overall()
            states = {st.state for st in snap.values()}
            if overall == "syncing" and states.isdisjoint({"syncing", "starting"}):
                overall = "waiting"
            n = len(pairs)
            text = {
                "synced": f"{n} folder sync{'s' if n > 1 else ''} · everything is in sync",
                "syncing": "Syncing changes…",
                "waiting": "Some files are in use — they'll sync when they're closed",
                "paused": "All syncing is paused",
                "error": "Something needs your attention",
            }[overall]
        problem = data_dir_problem()
        if problem:
            text = problem + " · using the default settings folder"
        if getattr(self, "_summary_override", None):
            text = self._summary_override
        self.summary.configure(text=text)
        if self.tray is not None:
            state = self.ctrl.overall() if pairs else "synced"
            if state != getattr(self, "_tray_state", None):
                self._tray_state = state
                self.tray.icon = make_icon(state, 64)
                self.tray.title = f"{APP} — {text}"

    def show_activity(self):
        if self.activity_win and self.activity_win.winfo_exists():
            self.activity_win.lift()
            return
        self.activity_win = ActivityWindow(self)

    def _toggle_autostart(self):
        try:
            set_autostart(self.auto_var.get())
        except OSError as e:
            messagebox.showerror(APP, f"Couldn't change the startup setting:\n{e}")
            self.auto_var.set(get_autostart())

    # ---------------------------------------------------------------- window & tray

    def show(self):
        self.root.deiconify()
        dark_titlebar(self.root, self.theme == "dark")
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray is None:
            self.quit()
            return
        self.root.withdraw()
        if not self.ctrl.config.tray_hint_shown:
            self.ctrl.remember(tray_hint_shown=True)
            try:
                self.tray.notify("SyncMate keeps syncing in the background. "
                                 "Open it again from the tray icon.", APP)
            except Exception:
                pass

    def quit(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.ctrl.stop()
        self.root.destroy()

    def _start_tray(self):
        try:
            import pystray
        except Exception:
            return
        later = self.q.put

        def all_paused(_=None):
            ps = self.ctrl.jobs()
            return bool(ps) and all(p.paused for p in ps)

        def toggle_all():
            pause = not all_paused()
            for p in self.ctrl.jobs():
                self.ctrl.set_paused(p.id, pause)

        menu = pystray.Menu(
            pystray.MenuItem("Open SyncMate", lambda: later(self.show), default=True),
            pystray.MenuItem("Sync now", lambda: self.ctrl.sync_now()),
            pystray.MenuItem(lambda item: "Resume all" if all_paused() else "Pause all",
                             lambda: toggle_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: later(self.quit)),
        )
        try:
            self.tray = pystray.Icon(APP, make_icon("synced", 64), APP, menu)
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        if os.path.exists(QUIT_FLAG):
            try:
                os.remove(QUIT_FLAG)
            except OSError:
                pass
            self.quit()
            return
        if os.path.exists(SHOW_FLAG):
            try:
                os.remove(SHOW_FLAG)
            except OSError:
                pass
            self.show()
        now = time.monotonic()
        if now - getattr(self, "_last_theme_check", 0) > 3:
            self._last_theme_check = now
            t = self._wanted_theme()
            if t != self.theme:
                self._apply_theme(t)
        self.refresh()
        if self.activity_win and self.activity_win.winfo_exists():
            self.activity_win.refresh()
        busy = any(st.state in ("syncing", "starting") for st in self.ctrl.snapshot().values())
        self.root.after(200 if busy else 600, self._poll)

    def run(self):
        self.root.mainloop()


class ActivityWindow:
    def __init__(self, app: App):
        self.app = app
        px = app.px
        self.win = win = tk.Toplevel(app.root)
        win.title("SyncMate — Activity")
        win.geometry(f"{px(720)}x{px(420)}")
        f = ttk.Frame(win, padding=px(16))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(10)))
        ttk.Label(top, text="Activity", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Open log file", command=lambda: open_folder(data_dir())).pack(
            side="right")
        self.tree = ttk.Treeview(f, columns=("time", "what"), show="headings")
        self.tree.heading("time", text="Time", anchor="w")
        self.tree.heading("what", text="What happened", anchor="w")
        self.tree.column("time", width=px(90), stretch=False)
        self.tree.column("what", width=px(560))
        self.tree.tag_configure("error", foreground=PALETTE[app.theme]["error"])
        self.tree.tag_configure("warn", foreground=COLORS["syncing"])
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        self._seen = None
        dark_titlebar(win, app.theme == "dark")
        self.refresh()

    def winfo_exists(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def lift(self):
        self.win.deiconify()
        self.win.lift()

    def refresh(self):
        items = list(self.app.ctrl.activity)
        head = items[0] if items else None
        if head == self._seen:
            return
        self._seen = head
        self.tree.delete(*self.tree.get_children())
        for t, _pid, level, msg in items:
            self.tree.insert("", "end", values=(time.strftime("%H:%M:%S", time.localtime(t)), msg),
                             tags=(level,))


def main():
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--background", action="store_true", help="start hidden in the tray")
    ap.add_argument("--quit", action="store_true", help="ask a running SyncMate to close")
    args = ap.parse_args()
    if args.quit:
        single_instance(QUIT_FLAG)  # signals the running copy, if there is one
        return
    if not single_instance():
        return
    for flag in (SHOW_FLAG, QUIT_FLAG):  # leftovers from a previous run
        try:
            os.remove(flag)
        except OSError:
            pass
    dpi_aware()
    ctrl = Controller()
    ctrl.start()
    try:
        App(ctrl, start_hidden=args.background).run()
    finally:
        if ctrl._thread.is_alive():
            ctrl.stop()


if __name__ == "__main__":
    main()
