"""A folder-sync card: Main on top with a tree of its connected folders, live status
and progress, and an editor that slides open below it like a curtain.

The same card, blank, is used for adding a new folder sync.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QEasingCurve, QEvent, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QSizePolicy,
                               QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import theme
from controls import EdgeLayout, FlowLayout, PathCombo, PathEdit, TreeLine
from store import (KEEP_DAY_STOPS, KEEP_DAYS_HELP, MODE_HELP, MODE_LABELS, MODES,
                   OLD_FILES_HELP, OLD_FILES_LABELS, Member, nearest_stop, parse_days)
from ui_common import (ONEDRIVE, describe, elide, folder_name, open_folder, pretty_path,
                       progress_detail)

ARROW_KIND = {"two-way": "both", "mirror": "right", "update": "right", "collect": "left"}
SHORT_MODE = {"two-way": "Two-way", "mirror": "Mirror", "update": "Update", "collect": "Collect"}
BOX_HINT = ("Type a folder path, use Browse, or drag a folder here from File Explorer. "
            "A folder that doesn't exist yet is created.")
QWIDGETSIZE_MAX = 16777215


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    if wrap:
        lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.NoTextInteraction)
    return lab


def button(text: str, kind: str | None = None, tip: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def set_text(lab: QLabel, text: str) -> None:
    if lab.text() != text:
        lab.setText(text)


# ============================================================================ drawer
class Drawer(QWidget):
    """Holds the editor and reveals it like a curtain: the editor always keeps its full
    size and the drawer's height grows from 0 (or shrinks back), clipping it."""

    OPEN_MS, CLOSE_MS = 280, 220

    def __init__(self, content: QWidget, parent=None):
        super().__init__(parent)
        self.content = content
        content.setParent(self)
        content.installEventFilter(self)
        self.opened = False
        self.setMaximumHeight(0)
        self.setVisible(False)
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)          # its height depends on the width (rows wrap)
        self.setSizePolicy(sp)
        # Each step sets the drawer's height AND the card's height together, so the card
        # grows/shrinks by exactly the part being revealed: nothing else moves or stretches.
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._done)
        self._then = None
        self._base = 0

    # its natural height is the editor's height at this width
    def full_height(self, w: int | None = None) -> int:
        w = self.width() if w is None else w
        c = self.content
        return c.heightForWidth(w) if c.hasHeightForWidth() else c.sizeHint().height()

    def hasHeightForWidth(self):              # noqa: N802
        return True

    def heightForWidth(self, w):              # noqa: N802
        # while sliding, ask only for the part that's showing; otherwise the card would
        # hand the rest of the space to its header and stretch it
        return min(self.full_height(w), self.maximumHeight())

    def sizeHint(self):                       # noqa: N802
        return QSize(self.content.sizeHint().width(),
                     min(self.full_height(), self.maximumHeight()))

    def minimumSizeHint(self):                # noqa: N802
        return QSize(0, 0)

    def resizeEvent(self, e):                 # noqa: N802
        self.content.setGeometry(0, 0, self.width(), self.full_height())

    def eventFilter(self, obj, e):            # noqa: N802
        if obj is self.content and e.type() == QEvent.LayoutRequest:
            self.updateGeometry()
            self.content.setGeometry(0, 0, self.width(), self.full_height())
        return False

    def _card(self) -> QWidget:
        return self.parentWidget()

    def _step(self, v):
        v = int(v)
        self.setMaximumHeight(v)
        self._card().setFixedHeight(self._base + v)

    def _release(self):
        card = self._card()
        card.setMinimumHeight(0)
        card.setMaximumHeight(QWIDGETSIZE_MAX)

    def open(self, animate: bool = True):
        self._then = None
        self.opened = True
        self.anim.stop()
        start = self.height() if self.isVisible() else 0
        self.setMaximumHeight(start)
        self.setVisible(True)
        # A card that was only just made (like "New folder sync") has no real size yet:
        # lay the list out now, so it starts from its true height, not Qt's default.
        card = self._card()
        if not card.isVisible() or card.height() > card.heightForWidth(card.width()) + 1:
            if card.isHidden():
                card.setVisible(True)        # Qt would show it a moment later anyway
            card.setMinimumHeight(0)
            card.setMaximumHeight(QWIDGETSIZE_MAX)
            holder = card.parentWidget()
            if holder is not None and holder.layout() is not None:
                holder.layout().activate()
        self._base = card.height() - start
        m = self._card().layout().contentsMargins()
        target = self.full_height(self._card().width() - m.left() - m.right())
        if not animate:
            self._release()
            self.setMaximumHeight(QWIDGETSIZE_MAX)
            return
        self._step(start)
        self.anim.setDuration(self.OPEN_MS)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)     # fast start, gentle stop
        self.anim.setStartValue(start)
        self.anim.setEndValue(max(target, 1))
        self.anim.start()

    def close(self, then=None, animate: bool = True):
        self._then = then
        self.opened = False
        self.anim.stop()
        if not self.isVisible() or not animate:
            self._release()
            self.setMaximumHeight(0)
            self.setVisible(False)
            self._finish_then()
            return
        self._base = self._card().height() - self.height()
        self._step(self.height())
        self.anim.setDuration(self.CLOSE_MS)
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.anim.setStartValue(self.height())
        self.anim.setEndValue(0)
        self.anim.start()

    def _finish_then(self):
        then, self._then = self._then, None
        if then:
            then()

    def _done(self):
        if self.opened:
            self.setMaximumHeight(QWIDGETSIZE_MAX)   # from now on it simply fits its content
            self._release()
        else:
            self.setVisible(False)
            self._release()
            self._finish_then()


# ============================================================================ folder row
class FolderRow(QWidget):
    """One connected folder in the editor: its path, sync type and old-files setting."""

    remove_requested = Signal(object)
    submit = Signal()
    escape = Signal()

    def __init__(self, card: "JobCard", member: Member | None = None):
        super().__init__()
        self.card = card
        self.id = member.id if member else None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.path = PathCombo()
        self.path.setToolTip(BOX_HINT)
        self.path.on_drop = lambda paths: card.on_drop(self, paths)
        self.path.lineEdit().returnPressed.connect(self.submit)
        top.addWidget(self.path, 1)
        b = button("Browse")
        b.clicked.connect(lambda: card.browse(self.path, "folder"))
        top.addWidget(b)
        b = button("Open")
        b.clicked.connect(lambda: card.open_path(self.path.text()))
        top.addWidget(b)
        self.remove = button("", "remove", "Remove this folder from the sync (its files stay "
                                            "where they are)")
        self.remove.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(self.remove)
        v.addLayout(top)

        opts = QWidget()
        flow = FlowLayout(opts, 8, 6)
        self.mode = QComboBox()
        for i, m in enumerate(MODES):
            self.mode.addItem(MODE_LABELS[m], m)
            self.mode.setItemData(i, MODE_HELP[m], Qt.ToolTipRole)
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.mode.currentIndexChanged.connect(self._mode_tip)
        flow.addWidget(self.mode)

        self.old = QComboBox()
        for i, keep in enumerate((False, True)):
            self.old.addItem(OLD_FILES_LABELS[keep], keep)
            self.old.setItemData(i, OLD_FILES_HELP[keep], Qt.ToolTipRole)
        self.old.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.old.currentIndexChanged.connect(self._old_changed)
        flow.addWidget(self.old)

        # "for [slider] [30] days": slide, or type any number of days ("forever" = keep)
        self.days = QWidget()
        d = QHBoxLayout(self.days)
        d.setContentsMargins(4, 0, 0, 0)
        d.setSpacing(10)
        self.for_label = label("for")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, len(KEEP_DAY_STOPS) - 1)
        self.slider.setFixedWidth(120)
        self.slider.setPageStep(1)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.days_box = QLineEdit()
        self.days_box.setAlignment(Qt.AlignCenter)
        self.days_box.setFixedWidth(76)
        self.unit = label("days")
        for w in (self.for_label, self.slider, self.days_box, self.unit):
            w.setToolTip(KEEP_DAYS_HELP)
            d.addWidget(w)
        d.addStretch(0)
        flow.addWidget(self.days)
        v.addWidget(opts)

        self.keep_days = 30
        self.slider.valueChanged.connect(self._slid)
        self.days_box.textEdited.connect(self._typed)
        self.days_box.editingFinished.connect(self.settle)
        self.days_box.returnPressed.connect(self.submit)
        self.retheme()
        self.load(member)

    # ---- values
    def load(self, member: Member | None):
        self.id = member.id if member else self.id
        self.path.setText(member.path if member else "")
        mode = member.mode if member else "two-way"
        self.mode.setCurrentIndex(MODES.index(mode))
        self.old.setCurrentIndex(1 if member and member.versions else 0)
        self.keep_days = max(0, member.keep_days) if member else 30
        self._show_days()
        self._mode_tip()
        self._old_changed()

    def value(self) -> Member | None:
        path = self.path.text().strip().strip('"')
        if not path:
            return None
        self.settle()
        return Member(path=path, mode=self.mode.currentData(), versions=bool(self.old.currentData()),
                      keep_days=self.keep_days, **({"id": self.id} if self.id else {}))

    # ---- behaviour
    def _mode_tip(self, *_):
        self.mode.setToolTip(MODE_HELP[self.mode.currentData()])

    def _old_changed(self, *_):
        keep = bool(self.old.currentData())
        self.old.setToolTip(OLD_FILES_HELP[keep])
        self.days.setVisible(keep)

    def _show_days(self):
        n = self.keep_days
        self.slider.blockSignals(True)
        self.slider.setValue(nearest_stop(n))
        self.slider.blockSignals(False)
        self.days_box.setText("forever" if n == 0 else str(n))
        self._unit()

    def _unit(self):
        n = self.keep_days
        self.unit.setVisible(n != 0)
        self.unit.setText("day" if n == 1 else "days")

    def _slid(self, i: int):
        self.keep_days = KEEP_DAY_STOPS[i]          # far right is "forever"
        self.days_box.setText("forever" if self.keep_days == 0 else str(self.keep_days))
        self._unit()

    def _typed(self, text: str):
        n = parse_days(text)
        if n is not None:
            self.keep_days = n
            self.slider.blockSignals(True)
            self.slider.setValue(nearest_stop(n))
            self.slider.blockSignals(False)
            self._unit()

    def settle(self):
        """Tidy what was typed, or put back the last good value."""
        self._show_days()

    def set_suggestions(self, items):
        self.path.set_suggestions(items)

    def retheme(self):
        self.remove.setIcon(theme.cross_icon("#FFFFFF", 14))
        self.remove.setIconSize(QSize(14, 14))


# ============================================================================ card
class Header(QWidget):
    """The always-visible top part of a card; clicking it opens or closes the card.
    It always keeps exactly its own height (never stretched or squeezed while the
    editor below it slides)."""
    clicked = Signal()

    def _own_height(self) -> int:
        lay = self.layout()
        return lay.totalHeightForWidth(self.width()) if lay and lay.hasHeightForWidth() \
            else lay.totalSizeHint().height()

    def minimumSizeHint(self):                # noqa: N802
        return QSize(0, self._own_height())

    def sizeHint(self):                       # noqa: N802
        return QSize(super().sizeHint().width(), self._own_height())

    def mousePressEvent(self, e):             # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class JobCard(QFrame):
    def __init__(self, win, job=None):
        super().__init__()
        self.win, self.job = win, job
        self.is_new = job is None
        self.key = "new" if self.is_new else job.id
        self.setObjectName("card")
        self.setProperty("open", False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.rows: list[FolderRow] = []
        self.badges: list = []
        self._activity_sig = None
        self._state = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(0)

        # ---- header -----------------------------------------------------------
        head = self.header = Header()
        hp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hp.setHeightForWidth(True)            # its own height, never stretched or squeezed
        head.setSizePolicy(hp)
        head.setCursor(Qt.PointingHandCursor if not self.is_new else Qt.ArrowCursor)
        head.clicked.connect(self.toggle)
        g = QGridLayout(head)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(0)
        g.setColumnStretch(1, 1)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 16)
        self.dot.setAlignment(Qt.AlignCenter)
        g.addWidget(self.dot, 0, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(0)
        body.setContentsMargins(0, 0, 0, 0)
        if self.is_new:
            body.addWidget(label("New folder sync", "name"))
            body.addWidget(label("Choose a Main folder and the folders to keep in sync with it",
                                 "muted", wrap=True))
        else:
            body.addWidget(label(folder_name(job.main), "name"))
            body.addWidget(label("Main · " + elide(pretty_path(os.path.dirname(job.main))
                                                   or job.main, 60), "muted"))
            # the tree: a line comes down from Main and branches off to each folder
            for i, m in enumerate(job.members):
                body.addWidget(self._branch(m, last=i == len(job.members) - 1))
            self.status = label("", "muted", wrap=True)
            self.status.setContentsMargins(0, 10, 0, 0)
            body.addWidget(self.status)
            self.bar = QProgressBar()
            self.bar.setRange(0, 1000)
            self.bar.setTextVisible(False)
            self.bar.setVisible(False)
            body.addSpacing(2)
            body.addWidget(self.bar)
            self.detail = label("", "faint", wrap=True)
            self.detail.setVisible(False)
            body.addWidget(self.detail)
            # "folder suddenly empty" choices
            self.fix = QWidget()
            fx = QHBoxLayout(self.fix)
            fx.setContentsMargins(0, 8, 0, 0)
            fx.setSpacing(8)
            b = button("Restore files", "primary")
            b.clicked.connect(lambda: win.ctrl.resolve_empty(self.job.id, False))
            fx.addWidget(b)
            b = button("Apply the deletion")
            b.clicked.connect(lambda: win.confirm_delete(self.job.id))
            fx.addWidget(b)
            fx.addStretch(1)
            self.fix.setVisible(False)
            body.addWidget(self.fix)
        g.addLayout(body, 0, 1)
        if not self.is_new:
            self.more = QToolButton()
            self.more.setObjectName("more")
            self.more.setIconSize(QSize(20, 20))
            self.more.setCursor(Qt.PointingHandCursor)
            self.more.setToolTip("Open this folder sync to change it")
            self.more.clicked.connect(self.toggle)
            g.addWidget(self.more, 0, 2, Qt.AlignTop)
        outer.addWidget(head)

        # ---- editor, in a drawer that opens like a curtain ------------------------
        self.editor = self._build_editor()
        self.drawer = Drawer(self.editor)
        self.drawer.anim.valueChanged.connect(lambda _: self.win.keep_visible(self))
        outer.addWidget(self.drawer)
        self.retheme()

    # ---------------------------------------------------------------- header parts
    def _branch(self, m: Member, last: bool) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 8, 10, 0)
        badge = QLabel()
        badge.setToolTip(MODE_HELP[m.mode])
        hv.addWidget(badge, 0, Qt.AlignVCenter)
        line = TreeLine(badge, last)
        h.addWidget(line)
        h.addWidget(holder)
        text = QWidget()
        tv = QVBoxLayout(text)
        tv.setContentsMargins(0, 8, 0, 0)
        tv.setSpacing(0)
        tv.addWidget(label(folder_name(m.path), "name"))
        tv.addWidget(label(f"{SHORT_MODE[m.mode]} · "
                           + elide(pretty_path(os.path.dirname(m.path)) or m.path, 56), "muted"))
        h.addWidget(text, 1)
        self.badges.append((badge, m.mode))
        return row

    def retheme(self):
        c = theme.pal()
        for badge, mode in self.badges:
            badge.setPixmap(theme.badge_pixmap(ARROW_KIND[mode], c["accent"], c["on_accent"],
                                               30, 22, self.devicePixelRatioF() or 2))
            badge.setFixedSize(30, 22)
        if not self.is_new:
            self.more.setIcon(theme.dots_icon(c["text"], 20))
        else:
            self._paint_dot(c["accent"])
        self.add_btn.setIcon(theme.plus_icon(c["text"], 12))
        for r in self.rows:
            r.retheme()
        if not self.is_new and self.job is not None and self._state.get("dot"):
            self._paint_dot(self._state["dot"])

    def _paint_dot(self, color: str):
        self.dot.setPixmap(theme.status_dot(color, 10, self.devicePixelRatioF() or 2))

    # ---------------------------------------------------------------- editor
    def _section(self, lay, title, hint=""):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label(title, "section"))
        if hint:
            row.addWidget(label(hint, "muted"))
        row.addStretch(1)
        lay.addLayout(row)

    def _build_editor(self) -> QWidget:
        ed = QWidget()
        v = QVBoxLayout(ed)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(6)
        div = QFrame()
        div.setObjectName("divider")
        v.addWidget(div)
        v.addSpacing(6)

        self._section(v, "Main folder", "the master copy")
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        self.main_box = PathEdit()
        self.main_box.setToolTip(BOX_HINT)
        self.main_box.on_drop = lambda paths: self.on_drop(None, paths)
        self.main_box.textChanged.connect(self._suggest)
        self.main_box.returnPressed.connect(self.apply)
        mrow.addWidget(self.main_box, 1)
        b = button("Browse")
        b.clicked.connect(lambda: self.browse(self.main_box, "Main folder"))
        mrow.addWidget(b)
        b = button("Open")
        b.clicked.connect(lambda: self.open_path(self.main_box.text()))
        mrow.addWidget(b)
        v.addLayout(mrow)

        v.addSpacing(10)
        self._section(v, "Connected folders", "each one syncs with Main its own way")
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(12)
        v.addLayout(self.rows_box)
        add = QHBoxLayout()
        self.add_btn = button(" Add folder")
        self.add_btn.setIconSize(QSize(12, 12))
        self.add_btn.clicked.connect(lambda: self.add_row(focus=True))
        add.addWidget(self.add_btn)
        add.addStretch(1)
        v.addSpacing(2)
        v.addLayout(add)

        if not self.is_new:
            v.addSpacing(10)
            self._section(v, "Recent activity")
            self.activity_holder = QVBoxLayout()
            self.activity_holder.setContentsMargins(0, 0, 0, 0)
            self.activity = None
            v.addLayout(self.activity_holder)

        self.err = label("", "error", wrap=True)
        self.err.setVisible(False)
        v.addWidget(self.err)

        # actions, and the "are you sure?" for Remove, in the same place
        v.addSpacing(8)
        self.stack = QStackedWidget()
        acts = QWidget()
        edge = EdgeLayout(acts, 8)
        left = QWidget()
        lh = QHBoxLayout(left)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(8)
        right = QWidget()
        rh = QHBoxLayout(right)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(8)
        if not self.is_new:
            self.pause_btn = button("Pause")
            self.pause_btn.clicked.connect(self._toggle_pause)
            lh.addWidget(self.pause_btn)
            self.sync_btn = button("Sync now")
            self.sync_btn.clicked.connect(lambda: self.win.ctrl.sync_now(self.job.id))
            lh.addWidget(self.sync_btn)
            b = button("Remove")
            b.clicked.connect(lambda: self.stack.setCurrentIndex(1))
            rh.addWidget(b)
        b = button("Cancel")
        b.clicked.connect(self.cancel)
        rh.addWidget(b)
        self.apply_btn = button("Start syncing" if self.is_new else "Apply", "primary")
        self.apply_btn.clicked.connect(self.apply)
        rh.addWidget(self.apply_btn)
        edge.addWidget(left)
        edge.addWidget(right)
        self.stack.addWidget(acts)

        sure = QWidget()
        sh = EdgeLayout(sure, 8)
        sh.addWidget(label("Stop this folder sync? Your files stay where they are.", wrap=False))
        sb = QWidget()
        sbh = QHBoxLayout(sb)
        sbh.setContentsMargins(0, 0, 0, 0)
        sbh.setSpacing(8)
        b = button("Keep syncing")
        b.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        sbh.addWidget(b)
        b = button("Stop syncing", "danger")
        b.clicked.connect(self._remove)
        sbh.addWidget(b)
        sh.addWidget(sb)
        self.stack.addWidget(sure)
        # the stack is as tall as the page showing
        self.stack.currentChanged.connect(lambda i: self._fit_stack())
        v.addWidget(self.stack)
        self._fit_stack()
        return ed

    def _fit_stack(self):
        for i in range(self.stack.count()):
            self.stack.widget(i).setSizePolicy(
                QSizePolicy.Preferred,
                QSizePolicy.Preferred if i == self.stack.currentIndex() else QSizePolicy.Ignored)
        self.stack.adjustSize()

    # ---------------------------------------------------------------- folder rows
    def add_row(self, member: Member | None = None, focus: bool = False) -> FolderRow:
        row = FolderRow(self, member)
        row.set_suggestions(self._suggestions())
        row.remove_requested.connect(self.remove_row)
        row.submit.connect(self.apply)
        self.rows_box.addWidget(row)
        self.rows.append(row)
        if focus:
            row.path.setFocus()
            QTimer.singleShot(0, lambda: self.win.keep_visible(self))
        return row

    def remove_row(self, row: FolderRow):
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _fill_rows(self, members):
        """Show the job's folders, re-using the rows already built when they're the same."""
        if [r.id for r in self.rows] != [m.id for m in members]:
            for r in list(self.rows):
                self.remove_row(r)
            for m in members:
                self.add_row(m)
            return
        for r, m in zip(self.rows, members):
            r.load(m)

    def _suggestions(self):
        """Offer OneDrive\\<name of Main> in each folder's list (never filled in by itself)."""
        main = self.main_box.text().strip().strip('"')
        name = os.path.basename(os.path.normpath(main)) if main else ""
        return [os.path.join(ONEDRIVE, name)] if ONEDRIVE and name else []

    def _suggest(self, *_):
        items = self._suggestions()
        for r in self.rows:
            r.set_suggestions(items)

    # ---------------------------------------------------------------- open / close
    def toggle(self):
        if self.is_new:
            return
        self.win.set_expanded(None if self.win.expanded == self.key else self.key)

    def expand(self, reset: bool = True, animate: bool = True):
        if reset and not self.is_new:
            self.main_box.setText(self.job.main)
            self._fill_rows(self.job.members)
        if not self.rows:
            self.add_row()
        self.show_error("")
        self.stack.setCurrentIndex(0)
        self._activity_sig = None
        if not self.is_new:
            self._show_activity()
        self._set_open(True)
        self.drawer.open(animate)

    def collapse(self, then=None, animate: bool = True):
        self._set_open(False)
        self.drawer.close(then, animate)

    def _set_open(self, on: bool):
        self.setProperty("open", on)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def opened(self) -> bool:
        return self.drawer.opened

    # ---------------------------------------------------------------- live status
    def update_status(self, st, job):
        self.job = job
        color = theme.STATE_COLOR.get(st.state, theme.STATE_COLOR["paused"])
        if self._state.get("dot") != color:
            self._state["dot"] = color
            self._paint_dot(color)
        bad = st.state in ("error", "empty")
        if self._state.get("bad") != bad:
            self._state["bad"] = bad
            self.status.setProperty("role", "error" if bad else "muted")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        set_text(self.status, describe(st, job))
        p = st.progress
        busy = st.state == "syncing" and p is not None and p.items_total > 0
        if busy:
            self.bar.setValue(int(p.fraction * 1000))
            set_text(self.detail, progress_detail(p))
        if self._state.get("busy") != busy:
            self._state["busy"] = busy
            self.bar.setVisible(busy)
            self.detail.setVisible(busy)
        empty = st.state == "empty"
        if self._state.get("empty") != empty:
            self._state["empty"] = empty
            self.fix.setVisible(empty)
        if self._state.get("paused") != job.paused:
            self._state["paused"] = job.paused
            self.pause_btn.setText("Resume" if job.paused else "Pause")
            self.sync_btn.setEnabled(not job.paused)
        if self.opened:
            self._show_activity()

    def _show_activity(self):
        items = self.win.ctrl.recent_activity(self.job.id, n=6)
        sig = tuple(items)
        if sig == self._activity_sig:
            return
        self._activity_sig = sig
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(2)
        g.setColumnStretch(1, 1)
        if not items:
            g.addWidget(label("Nothing yet.", "muted"), 0, 0, 1, 2)
        for i, (t, level, msg) in enumerate(items):
            g.addWidget(label(time.strftime("%I:%M %p", time.localtime(t)).lstrip("0"), "muted"),
                        i, 0, Qt.AlignTop)
            g.addWidget(label(msg, "error" if level == "error" else "muted", wrap=True), i, 1)
        if self.activity is not None:
            self.activity_holder.removeWidget(self.activity)
            self.activity.deleteLater()
        self.activity = box
        self.activity_holder.addWidget(box)

    # ---------------------------------------------------------------- actions
    def show_error(self, text: str):
        self.err.setText(text)
        self.err.setVisible(bool(text))

    def values(self):
        main = self.main_box.text().strip().strip('"')
        members = [m for m in (r.value() for r in self.rows) if m is not None]
        return main, members

    def fill(self, folders) -> None:
        """Put dropped folders into empty boxes: Main first, then connected folders."""
        for p in folders:
            if not self.main_box.text().strip():
                self.main_box.setText(p)
                continue
            empty = next((r for r in self.rows if not r.path.text().strip()), None)
            (empty or self.add_row()).path.setText(p)

    def on_drop(self, row, paths) -> None:
        """Folders dropped on a box (row=None means Main); extra ones become more rows."""
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders:
            self.show_error("That's a file. Drop a folder instead.")
            return
        self.show_error("")
        first, rest = folders[0], folders[1:]
        (self.main_box if row is None else row.path).setText(first)
        for p in rest:
            empty = next((r for r in self.rows if not r.path.text().strip()), None)
            (empty or self.add_row()).path.setText(p)

    def browse(self, box, what):
        start = box.text().strip()
        p = QFileDialog.getExistingDirectory(self.win, f"Choose {what}",
                                             start if os.path.isdir(start) else "")
        if p:
            box.setText(os.path.normpath(p))

    @staticmethod
    def open_path(path):
        path = path.strip().strip('"')
        if os.path.isdir(path):
            open_folder(path)

    def _toggle_pause(self):
        self.win.ctrl.set_paused(self.job.id, not self.job.paused)

    def _remove(self):
        jid = self.job.id
        self.win.expanded = None
        self.collapse(then=lambda: self.win.remove_card(self, jid))

    def cancel(self):
        if self.is_new:
            self.collapse(then=self.win.close_draft)
        else:
            self.win.set_expanded(None)

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
            self.win.set_expanded(None)
            return
        problem = self.win.prepare_folders(main, [m.path for m in members],
                                           ignore=None if self.is_new else self.job.id)
        if problem:
            self.show_error(problem)
            return
        if self.is_new:
            # start syncing once the card has slid shut (so the list changes only then)
            self.collapse(then=lambda: (self.win.ctrl.add_job(main, members),
                                        self.win.close_draft()))
        else:
            jid = self.job.id
            self.win.expanded = None
            self.collapse(then=lambda: (self.win.ctrl.update_job(jid, main, members),
                                        self.win.after_change()))
