"""Small custom controls: a toggle switch, a segmented picker, a wrapping row layout,
the tree line that links folders, and path boxes that accept dropped folders."""
from __future__ import annotations

import os

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLayout, QLineEdit, QSizePolicy,
                               QWidget)

import theme


def _anim(owner, ms: int, on_value, curve=QEasingCurve.OutCubic) -> QVariantAnimation:
    a = QVariantAnimation(owner)
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.valueChanged.connect(on_value)
    return a


# ---------------------------------------------------------------------------- toggle switch
class ToggleSwitch(QAbstractButton):
    """A Material/Fluent style on/off switch whose knob slides."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._a = _anim(self, 160, self._set_pos)
        self.toggled.connect(self._animate)

    def _set_pos(self, v):
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool):
        self._a.stop()
        self._a.setStartValue(self._pos)
        self._a.setEndValue(1.0 if on else 0.0)
        self._a.start()

    def setChecked(self, on: bool):          # noqa: N802 (Qt naming)
        super().setChecked(on)
        self._a.stop()
        self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        w = 40 + (10 + self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, 24)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.pal()
        track = QRectF(1, 3, 38, 18)
        on = QColor(c["accent"])
        off = QColor(c["border_strong"])
        if not self.isEnabled():
            on = off = QColor(c["track"])
        col = QColor(
            round(off.red() + (on.red() - off.red()) * self._pos),
            round(off.green() + (on.green() - off.green()) * self._pos),
            round(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, 9, 9)
        knob = 12 + 2 * self._pos
        x = 4 + (38 - 8 - 12) * self._pos + 6
        p.setBrush(QColor(c["on_accent"] if self._pos > 0.5 else c["surface"]))
        p.drawEllipse(QPointF(x + 1, 12), knob / 2, knob / 2)
        if self.text():
            p.setPen(QColor(c["text"] if self.isEnabled() else c["faint"]))
            p.drawText(QRect(48, 0, self.width() - 48, self.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, self.text())


# ---------------------------------------------------------------------------- segmented
class Segmented(QWidget):
    """Auto | Light | Dark: a pill with a highlight that slides to the chosen option."""

    changed = Signal(str)

    def __init__(self, options, value, parent=None, seg_w: int = 74, h: int = 32):
        super().__init__(parent)
        self.options = list(options)          # [(value, label), ...]
        self.seg_w, self.h = seg_w, h
        self.index = next((i for i, (v, _) in enumerate(self.options) if v == value), 0)
        self._x = float(self.index)
        self._a = _anim(self, 180, self._set_x)
        self.setFixedSize(seg_w * len(self.options) + 6, h)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def _set_x(self, v):
        self._x = float(v)
        self.update()

    def value(self) -> str:
        return self.options[self.index][0]

    def select(self, index: int, notify: bool = True, animate: bool = True):
        index = max(0, min(len(self.options) - 1, index))
        changed = index != self.index
        self.index = index
        self._a.stop()
        if animate:
            self._a.setStartValue(self._x)
            self._a.setEndValue(float(index))
            self._a.start()
        else:
            self._set_x(float(index))
        if changed and notify:
            self.changed.emit(self.options[index][0])

    def set_value(self, value: str, notify: bool = False):
        i = next((i for i, (v, _) in enumerate(self.options) if v == value), self.index)
        self.select(i, notify=notify, animate=False)

    def mousePressEvent(self, e):
        self.select(int((e.position().x() - 3) // self.seg_w))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Left:
            self.select(self.index - 1)
        elif e.key() == Qt.Key_Right:
            self.select(self.index + 1)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.pal()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["track"]))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self.h / 2, self.h / 2)
        p.setBrush(QColor(c["accent"]))
        p.drawRoundedRect(QRectF(3 + self._x * self.seg_w, 3, self.seg_w, self.h - 6),
                          (self.h - 6) / 2, (self.h - 6) / 2)
        for i, (_, label) in enumerate(self.options):
            near = max(0.0, 1 - abs(self._x - i))
            p.setPen(QColor(c["on_accent"] if near > 0.5 else c["muted"]))
            p.drawText(QRect(3 + i * self.seg_w, 0, self.seg_w, self.h), Qt.AlignCenter, label)


# ---------------------------------------------------------------------------- flow layout
class FlowLayout(QLayout):
    """Lays its items out in a row and wraps onto the next line when they don't fit."""

    def __init__(self, parent=None, hspacing: int = 8, vspacing: int = 8):
        super().__init__(parent)
        self._items = []
        self.hs, self.vs = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):                  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):                      # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):                      # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):            # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self):              # noqa: N802
        return True

    def heightForWidth(self, w):              # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):              # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):                       # noqa: N802
        return self.minimumSize()

    def minimumSize(self):                    # noqa: N802
        s = QSize()
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            if it.isEmpty():
                continue
            hint = it.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y = rect.x(), y + line_h + self.vs
                line_h = 0
            if not test:
                # centre each item vertically in its line
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.hs
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


# ---------------------------------------------------------------------------- tree line
class TreeLine(QWidget):
    """The line linking Main to a connected folder: it comes down from above and turns,
    with a rounded corner, towards the folder's badge. The last one ends there."""

    def __init__(self, badge: QWidget, last: bool, parent=None):
        super().__init__(parent)
        self.badge, self.last = badge, last
        self.setFixedWidth(22)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.pal()["line"]), 1.5)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        x, r = 6.5, 7.0
        mid = self.badge.mapTo(self.parentWidget(), QPoint(0, self.badge.height() // 2)).y() \
            - self.y() + 0.5
        path = QPainterPath()
        path.moveTo(x, 0)
        path.lineTo(x, mid - r)
        path.quadTo(x, mid, x + r, mid)
        path.lineTo(self.width(), mid)
        p.drawPath(path)
        if not self.last:
            p.drawLine(QPointF(x, mid - r), QPointF(x, self.height()))


# ---------------------------------------------------------------------------- folder boxes
def dropped_folders(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    return [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]


class _DropMixin:
    """Accept folders dragged from File Explorer: highlight while over, then call
    self.on_drop(list_of_paths)."""
    on_drop = None

    def _drop_init(self):
        self.setAcceptDrops(True)

    def _hl(self, on):
        self.setProperty("drop", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):              # noqa: N802
        if dropped_folders(e.mimeData()):
            e.acceptProposedAction()
            self._hl(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):               # noqa: N802
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):              # noqa: N802
        self._hl(False)

    def dropEvent(self, e):                   # noqa: N802
        self._hl(False)
        paths = dropped_folders(e.mimeData())
        if paths and self.on_drop:
            e.acceptProposedAction()
            self.on_drop(paths)


class PathEdit(_DropMixin, QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_init()
        self.setPlaceholderText("Type a path, Browse, or drag a folder here")


class PathCombo(_DropMixin, QComboBox):
    """An editable folder box with suggestions in its list (never filled in by itself)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText("Type a path, Browse, or drag a folder here")
        self.lineEdit().setAcceptDrops(False)        # let the box itself take the drop
        self._drop_init()

    def text(self) -> str:
        return self.currentText()

    def setText(self, t: str):                 # noqa: N802
        self.setEditText(t)

    def set_suggestions(self, items):
        t = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(items)
        self.setEditText(t)
        self.blockSignals(False)


# ---------------------------------------------------------------------------- edge row
class EdgeLayout(QLayout):
    """Two groups: one at the left edge, one at the right. When they don't both fit on
    one line, the right group moves to its own line underneath (still on the right)."""

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self.gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):                  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):                      # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):                      # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):            # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self):              # noqa: N802
        return True

    def heightForWidth(self, w):              # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):              # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):                       # noqa: N802
        return self.minimumSize()

    def minimumSize(self):                    # noqa: N802
        s = QSize()
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.sizeHint())
        return s

    def _do(self, rect, test):
        shown = [it for it in self._items if not it.isEmpty()]
        if not shown:
            return 0
        left = shown[0] if len(shown) > 1 else None
        right = shown[-1]
        lw = left.sizeHint().width() if left else 0
        rw, rh = right.sizeHint().width(), right.sizeHint().height()
        lh = left.sizeHint().height() if left else 0
        one_line = left is None or lw + self.gap + rw <= rect.width()
        if not test:
            if left:
                left.setGeometry(QRect(rect.x(), rect.y(), lw, lh))
            ry = rect.y() if one_line else rect.y() + lh + self.gap
            right.setGeometry(QRect(rect.right() + 1 - rw, ry, rw, rh))
        return max(lh, rh) if one_line else lh + self.gap + rh
