"""SyncMate — keep any two folders in sync, continuously, in both directions."""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sv_ttk  # noqa: E402
from PIL import ImageTk  # noqa: E402

from icon import COLORS, make_icon  # noqa: E402
from store import data_dir  # noqa: E402
from widgets import SlideSwitch, gear, plus  # noqa: E402
from watcher import Controller  # noqa: E402

try:
    import darkdetect
except Exception:  # pragma: no cover
    darkdetect = None

IS_WIN = os.name == "nt"
APP = "SyncMate"
SHOW_FLAG = os.path.join(data_dir(), "show.flag")   # "please show your window"
QUIT_FLAG = os.path.join(data_dir(), "quit.flag")   # "please quit" (used by build.bat)
ONEDRIVE = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""

PALETTE = {
    # bg matches the Sun Valley theme's own background colours
    "light": {"bg": "#fafafa", "fg": "#1c1c1c", "muted": "#5F6670", "error": "#C42B1C",
              "track": "#E9E9EC", "thumb": "#005FB8", "on_thumb": "#FFFFFF"},
    "dark": {"bg": "#1c1c1c", "fg": "#F2F2F2", "muted": "#A3A9B2", "error": "#FF8A80",
             "track": "#2E2E2E", "thumb": "#57C8FF", "on_thumb": "#101010"},
}
STATE_COLOR = {
    "synced": COLORS["synced"], "syncing": COLORS["syncing"], "starting": COLORS["syncing"],
    "waiting": COLORS["syncing"], "paused": COLORS["paused"], "error": COLORS["error"],
    "empty": COLORS["error"],
}


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


def open_folder(path: str) -> None:
    if IS_WIN:
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


# ============================================================== formatting

def pretty_path(path: str) -> str:
    if ONEDRIVE and os.path.normcase(path).startswith(os.path.normcase(ONEDRIVE)):
        path = "OneDrive" + path[len(ONEDRIVE):]
    return path


def elide(s: str, n: int = 46) -> str:
    return s if len(s) <= n else s[: n // 2 - 1] + "…" + s[-(n // 2):]


def ago(t: float) -> str:
    if not t:
        return ""
    s = time.time() - t
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return time.strftime("%d %b, %H:%M", time.localtime(t))


def describe(st) -> str:
    if st.state == "synced":
        return f"In sync · {ago(st.last_sync)}"
    if st.state in ("syncing", "starting"):
        return "Syncing…"
    if st.state == "paused":
        return "Paused"
    return st.message.replace(ONEDRIVE, "OneDrive") if ONEDRIVE else st.message


# ============================================================== UI

class App:
    def __init__(self, ctrl: Controller, start_hidden: bool = False):
        self.ctrl = ctrl
        self.q: queue.Queue = queue.Queue()
        self.cards: dict = {}
        self.card_ids = None
        self.activity_win = None
        self.tray = None

        self.root = root = tk.Tk()
        root.withdraw()
        root.title(APP)
        self.S = root.winfo_fpixels("1i") / 96  # display scale
        root.geometry(f"{self.px(760)}x{self.px(500)}")
        root.minsize(self.px(560), self.px(380))
        self._icon_img = ImageTk.PhotoImage(make_icon("synced", 64))
        root.iconphoto(True, self._icon_img)

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
        self.card_ids = None  # force rebuild
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
        self.add_btn = ttk.Button(head, text=" Add folder pair", style="Accent.TButton",
                                  compound="left", command=self.add_dialog)
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
        ttk.Button(p, text="View activity log", command=self._activity_from_settings).grid(
            row=6, column=0, sticky="ew")

    def _activity_from_settings(self):
        self.hide_settings()
        self.show_activity()

    def _theme_custom_widgets(self):
        pal = PALETTE[self.theme]
        line = self._line_height()
        self._gear_img = ImageTk.PhotoImage(gear(line, pal["fg"]))
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
        pairs = self.ctrl.pairs()
        snap = self.ctrl.snapshot()
        ids = [p.id for p in pairs]
        if ids != self.card_ids:
            self._rebuild(pairs)
            self.card_ids = ids
        for p in pairs:
            st = snap.get(p.id)
            if st is None or p.id not in self.cards:
                continue
            c = self.cards[p.id]
            color = STATE_COLOR.get(st.state, COLORS["paused"])
            c["dot"].itemconfigure("dot", fill=color, outline=color)
            bad = st.state in ("error", "empty")
            c["status"].configure(text=describe(st),
                                  foreground=PALETTE[self.theme]["error" if bad else "muted"])
            if st.state == "empty":
                if not c["fix"].winfo_ismapped():
                    other = p.right if st.empty_side == 0 else p.left
                    c["fix_restore"].configure(text="Restore files")
                    c["fix_delete"].configure(text=f"Delete in {elide(os.path.basename(other) or other, 20)} too")
                    c["fix"].grid()
            elif c["fix"].winfo_ismapped():
                c["fix"].grid_remove()
            c["pause_label"] = "Resume" if p.paused else "Pause"
        self._update_summary(pairs, snap)

    def _update_summary(self, pairs, snap):
        if not pairs:
            text = "Add a pair of folders and SyncMate keeps them identical."
        else:
            overall = self.ctrl.overall()
            states = {st.state for st in snap.values()}
            if overall == "syncing" and states.isdisjoint({"syncing", "starting"}):
                overall = "waiting"
            n = len(pairs)
            text = {
                "synced": f"{n} folder pair{'s' if n > 1 else ''} · everything is in sync",
                "syncing": "Syncing changes…",
                "waiting": "Some files are in use — they'll sync when they're closed",
                "paused": "All syncing is paused",
                "error": "Something needs your attention",
            }[overall]
        self.summary.configure(text=text)
        if self.tray is not None:
            state = self.ctrl.overall() if pairs else "synced"
            if state != getattr(self, "_tray_state", None):
                self._tray_state = state
                self.tray.icon = make_icon(state, 64)
                self.tray.title = f"{APP} — {text}"

    def _rebuild(self, pairs):
        for w in self.list.winfo_children():
            w.destroy()
        self.cards = {}
        px = self.px
        if not pairs:
            self._empty_state()
            return
        for i, p in enumerate(pairs):
            card = ttk.Frame(self.list, style="Card.TFrame", padding=(px(16), px(12)))
            card.grid(row=i, column=0, sticky="ew", pady=(0, px(10)))
            card.columnconfigure(1, weight=1, uniform="side")
            card.columnconfigure(3, weight=1, uniform="side")

            dot = tk.Canvas(card, width=px(10), height=px(10), highlightthickness=0,
                            background=self.card_bg)
            dot.create_oval(1, 1, px(10) - 1, px(10) - 1, tags="dot")
            dot.grid(row=0, column=0, sticky="w", padx=(0, px(12)))

            for col, path in ((1, p.left), (3, p.right)):
                box = tk.Frame(card, background=self.card_bg, highlightthickness=0, bd=0)
                box.grid(row=0, column=col, sticky="ew")
                name = os.path.basename(path.rstrip("\\/")) or path
                ttk.Label(box, text=name, style="Card.Name.TLabel").pack(anchor="w")
                self.tint(ttk.Label(box, text=elide(pretty_path(os.path.dirname(path)) or path, 44),
                                    style="Card.Muted.TLabel")).pack(anchor="w")
            self.tint(ttk.Label(card, text="⇄", style="Card.Arrow.TLabel")).grid(
                row=0, column=2, padx=px(14))

            more = ttk.Button(card, text="⋯", width=3, style="Ghost.TButton")
            more.grid(row=0, column=4, sticky="e", padx=(px(10), 0))
            more.configure(command=lambda b=more, pid=p.id: self._menu(b, pid))

            status = self.tint(ttk.Label(card, text="", style="Card.Muted.TLabel"))
            status.grid(row=1, column=1, columnspan=3, sticky="w", pady=(px(8), 0))

            fix = tk.Frame(card, background=self.card_bg, highlightthickness=0, bd=0)
            fix.grid(row=2, column=1, columnspan=3, sticky="w", pady=(px(8), 0))
            r = ttk.Button(fix, style="Accent.TButton",
                           command=lambda pid=p.id: self.ctrl.resolve_empty(pid, False))
            r.pack(side="left", padx=(0, px(8)))
            d = ttk.Button(fix, command=lambda pid=p.id: self._confirm_delete(pid))
            d.pack(side="left")
            fix.grid_remove()
            self.cards[p.id] = dict(dot=dot, status=status, fix=fix, fix_restore=r,
                                    fix_delete=d, pause_label="Pause")

    def _empty_state(self):
        px = self.px
        box = ttk.Frame(self.list, padding=(0, px(50)))
        box.grid(row=0, column=0)
        self._big_icon = ImageTk.PhotoImage(make_icon("synced", px(72)))
        ttk.Label(box, image=self._big_icon).pack(pady=(0, px(14)))
        ttk.Label(box, text="Keep any two folders in sync", style="Name.TLabel").pack()
        self.tint(ttk.Label(box, text="Pick a folder on your PC and one in OneDrive (or anywhere).\n"
                                      "Changes flow both ways, automatically.",
                            style="Muted.TLabel", justify="center")).pack(pady=(px(4), px(16)))
        ttk.Button(box, text="Add your first pair", style="Accent.TButton",
                   command=self.add_dialog).pack()

    def _menu(self, button, pid):
        pair = next((p for p in self.ctrl.pairs() if p.id == pid), None)
        if pair is None:
            return
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Resume" if pair.paused else "Pause",
                      command=lambda: self.ctrl.set_paused(pid, not pair.paused))
        m.add_command(label="Sync now", command=lambda: self.ctrl.sync_now(pid),
                      state="disabled" if pair.paused else "normal")
        m.add_separator()
        m.add_command(label=f"Open {elide(os.path.basename(pair.left) or pair.left, 30)}",
                      command=lambda: open_folder(pair.left))
        m.add_command(label=f"Open {elide(os.path.basename(pair.right) or pair.right, 30)}",
                      command=lambda: open_folder(pair.right))
        m.add_separator()
        m.add_command(label="Stop syncing this pair…", command=lambda: self._remove(pair))
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _remove(self, pair):
        if messagebox.askyesno(
                APP, "Stop syncing these folders?\n\n"
                     f"{pair.left}\n⇄\n{pair.right}\n\nYour files stay where they are.",
                parent=self.root):
            self.ctrl.remove_pair(pair.id)

    def _confirm_delete(self, pid):
        pair = next((p for p in self.ctrl.pairs() if p.id == pid), None)
        st = self.ctrl.snapshot().get(pid)
        if not pair or not st:
            return
        empty = pair.left if st.empty_side == 0 else pair.right
        other = pair.right if st.empty_side == 0 else pair.left
        if messagebox.askyesno(
                APP, f"{empty} is empty.\n\nDelete the same files in\n{other}?\n\n"
                     "They'll go to the Recycle Bin.", icon="warning", parent=self.root):
            self.ctrl.resolve_empty(pid, True)

    # ---------------------------------------------------------------- dialogs

    def add_dialog(self):
        AddDialog(self)

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
            ps = self.ctrl.pairs()
            return bool(ps) and all(p.paused for p in ps)

        def toggle_all():
            pause = not all_paused()
            for p in self.ctrl.pairs():
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
        self.root.after(600, self._poll)

    def run(self):
        self.root.mainloop()


class AddDialog:
    def __init__(self, app: App):
        self.app = app
        px = app.px
        self.win = win = tk.Toplevel(app.root)
        win.withdraw()
        win.title("Add folder pair")
        win.transient(app.root)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=px(22))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="Add folder pair", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(14)))
        self.a = tk.StringVar()
        self.b = tk.StringVar()
        self._field(f, 1, "Folder A", self.a, "This folder…", self._pick_a)
        self.b_box = self._field(f, 3, "Folder B", self.b, "…stays identical to this one",
                                 self._pick_b, combo=True)
        self.a.trace_add("write", lambda *_: self._suggest())

        app.tint(ttk.Label(f, style="Muted.TLabel", wraplength=px(440), justify="left", text=(
            "Tip: pick a folder inside OneDrive as Folder B to back Folder A up to the cloud. "
            "A folder that doesn't exist yet will be created.\n\n"
            "If both already contain files, they're merged. Where the same file differs, the "
            "newer one is kept and the older one goes to the Recycle Bin."))).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(px(14), 0))
        self.err = app.tint(ttk.Label(f, text="", style="Error.TLabel", wraplength=px(440)), "error")
        self.err.grid(row=6, column=0, columnspan=2, sticky="w", pady=(px(8), 0))

        btns = ttk.Frame(f)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(px(14), 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=(0, px(8)))
        ttk.Button(btns, text="Start syncing", style="Accent.TButton",
                   command=self._ok).pack(side="left")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: self._ok())

        win.update_idletasks()
        r = app.root
        if r.winfo_viewable():
            x = r.winfo_rootx() + (r.winfo_width() - win.winfo_reqwidth()) // 2
            y = r.winfo_rooty() + (r.winfo_height() - win.winfo_reqheight()) // 3
            win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        win.deiconify()
        dark_titlebar(win, app.theme == "dark")
        win.grab_set()

    def _field(self, f, row, label, var, hint, pick, combo=False):
        px = self.app.px
        top = ttk.Frame(f)
        top.grid(row=row, column=0, columnspan=2, sticky="w", pady=(px(6), px(4)))
        ttk.Label(top, text=label, style="Name.TLabel").pack(side="left")
        self.app.tint(ttk.Label(top, text="   " + hint, style="Muted.TLabel")).pack(side="left")
        e = (ttk.Combobox(f, textvariable=var, width=50, values=[]) if combo
             else ttk.Entry(f, textvariable=var, width=52))
        e.grid(row=row + 1, column=0, sticky="ew")
        ttk.Button(f, text="Browse…", command=pick).grid(row=row + 1, column=1, padx=(px(8), 0))
        return e

    def _suggest(self):
        """Offer OneDrive\\<name of Folder A> in Folder B's dropdown (never filled in automatically)."""
        a = self.a.get().strip().strip('"')
        name = os.path.basename(os.path.normpath(a)) if a else ""
        self.b_box.configure(values=[os.path.join(ONEDRIVE, name)] if ONEDRIVE and name else [])

    def _pick_a(self):
        p = filedialog.askdirectory(parent=self.win, title="Choose Folder A", mustexist=True)
        if p:
            self.a.set(os.path.normpath(p))

    def _pick_b(self):
        p = filedialog.askdirectory(parent=self.win, title="Choose Folder B", mustexist=True)
        if p:
            self.b.set(os.path.normpath(p))

    def _ok(self):
        a, b = self.a.get().strip().strip('"'), self.b.get().strip().strip('"')
        created = []
        if a and b:
            for p in (a, b):
                if not os.path.exists(p) and os.path.isdir(os.path.dirname(os.path.abspath(p))):
                    created.append(p)
        problem = self.app.ctrl.validate(a, b) if not created else None
        if created:
            # validate as if they existed, by checking overlaps only after creating
            try:
                for p in created:
                    os.makedirs(p)
            except OSError as e:
                self.err.configure(text=f"Couldn't create “{p}”: {e.strerror}")
                return
            problem = self.app.ctrl.validate(a, b)
            if problem:
                for p in created:
                    try:
                        os.rmdir(p)
                    except OSError:
                        pass
        if problem:
            self.err.configure(text=problem)
            return
        self.app.ctrl.add_pair(a, b)
        self.win.destroy()


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
