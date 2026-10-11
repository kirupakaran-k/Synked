"""Synked — keep folders in sync, continuously. The window (Qt / PySide6)."""
from __future__ import annotations

import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QParallelAnimationGroup,  # noqa: E402
                            QPoint, QPropertyAnimation, QSize, Qt, QTimer)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame,  # noqa: E402
                               QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
                               QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
                               QScrollArea, QSystemTrayIcon,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import theme  # noqa: E402
from cards import JobCard, button, label  # noqa: E402
from controls import Segmented, ToggleSwitch, dropped_folders  # noqa: E402
from icon import make_icon  # noqa: E402
from store import (CONFIG_EXT, OLD_APP_NAME, Member, app_dir, data_dir,  # noqa: E402
                   export_settings, migrate_old_home, old_app_dir, read_settings_file)
from ui_common import IS_WIN, elide, open_folder  # noqa: E402
from version import APP, COMPANY, CREDIT, VERSION  # noqa: E402
from watcher import Controller, _overlaps  # noqa: E402

SHOW_FLAG = os.path.join(app_dir(), "show.flag")   # "please show your window"
QUIT_FLAG = os.path.join(app_dir(), "quit.flag")   # "please quit" (used by build.bat)


# ============================================================== Windows helpers
def single_instance(signal: str = SHOW_FLAG) -> bool:
    """True if we're the only Synked running. Otherwise pass `signal` to the running one."""
    if not IS_WIN:
        return True
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    single_instance.handle = k32.CreateMutexW(None, False, "Local\\Synked.SingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        open(signal, "w").close()
        return False
    return True


def dark_titlebar(win: QWidget, dark: bool) -> None:
    if not IS_WIN:
        return
    try:
        import ctypes
        val = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(win.winId()), 20, ctypes.byref(val),
                                                   ctypes.sizeof(val))
    except Exception:
        pass


def stop_old_app(wait: float = 8.0) -> None:
    """If SyncMate (this app before it was renamed) is still running, ask it to quit and
    wait for it, so the two never sync the same folders at once."""
    if not IS_WIN:
        return
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p

    def running() -> bool:
        h = k32.OpenMutexW(0x00100000, False, "Local\\SyncMate.SingleInstance")  # SYNCHRONIZE
        if h:
            k32.CloseHandle(ctypes.c_void_p(h))
        return bool(h)

    if not running():
        return
    try:
        open(os.path.join(old_app_dir(), "quit.flag"), "w").close()
    except OSError:
        return
    end = time.monotonic() + wait
    while running() and time.monotonic() < end:
        time.sleep(0.25)


def carry_over_old_data() -> None:
    """First start after the rename: bring your folder syncs and sync history over from
    SyncMate, and move Start with Windows to the new name."""
    stop_old_app()
    try:
        migrate_old_home(app_dir(), old_app_dir())
    except OSError:
        pass
    migrate_autostart()


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


def migrate_autostart() -> None:
    """SyncMate's Start with Windows entry becomes Synked's (pointing at this exe)."""
    if not IS_WIN:
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, OLD_APP_NAME)
            except OSError:
                return
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
            winreg.DeleteValue(k, OLD_APP_NAME)
    except OSError:
        pass


def pil_to_pixmap(img) -> QPixmap:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


def app_icon(state: str = "synced") -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(pil_to_pixmap(make_icon(state, s)))
    return icon


# ============================================================== settings flyout
class SettingsFlyout(QWidget):
    """The ⚙ menu: a panel that fades and slides down from the gear button."""

    def __init__(self, win: "MainWindow"):
        super().__init__(win, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.win = win
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 18)          # room for the soft shadow
        panel = self.panel = QFrame()
        panel.setObjectName("flyout")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        panel.setGraphicsEffect(shadow)
        outer.addWidget(panel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(10)
        v.addWidget(label("Settings", "name"))
        self.autostart = ToggleSwitch("Start with Windows")
        self.autostart.setChecked(get_autostart())
        self.autostart.setEnabled(IS_WIN)
        self.autostart.toggled.connect(win.set_autostart)
        v.addWidget(self.autostart)
        v.addWidget(self._divider())
        v.addWidget(label("Theme"))
        self.theme = Segmented([("auto", "Auto"), ("light", "Light"), ("dark", "Dark")],
                               win.ctrl.config.theme)
        self.theme.changed.connect(win.set_theme_pref)
        v.addWidget(self.theme)
        v.addWidget(self._divider())
        v.addWidget(label("Folder sync configurations"))
        row = QHBoxLayout()
        row.setSpacing(8)
        b = button("Export")
        b.clicked.connect(lambda: (self.hide(), win.export_syncs()))
        row.addWidget(b)
        b = button("Import")
        b.clicked.connect(lambda: (self.hide(), win.import_syncs()))
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(self._divider())
        b = button("View activity log")
        b.clicked.connect(lambda: (self.hide(), win.show_activity()))
        v.addWidget(b)
        foot = label(f"{APP} (version {VERSION}) by {COMPANY}\n{CREDIT}", "faint")
        foot.setAlignment(Qt.AlignHCenter)
        v.addWidget(foot)
        self.hidden_at = 0.0
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.slide = QPropertyAnimation(self, b"pos", self)
        self.group = QParallelAnimationGroup(self)
        self.group.addAnimation(self.fade)
        self.group.addAnimation(self.slide)

    @staticmethod
    def _divider():
        d = QFrame()
        d.setObjectName("divider")
        return d

    def popup(self, anchor: QWidget):
        self.adjustSize()
        m = self.layout().contentsMargins()
        right = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
        end = QPoint(right.x() - self.width() + m.right(), right.y() - m.top())
        start = end - QPoint(0, 10)
        self.setWindowOpacity(0.0)
        self.move(start)
        self.show()
        for a, (s, e) in ((self.fade, (0.0, 1.0)), (self.slide, (start, end))):
            a.setDuration(170)
            a.setEasingCurve(QEasingCurve.OutCubic)
            a.setStartValue(s)
            a.setEndValue(e)
        self.group.start()

    def hideEvent(self, e):                   # noqa: N802
        self.hidden_at = time.monotonic()
        super().hideEvent(e)


# ============================================================== activity log
class ActivityWindow(QDialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"{APP} — Activity")
        self.resize(760, 440)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        top = QHBoxLayout()
        top.addWidget(label("Activity", "title"))
        top.addStretch(1)
        b = button("Open log file")
        b.clicked.connect(lambda: open_folder(data_dir()))
        top.addWidget(b)
        v.addLayout(top)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Time", "What happened"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        v.addWidget(self.tree, 1)
        self._seen = None
        self.refresh()

    def refresh(self):
        items = list(self.win.ctrl.activity)
        head = items[0] if items else None
        if head == self._seen:
            return
        self._seen = head
        self.tree.clear()
        c = theme.pal()
        for t, _pid, level, msg in items:
            it = QTreeWidgetItem([time.strftime("%H:%M:%S", time.localtime(t)), msg])
            if level == "error":
                for col in (0, 1):
                    it.setForeground(col, QColor(c["error"]))
            elif level == "warn":
                for col in (0, 1):
                    it.setForeground(col, QColor(theme.STATE_COLOR["syncing"]))
            self.tree.addTopLevelItem(it)


# ============================================================== main window
class MainWindow(QMainWindow):
    def __init__(self, app: QApplication, ctrl: Controller, start_hidden: bool = False):
        super().__init__()
        self.qapp, self.ctrl = app, ctrl
        self.cards: dict = {}
        self.card_sig = None
        self.expanded = None          # key of the open card: a job id, "new", or None
        self._scroll_plan = None      # how the list scrolls while cards open/close
        self._draft = False
        self._summary_override = None
        self.activity_win = None
        self.tray = None
        self._busy = False

        self.setWindowTitle(f"{APP} - {VERSION}")
        self.setWindowIcon(app_icon())
        self.resize(880, 680)
        self.setMinimumSize(460, 420)
        self.setAcceptDrops(True)

        self.theme = self.wanted_theme()
        self._apply_style(self.theme)
        self._build()
        self.flyout = SettingsFlyout(self)
        self.refresh()
        self._heal_autostart()
        self._start_tray()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(600)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self._follow_system())
        if not start_hidden or self.tray is None:
            self.show()
            dark_titlebar(self, self.theme == "dark")

    # ---------------------------------------------------------------- layout
    def _build(self):
        page = QWidget()
        page.setObjectName("page")
        self.setCentralWidget(page)
        v = QVBoxLayout(page)
        v.setContentsMargins(24, 20, 24, 14)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(APP, "title"))
        self.summary = label("", "muted")
        titles.addWidget(self.summary)
        head.addLayout(titles)
        head.addStretch(1)
        self.add_btn = button(" Add folder sync", "primary")
        self.add_btn.setIconSize(QSize(14, 14))
        self.add_btn.clicked.connect(lambda: self.new_sync())
        head.addWidget(self.add_btn, 0, Qt.AlignVCenter)
        self.gear = button("", "icon", "Settings")
        self.gear.setIconSize(QSize(18, 18))
        self.gear.clicked.connect(self.toggle_settings)
        head.addWidget(self.gear, 0, Qt.AlignVCenter)
        v.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # The scroll bar always keeps its place (it's just invisible when there's nothing
        # to scroll). If it came and went, the cards would get narrower/wider, re-wrap,
        # change height and make it come and go again: a jitter loop.
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        sb = self.scroll.verticalScrollBar()

        def idle(lo, hi, sb=sb):
            sb.setProperty("idle", hi <= lo)
            sb.style().unpolish(sb)
            sb.style().polish(sb)
        sb.rangeChanged.connect(idle)
        idle(0, 0)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list = QWidget()
        self.list.setObjectName("list")
        self.list_box = QVBoxLayout(self.list)
        self.list_box.setContentsMargins(0, 0, 4, 0)
        self.list_box.setSpacing(10)
        self.list_box.addStretch(1)
        self.scroll.setWidget(self.list)
        v.addWidget(self.scroll, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        b = button("Sync now", tip="Check all folder syncs for changes right away")
        b.clicked.connect(lambda: self.ctrl.sync_now())
        foot.addWidget(b)
        v.addLayout(foot)
        self._retheme_icons()

    # ---------------------------------------------------------------- theme
    def wanted_theme(self) -> str:
        pref = self.ctrl.config.theme
        return self.system_theme() if pref == "auto" else pref

    @staticmethod
    def system_theme() -> str:
        try:
            scheme = QGuiApplication.styleHints().colorScheme()
            return "dark" if scheme == Qt.ColorScheme.Dark else "light"
        except Exception:
            return "light"

    def _apply_style(self, name: str):
        theme.set_current(name)
        c = theme.PALETTES[name]
        pal = QPalette()
        for role, key in ((QPalette.Window, "window"), (QPalette.WindowText, "text"),
                          (QPalette.Base, "field"), (QPalette.AlternateBase, "surface2"),
                          (QPalette.Text, "text"), (QPalette.Button, "button"),
                          (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                          (QPalette.HighlightedText, "on_accent"),
                          (QPalette.ToolTipBase, "tooltip"), (QPalette.ToolTipText, "on_tooltip"),
                          (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent")):
            pal.setColor(role, QColor(c[key]))
        self.qapp.setPalette(pal)
        self.qapp.setStyleSheet(theme.style_sheet(name))

    def switch_theme(self, name: str, animate: bool = True):
        """Cross-fade to the other theme: a picture of the window as it is fades away over
        the window already redrawn in the new colours (Qt draws off-screen, so nothing
        half-drawn is ever shown)."""
        if name == self.theme:
            return
        cover = None
        if animate and self.isVisible():
            page = self.centralWidget()
            cover = QLabel(page)
            cover.setPixmap(page.grab())
            cover.setGeometry(page.rect())
            cover.setAttribute(Qt.WA_TransparentForMouseEvents)
            cover.show()
            cover.raise_()
        self.theme = name
        self._apply_style(name)
        self._retheme_icons()
        for card in self.cards.values():
            card.retheme()
        dark_titlebar(self, name == "dark")
        if self.activity_win is not None:
            dark_titlebar(self.activity_win, name == "dark")
            self.activity_win._seen = None
            self.activity_win.refresh()
        if cover is not None:
            fx = QGraphicsOpacityEffect(cover)
            cover.setGraphicsEffect(fx)
            a = QPropertyAnimation(fx, b"opacity", cover)
            a.setDuration(260)
            a.setStartValue(1.0)
            a.setEndValue(0.0)
            a.setEasingCurve(QEasingCurve.InOutQuad)
            a.finished.connect(cover.deleteLater)
            a.start()
        self.flyout.update()

    def set_theme_pref(self, pref: str):
        self.ctrl.remember(theme=pref)
        self.switch_theme(self.wanted_theme())

    def _follow_system(self):
        if self.ctrl.config.theme == "auto":
            self.switch_theme(self.wanted_theme())

    def _retheme_icons(self):
        c = theme.pal()
        self.add_btn.setIcon(theme.plus_icon(c["on_accent"], 14, weight=0.16))
        self.gear.setIcon(theme.gear_icon(c["text"], 18))
        self._empty_icon = None

    # ---------------------------------------------------------------- cards
    def refresh(self):
        jobs = self.ctrl.jobs()
        snap = self.ctrl.snapshot()
        sig = [(j.id, j.main, tuple((m.id, m.path, m.mode, m.versions) for m in j.members))
               for j in jobs] + (["new"] if self._draft else [])
        if sig != self.card_sig and not self._animating():
            self._rebuild(jobs)      # (never mid-animation: it would cut the slide short)
            self.card_sig = sig
        for j in jobs:
            st, card = snap.get(j.id), self.cards.get(j.id)
            if st is not None and card is not None:
                card.update_status(st, j)
        self._update_summary(jobs, snap)
        self._busy = any(st.state in ("syncing", "starting") for st in snap.values())

    def _animating(self) -> bool:
        return any(c.drawer.anim.state() == QAbstractAnimation.Running
                   for c in self.cards.values()) or bool(getattr(self, "_folding", 0))

    def _clear_list(self):
        while self.list_box.count() > 1:
            w = self.list_box.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _rebuild(self, jobs):
        draft = self.cards.get("new")
        kept = draft.values() if draft else None
        self.list.setUpdatesEnabled(False)
        self._clear_list()
        self.cards = {}
        self._scroll_plan = None      # it may refer to cards that are now gone
        if self._draft:
            card = self.cards["new"] = JobCard(self)
            if kept:
                card.main_box.setText(kept[0])
                for m in kept[1]:
                    card.add_row(m)
            self.list_box.insertWidget(self.list_box.count() - 1, card)
        for j in jobs:
            card = self.cards[j.id] = JobCard(self, j)
            self.list_box.insertWidget(self.list_box.count() - 1, card)
        if not self.cards:
            self.list_box.insertWidget(0, self._empty_state())
        if self.expanded in self.cards:
            self.cards[self.expanded].expand(reset=self.expanded != "new", animate=False)
        else:
            self.expanded = None
        self.list.setUpdatesEnabled(True)

    def _empty_state(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 50, 0, 0)
        v.setSpacing(4)
        pic = QLabel()
        pic.setPixmap(pil_to_pixmap(make_icon("synced", 144)).scaled(
            72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        pic.setAlignment(Qt.AlignCenter)
        v.addWidget(pic)
        v.addSpacing(10)
        t = label("Keep any two folders in sync", "name")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        t = label("Pick a folder on your PC and one in OneDrive (or anywhere).\n"
                  "Changes flow both ways, automatically.\n\n"
                  "Tip: you can drag folders onto this window.", "body-muted")
        t.setAlignment(Qt.AlignCenter)
        v.addWidget(t)
        v.addSpacing(12)
        row = QHBoxLayout()
        row.addStretch(1)
        b = button("Add your first folder sync", "primary")
        b.clicked.connect(lambda: self.new_sync())
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        return box

    def after_change(self):
        """A job was changed or removed: the sync engine applies that a moment later, so
        look again shortly to show the result as soon as it's in."""
        self.card_sig = None
        self.refresh()
        for ms in (60, 180, 400):
            QTimer.singleShot(ms, self.refresh)

    def remove_card(self, card: QWidget, jid: str):
        """Fold a removed sync's card away, then forget it."""
        self.ctrl.remove_job(jid)
        a = QPropertyAnimation(card, b"maximumHeight", card)
        a.setDuration(200)
        a.setEasingCurve(QEasingCurve.InOutCubic)
        a.setStartValue(card.height())
        a.setEndValue(0)
        self._folding = getattr(self, "_folding", 0) + 1

        def done():
            self._folding -= 1
            card.hide()
            self.after_change()
        a.finished.connect(done)
        a.start()

    def set_expanded(self, key) -> None:
        """Open one card (only one at a time); None closes them all."""
        if self.expanded == key:
            return
        if self.expanded == "new" and key != "new":
            old = self.cards.get("new")
            self.expanded = None
            if old is not None:
                old.collapse(then=self.close_draft)
        else:
            old = self.cards.get(self.expanded)
            if old is not None:
                old.collapse()
        self.expanded = key
        card = self.cards.get(key)
        if card is not None:
            card.expand()

    def new_sync(self, prefill=()) -> None:
        """Show the blank 'New folder sync' card at the top of the list, opening. If another
        sync is open, it closes first and the new card opens right after."""
        self.flyout.hide()
        if not self._draft:
            old = self.cards.get(self.expanded) if self.expanded is not None else None
            if old is not None and old.opened:
                self.expanded = None
                # (opened a moment later, once the closing card has fully finished)
                old.collapse(then=lambda: QTimer.singleShot(0, lambda: self._open_draft(prefill)))
                return
            self._open_draft(prefill)
            return
        card = self.cards["new"]
        if prefill:
            card.fill(prefill)
        if not self._animating():
            self.scroll.verticalScrollBar().setValue(0)
        card.main_box.setFocus()

    def _open_draft(self, prefill=()) -> None:
        if self._draft:
            return
        self._draft = True
        self.expanded = "new"
        self.refresh()
        card = self.cards["new"]
        card.expand(reset=False)
        if prefill:
            card.fill(prefill)
        card.main_box.setFocus()

    def close_draft(self) -> None:
        self._draft = False
        if self.expanded == "new":
            self.expanded = None
        self.refresh()

    def keep_visible(self, card: QWidget) -> None:
        """While a card opens, scroll so it stays in view (its top never leaves the view)."""
        if card.parent() is None:
            return
        sb = self.scroll.verticalScrollBar()
        view = self.scroll.viewport().height()
        top = card.y()
        bottom = top + card.height()
        if bottom > sb.value() + view:
            sb.setValue(min(top, bottom - view))
        elif top < sb.value():
            sb.setValue(top)

    # While cards open or close, one place decides where the list is scrolled. It works
    # from the animations' own numbers (how much of each editor shows), not from widget
    # positions, which Qt only updates a moment later: reading those made the list jump.
    def drawer_step(self, card: QWidget) -> None:
        if card.parent() is None:
            return
        sb = self.scroll.verticalScrollBar()
        plan = self._scroll_plan
        if plan is None:
            plan = self._scroll_plan = {"cards": {}, "max0": sb.maximum(),
                                        "view": self.scroll.viewport().height(), "how": None}
        ents = plan["cards"]
        if card not in ents:
            # where it would be if every card already moving were still at its start
            y = card.y() - sum(c.drawer.height() - e["h0"] for c, e in ents.items()
                               if c.y() < card.y())
            ents[card] = {"y0": y, "h0": card.drawer.shown,
                          "base": card.height() - card.drawer.height()}
            plan["how"] = None               # something new is moving: plan again from here
        now = {c: c.drawer.shown for c in ents}
        last = max(0, plan["max0"] + sum(now[c] - e["h0"] for c, e in ents.items()))

        def top_of(c):
            y0 = ents[c]["y0"]
            return y0 + sum(now[d] - ents[d]["h0"] for d in ents if ents[d]["y0"] < y0)

        def progress(c, h_from, h_to):
            span = h_to - h_from
            return 1.0 if span == 0 else max(0.0, min(1.0, (now[c] - h_from) / span))

        view = plan["view"]
        opening = [c for c in ents if c.drawer.opened]
        if opening:
            # Keep the opening card in view: as it grows, the list follows its bottom until
            # its top reaches the top of the window, and its top never leaves the view. If
            # part of it was already out of view, that part comes in gradually, in step with
            # the curtain, instead of with a jump.
            c = opening[-1]
            top = top_of(c)
            bottom = top + ents[c]["base"] + now[c]
            if plan["how"] is None:
                s0 = sb.value()
                plan["how"] = ("open", c, now[c], self._end_height(c),
                               max(0, bottom - (s0 + view)), max(0, s0 - top))
            _, c, h_from, h_to, below, above = plan["how"]
            left = 1.0 - progress(c, h_from, h_to)
            pos = sb.value()
            pos = max(pos, bottom - view - below * left)
            pos = min(pos, top + above * left)
        else:
            # Only closing. Its top in view: nothing above it moves. Its top scrolled out
            # above: the list glides up as it shrinks (never past its top), so the syncs
            # below stay still. Toward the end of the list it glides down with it. All in
            # one smooth glide, planned when the closing started.
            if plan["how"] is None:
                c = min(ents, key=lambda d: ents[d]["y0"])
                s0, top, h_to = sb.value(), top_of(c), self._end_height(c)
                end = s0 if top >= s0 else max(top, s0 - (now[c] - h_to))
                last_end = max(0, plan["max0"] + sum(
                    self._end_height(d) - e["h0"] for d, e in ents.items()))
                plan["how"] = ("close", c, now[c], h_to, s0, max(0, min(end, last_end)))
            _, c, h_from, h_to, s0, end = plan["how"]
            pos = s0 + (end - s0) * progress(c, h_from, h_to)
        pos = round(max(0, min(pos, last)))
        self._sync_list()
        if sb.maximum() < pos:          # (only if Qt still hasn't measured the new length)
            sb.setRange(0, last)
        sb.setValue(pos)

    @staticmethod
    def _end_height(card) -> int:
        """How much of a card's editor will show when its curtain stops."""
        a = card.drawer.anim
        return int(a.endValue()) if a.state() == QAbstractAnimation.Running else card.drawer.shown

    def drawer_settled(self) -> None:
        if not self._animating():
            self._scroll_plan = None

    def _sync_list(self) -> None:
        """Lay the list out now, not a moment later: otherwise one frame can show the new
        scroll position with the cards still at their old places, which looks like a jump."""
        self.list_box.activate()
        QApplication.sendEvent(self.scroll, QEvent(QEvent.LayoutRequest))

    # ---------------------------------------------------------------- checks
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
        if QMessageBox.warning(self, APP, f"{empty} is empty.\n\nDelete the same files in\n"
                                          f"{other}?\n\nThey'll go to the Recycle Bin (or old "
                                          "versions).", QMessageBox.Yes | QMessageBox.No,
                               QMessageBox.No) == QMessageBox.Yes:
            self.ctrl.resolve_empty(jid, True)

    # ---------------------------------------------------------------- summary
    def _update_summary(self, jobs, snap):
        if not jobs:
            text = f"Add a folder sync and {APP} keeps your folders identical."
        else:
            overall = self.ctrl.overall()
            states = {st.state for st in snap.values()}
            if overall == "syncing" and states.isdisjoint({"syncing", "starting"}):
                overall = "waiting"
            n = len(jobs)
            text = {
                "synced": f"{n} folder sync{'s' if n > 1 else ''} · everything is in sync",
                "syncing": "Syncing changes…",
                "waiting": "Some files are in use — they'll sync when they're closed",
                "paused": "All syncing is paused",
                "error": "Something needs your attention",
            }[overall]
        if self._summary_override:
            text = self._summary_override
        if self.summary.text() != text:
            self.summary.setText(text)
        if self.tray is not None:
            state = self.ctrl.overall() if jobs else "synced"
            if state != getattr(self, "_tray_state", None):
                self._tray_state = state
                self.tray.setIcon(app_icon(state))
            self.tray.setToolTip(f"{APP} — {text}")

    def flash(self, text: str, ms: int = 2500):
        self._summary_override = text
        self.refresh()

        def clear():
            if self._summary_override == text:
                self._summary_override = None
                self.refresh()
        QTimer.singleShot(ms, clear)

    # ---------------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):              # noqa: N802
        if dropped_folders(e.mimeData()):
            e.acceptProposedAction()
            self._summary_override = "Drop to add a folder sync"
            self.refresh()

    def dragLeaveEvent(self, e):              # noqa: N802
        self._summary_override = None
        self.refresh()

    def dropEvent(self, e):                   # noqa: N802
        self._summary_override = None
        folders = [p for p in dropped_folders(e.mimeData()) if os.path.isdir(p)]
        if not folders:
            self.flash("Drop folders, not files, to add a folder sync.")
            return
        e.acceptProposedAction()
        self.new_sync(prefill=folders)

    # ---------------------------------------------------------------- settings
    def toggle_settings(self):
        if self.flyout.isVisible():
            self.flyout.hide()
            return
        if time.monotonic() - self.flyout.hidden_at < 0.25:
            return              # this click is the one that just closed it
        self.flyout.autostart.blockSignals(True)
        self.flyout.autostart.setChecked(get_autostart())
        self.flyout.autostart.blockSignals(False)
        self.flyout.theme.set_value(self.ctrl.config.theme)
        self.flyout.popup(self.gear)

    def set_autostart(self, on: bool):
        try:
            set_autostart(on)
        except OSError as e:
            QMessageBox.critical(self, APP, f"Couldn't change the startup setting:\n{e}")
            self.flyout.autostart.setChecked(get_autostart())

    @staticmethod
    def _heal_autostart():
        """If Start with Windows is on, point it at whichever Synked.exe is running now."""
        try:
            if get_autostart():
                set_autostart(True)
        except OSError:
            pass

    def show_activity(self):
        if self.activity_win is None:
            self.activity_win = ActivityWindow(self)
        self.activity_win.show()
        self.activity_win.raise_()
        self.activity_win.activateWindow()
        dark_titlebar(self.activity_win, self.theme == "dark")

    # ---------------------------------------------------------------- export / import
    def export_syncs(self, path: str | None = None) -> bool:
        """Save every folder sync (folders, sync types, options) and the theme to a file."""
        if not self.ctrl.jobs():
            QMessageBox.information(self, APP, "There are no folder syncs to export yet.")
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export folder sync configurations",
                f"{APP} folder syncs {time.strftime('%Y-%m-%d')}{CONFIG_EXT}",
                f"{APP} configuration (*{CONFIG_EXT});;All files (*.*)")
            if not path:
                return False
            if not os.path.splitext(path)[1]:
                path += CONFIG_EXT
        try:
            n = export_settings(path, self.ctrl.config, VERSION)
        except OSError as e:
            QMessageBox.critical(self, APP, f"Couldn't save the file:\n{e.strerror or e}")
            return False
        self.ctrl._log("", "info", f"Exported {n} folder sync{'s' if n != 1 else ''} to {path}")
        self.flash(f"Exported {n} folder sync{'s' if n != 1 else ''} to "
                   f"{elide(os.path.basename(path), 40)}", 4000)
        return True

    def import_syncs(self, path: str | None = None, replace: bool | None = None) -> int:
        """Load folder syncs from an exported file. Returns how many were added."""
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import folder sync configurations", "",
                f"{APP} configuration (*{CONFIG_EXT});;Older exports (*.json);;All files (*.*)")
            if not path:
                return 0
        try:
            cfg = read_settings_file(path)
        except ValueError as e:
            QMessageBox.critical(self, APP, str(e))
            return 0
        if not cfg.jobs:
            QMessageBox.information(self, APP, "That file has no folder syncs in it.")
            return 0
        current = self.ctrl.jobs()
        n = len(cfg.jobs)
        if replace is None:
            if current:
                box = QMessageBox(QMessageBox.Question, APP,
                                  f"Import {n} folder sync{'s' if n != 1 else ''}?", parent=self)
                box.setInformativeText("Replace your current folder syncs, or add these to them?")
                rep = box.addButton("Replace", QMessageBox.AcceptRole)
                add = box.addButton("Add", QMessageBox.AcceptRole)
                box.addButton(QMessageBox.Cancel)
                box.exec()
                if box.clickedButton() not in (rep, add):
                    return 0
                replace = box.clickedButton() is rep
            else:
                replace = True

        taken = [] if replace else [p for j in current for p in [j.main] + [m.path for m in j.members]]
        added, skipped, missing = [], [], []
        for j in cfg.jobs:
            folders = [j.main] + [m.path for m in j.members]
            clash = any(_overlaps(a, b) for a in folders for b in taken) or any(
                _overlaps(a, b) for i, a in enumerate(folders) for b in folders[i + 1:])
            if clash or not j.members:
                skipped.append(j.main)
                continue
            taken += folders
            added.append(j)
            missing += [p for p in folders if not os.path.isdir(p)]

        if replace:
            for j in current:
                self.ctrl.remove_job(j.id)
            if cfg.theme != self.ctrl.config.theme:
                self.flyout.theme.set_value(cfg.theme)
                self.set_theme_pref(cfg.theme)
        for j in added:
            job = self.ctrl.add_job(j.main, [Member(m.path, m.mode, m.versions, keep_days=m.keep_days)
                                             for m in j.members])
            if j.paused:
                self.ctrl.set_paused(job.id, True)
        self.ctrl._log("", "info", f"Imported {len(added)} folder sync"
                                   f"{'s' if len(added) != 1 else ''} from {path}")
        self.expanded, self._draft, self.card_sig = None, False, None
        self.refresh()
        note = [f"Imported {len(added)} folder sync{'s' if len(added) != 1 else ''}."]
        if skipped:
            note.append(f"Skipped {len(skipped)} that would overlap folders already being "
                        "synced:\n" + "\n".join(skipped[:5]))
        if missing:
            note.append("These folders don't exist on this PC yet. Their syncs will start "
                        "when the folders appear:\n" + "\n".join(missing[:8]))
        if skipped or missing:
            QMessageBox.information(self, APP, "\n\n".join(note))
        else:
            self.flash(note[0], 4000)
        return len(added)

    # ---------------------------------------------------------------- window & tray
    def _start_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction(f"Open {APP}", self.show_window)
        menu.addAction("Sync now", lambda: self.ctrl.sync_now())
        self.pause_all = menu.addAction("Pause all", self._toggle_all)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        menu.aboutToShow.connect(self._tray_menu_text)
        tray.setContextMenu(menu)
        self._tray_menu = menu
        tray.activated.connect(lambda r: self.show_window()
                               if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        tray.setToolTip(APP)
        tray.show()

    def _all_paused(self) -> bool:
        jobs = self.ctrl.jobs()
        return bool(jobs) and all(j.paused for j in jobs)

    def _tray_menu_text(self):
        self.pause_all.setText("Resume all" if self._all_paused() else "Pause all")

    def _toggle_all(self):
        pause = not self._all_paused()
        for j in self.ctrl.jobs():
            self.ctrl.set_paused(j.id, pause)

    def show_window(self):
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        dark_titlebar(self, self.theme == "dark")

    def closeEvent(self, e):                  # noqa: N802
        if self.tray is None or getattr(self, "_quitting", False):
            self._shutdown()
            e.accept()
            return
        e.ignore()
        self.hide()
        if not self.ctrl.config.tray_hint_shown:
            self.ctrl.remember(tray_hint_shown=True)
            self.tray.showMessage(APP, f"{APP} keeps syncing in the background. "
                                       "Open it again from the tray icon.", app_icon(), 5000)

    def _shutdown(self):
        if getattr(self, "_down", False):
            return
        self._down = True
        self.timer.stop()
        if self.tray is not None:
            self.tray.hide()
        self.ctrl.stop()
        self.qapp.quit()

    def quit(self):
        self._quitting = True
        self.close()
        self._shutdown()

    def _poll(self):
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
            self.show_window()
        self.refresh()
        if self.activity_win is not None and self.activity_win.isVisible():
            self.activity_win.refresh()
        self.timer.setInterval(200 if self._busy else 600)


# ============================================================== start
def make_app(argv=None) -> QApplication:
    if IS_WIN:
        try:   # own taskbar button and icon, not python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EagerSoftech.Synked")
        except Exception:
            pass
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP)
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(theme.FONT_FAMILIES)
    font.setPointSize(10)
    app.setFont(font)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    return app


def main():
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--background", action="store_true", help="start hidden in the tray")
    ap.add_argument("--quit", action="store_true", help="ask a running Synked to close")
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
    carry_over_old_data()
    app = make_app()
    ctrl = Controller()
    ctrl.start()
    try:
        win = MainWindow(app, ctrl, start_hidden=args.background)
        app.exec()
        del win
    finally:
        if ctrl._thread.is_alive():
            ctrl.stop()


if __name__ == "__main__":
    main()
