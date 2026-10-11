"""Synked's look: light and dark palettes, the app-wide style sheet, and the small
icons, all drawn in code so they stay crisp at any display scaling."""
from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from icon import COLORS

# ---------------------------------------------------------------------------- colours
PALETTES = {
    "light": {
        "window": "#F6F7F9", "surface": "#FFFFFF", "surface2": "#F1F3F5",
        "border": "#E2E5E9", "border_strong": "#CDD2D8",
        "text": "#1B1C1F", "muted": "#5F6670", "faint": "#8A9099",
        "accent": "#005FB8", "accent_hover": "#0B6CC6", "accent_pressed": "#004E98",
        "on_accent": "#FFFFFF",
        "field": "#FFFFFF", "button": "#FFFFFF", "button_hover": "#F3F4F6",
        "button_pressed": "#E9EBEE",
        "line": "#BFC5CC", "track": "#E3E6EA",
        "danger": "#C42B1C", "danger_hover": "#AE2517", "danger_pressed": "#962012",
        "error": "#C42B1C", "warn": "#9A5B00", "selection": "#CFE3F7",
        "tooltip": "#2B2D31", "on_tooltip": "#F4F5F6",
    },
    "dark": {
        "window": "#17181B", "surface": "#202226", "surface2": "#26282D",
        "border": "#2F3238", "border_strong": "#41454D",
        "text": "#ECEDEF", "muted": "#A2A8B1", "faint": "#7A808A",
        "accent": "#57C8FF", "accent_hover": "#6FD0FF", "accent_pressed": "#45B4EA",
        "on_accent": "#0B0C0E",
        "field": "#1B1D20", "button": "#2A2D32", "button_hover": "#32353B",
        "button_pressed": "#3A3D44",
        "line": "#4D525B", "track": "#363A41",
        "danger": "#C42B1C", "danger_hover": "#AE2517", "danger_pressed": "#962012",
        "error": "#FF8A80", "warn": "#F0B860", "selection": "#1F4A6B",
        "tooltip": "#3A3D43", "on_tooltip": "#F4F5F6",
    },
}

STATE_COLOR = {
    "synced": COLORS["synced"], "syncing": COLORS["syncing"], "starting": COLORS["syncing"],
    "waiting": COLORS["syncing"], "paused": COLORS["paused"], "error": COLORS["error"],
    "empty": COLORS["error"],
}

_current = {"name": "light"}


def set_current(name: str) -> None:
    _current["name"] = name


def current() -> str:
    return _current["name"]


def pal() -> dict:
    """The colours of the theme in use right now."""
    return PALETTES[_current["name"]]


FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"]


# ---------------------------------------------------------------------------- painting
def _canvas(w: float, h: float, dpr: float) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(max(1, round(w * dpr)), max(1, round(h * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def dots_icon(color: str, size: int = 20, dpr: float = 2.0) -> QIcon:
    """Three vertical dots (⋮, 'more options')."""
    pm, p = _canvas(size, size, dpr)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    r = size * 0.09
    for cy in (0.24, 0.5, 0.76):
        p.drawEllipse(QPointF(size / 2, size * cy), r, r)
    p.end()
    return QIcon(pm)


# Android's settings gear (Material Icons "settings", Apache License 2.0, by Google)
_GEAR_PATH = ("M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58"
              "c0.18-0.14,0.23-0.41,0.12-0.61l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96"
              "c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41h-3.84"
              "c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33"
              "c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58"
              "C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58c-0.18,0.14-0.23,0.41-0.12,0.61"
              "l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54"
              "c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54"
              "c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32"
              "c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6"
              "s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z")


def gear_icon(color: str, size: int = 20, dpr: float = 2.0) -> QIcon:
    """The settings gear, as on Android."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path fill="{color}" d="{_GEAR_PATH}"/></svg>')
    pm, p = _canvas(size, size, dpr)
    QSvgRenderer(QByteArray(svg.encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    return QIcon(pm)


def plus_icon(color: str, size: int = 16, dpr: float = 2.0, weight: float = 0.13) -> QIcon:
    pm, p = _canvas(size, size, dpr)
    p.setPen(_pen(color, size * weight))
    a = size * 0.36
    p.drawLine(QPointF(size / 2 - a, size / 2), QPointF(size / 2 + a, size / 2))
    p.drawLine(QPointF(size / 2, size / 2 - a), QPointF(size / 2, size / 2 + a))
    p.end()
    return QIcon(pm)


def cross_icon(color: str, size: int = 14, dpr: float = 2.0) -> QIcon:
    """Bold ✕ for the red remove button."""
    pm, p = _canvas(size, size, dpr)
    p.setPen(_pen(color, size * 0.17))
    a = size * 0.3
    c = size / 2
    p.drawLine(QPointF(c - a, c - a), QPointF(c + a, c + a))
    p.drawLine(QPointF(c - a, c + a), QPointF(c + a, c - a))
    p.end()
    return QIcon(pm)


def pause_icon(color: str, size: int = 14, dpr: float = 2.0) -> QIcon:
    pm, p = _canvas(size, size, dpr)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    w, h = size * 0.24, size * 0.72
    for x in (size * 0.2, size * 0.56):
        p.drawRoundedRect(QRectF(x, (size - h) / 2, w, h), w * 0.3, w * 0.3)
    p.end()
    return QIcon(pm)


def play_icon(color: str, size: int = 14, dpr: float = 2.0) -> QIcon:
    pm, p = _canvas(size, size, dpr)
    p.setPen(_pen(color, size * 0.08))
    p.setBrush(QColor(color))
    tri = QPainterPath()
    tri.moveTo(size * 0.28, size * 0.16)
    tri.lineTo(size * 0.86, size * 0.5)
    tri.lineTo(size * 0.28, size * 0.84)
    tri.closeSubpath()
    p.drawPath(tri)
    p.end()
    return QIcon(pm)


def chevron_pixmap(color: str, size: int = 12, dpr: float = 2.0, up: bool = False) -> QPixmap:
    pm, p = _canvas(size, size, dpr)
    p.setPen(_pen(color, size * 0.12))
    y0, y1 = (size * 0.62, size * 0.36) if up else (size * 0.38, size * 0.64)
    p.drawLine(QPointF(size * 0.22, y0), QPointF(size / 2, y1))
    p.drawLine(QPointF(size / 2, y1), QPointF(size * 0.78, y0))
    p.end()
    return pm


def badge_pixmap(kind: str, fill: str, mark: str, w: int = 30, h: int = 22,
                 dpr: float = 2.0) -> QPixmap:
    """The sync-type badge at the end of a tree branch: a small filled box with arrows.
    kind: 'both' (⇄ two-way), 'right' (→ Main to folder), 'left' (← folder to Main)."""
    pm, p = _canvas(w, h, dpr)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(fill))
    p.drawRoundedRect(QRectF(0, 0, w, h), h * 0.24, h * 0.24)
    p.setBrush(QColor(mark))
    pen = _pen(mark, h * 0.105)

    def arrow(y, x0, x1, head, half):
        sign = 1 if x1 > x0 else -1
        base = x1 - sign * head
        p.setPen(pen)
        p.drawLine(QPointF(x0, y), QPointF(base + sign * 0.5, y))
        p.setPen(Qt.NoPen)
        tri = QPainterPath()
        tri.moveTo(x1, y)
        tri.lineTo(base, y - half)
        tri.lineTo(base, y + half)
        tri.closeSubpath()
        p.drawPath(tri)

    left, right = w * 0.24, w * 0.76
    if kind == "both":
        arrow(h * 0.36, left, right, h * 0.22, h * 0.15)
        arrow(h * 0.66, right, left, h * 0.22, h * 0.15)
    elif kind == "left":
        arrow(h * 0.5, right, left, h * 0.28, h * 0.2)
    else:
        arrow(h * 0.5, left, right, h * 0.28, h * 0.2)
    p.end()
    return pm


def status_dot(color: str, size: int = 10, dpr: float = 2.0) -> QPixmap:
    pm, p = _canvas(size, size, dpr)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return pm


# ---------------------------------------------------------------------------- style sheet
_ASSET_DIR = os.path.join(tempfile.gettempdir(), "synked-ui")


def _assets(theme: str, pal: dict) -> dict:
    """A few tiny images the style sheet refers to by file (Qt style sheets can't draw
    arrows themselves). Written once per theme into the temp folder."""
    os.makedirs(_ASSET_DIR, exist_ok=True)
    out = {}
    for name, color, up in (("down", pal["muted"], False), ("up", pal["muted"], True),
                            ("down_dis", pal["faint"], False)):
        path = os.path.join(_ASSET_DIR, f"chevron-{name}-{theme}.png")
        if not os.path.exists(path):
            chevron_pixmap(color, 12, 2.0, up).save(path)
        out[name] = path.replace("\\", "/")
    return out


def style_sheet(theme: str) -> str:
    p = PALETTES[theme]
    a = _assets(theme, p)
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p['text']}; font-size: 10pt; }}
QMainWindow, QDialog, #page {{ background: {p['window']}; }}
QScrollArea, QScrollArea > QWidget > QWidget#list {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 17pt; font-weight: 600; }}
QLabel[role="name"] {{ font-weight: 600; }}
QLabel[role="muted"] {{ color: {p['muted']}; font-size: 9pt; }}
QLabel[role="body-muted"] {{ color: {p['muted']}; }}
QLabel[role="section"] {{ font-weight: 600; }}
QLabel[role="error"] {{ color: {p['error']}; font-size: 9pt; }}
QLabel[role="warn"] {{ color: {p['warn']}; font-size: 9pt; }}
QLabel[role="faint"] {{ color: {p['faint']}; font-size: 9pt; }}

/* cards */
QFrame#card {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 10px; }}
QFrame#card:hover {{ border-color: {p['border_strong']}; }}
QFrame#card[open="true"] {{ border-color: {p['border_strong']}; }}
QFrame#divider {{ background: {p['border']}; border: none; min-height: 1px; max-height: 1px; }}

/* buttons */
QPushButton {{
    background: {p['button']}; border: 1px solid {p['border_strong']}; border-radius: 6px;
    padding: 0px 14px; min-height: 30px; max-height: 30px;
}}
QPushButton:hover {{ background: {p['button_hover']}; }}
QPushButton:pressed {{ background: {p['button_pressed']}; }}
QPushButton:disabled {{ color: {p['faint']}; }}
QPushButton[kind="primary"] {{
    background: {p['accent']}; color: {p['on_accent']}; border: 1px solid {p['accent']};
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton[kind="primary"]:pressed {{ background: {p['accent_pressed']}; }}
QPushButton[kind="danger"] {{
    background: {p['danger']}; color: #FFFFFF; border: 1px solid {p['danger']};
}}
QPushButton[kind="danger"]:hover {{ background: {p['danger_hover']}; border-color: {p['danger_hover']}; }}
QPushButton[kind="danger"]:pressed {{ background: {p['danger_pressed']}; }}
QPushButton[kind="icon"] {{ padding: 0px; min-width: 32px; max-width: 32px; }}
QPushButton[kind="hold"] {{
    background: {p['accent']}; border: 1px solid {p['accent']}; padding: 0px;
    min-width: 38px; max-width: 38px;
}}
QPushButton[kind="hold"]:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton[kind="hold"]:pressed {{ background: {p['accent_pressed']}; }}
QPushButton[kind="remove"] {{
    background: {p['danger']}; border: 1px solid {p['danger']}; padding: 0px;
    min-width: 38px; max-width: 38px;
}}
QPushButton[kind="remove"]:hover {{ background: {p['danger_hover']}; border-color: {p['danger_hover']}; }}
QPushButton[kind="remove"]:pressed {{ background: {p['danger_pressed']}; }}
QToolButton#more {{ border: none; border-radius: 6px; padding: 4px; background: transparent; }}
QToolButton#more:hover {{ background: {p['surface2']}; }}

/* text boxes and dropdowns */
QLineEdit, QComboBox {{
    background: {p['field']}; border: 1px solid {p['border_strong']};
    border-bottom: 2px solid {p['border_strong']};
    border-radius: 6px; padding: 0px 10px; min-height: 28px; max-height: 28px;
    selection-background-color: {p['accent']}; selection-color: {p['on_accent']};
}}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-bottom: 2px solid {p['accent']}; }}
QLineEdit[drop="true"], QComboBox[drop="true"] {{ border: 2px dashed {p['accent']}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding;
    subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({a['down']}); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ image: url({a['up']}); }}
QComboBox QAbstractItemView {{
    background: {p['surface']}; border: 1px solid {p['border_strong']}; border-radius: 6px;
    padding: 4px; selection-background-color: {p['selection']}; selection-color: {p['text']};
    outline: none;
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0px 8px; border-radius: 4px; }}
QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
    background: {p['selection']}; color: {p['text']}; }}

/* slider */
QSlider::groove:horizontal {{ height: 4px; background: {p['track']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['accent']}; border: 3px solid {p['surface']};
    width: 12px; height: 12px; margin: -7px 0px; border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}

/* thin progress bar (Material style) */
QProgressBar {{ background: {p['track']}; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; }}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 2px; }}

/* scroll bars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['faint']}; }}
QScrollBar[idle="true"]::handle:vertical {{ background: transparent; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* hints */
QToolTip {{ background: {p['tooltip']}; color: {p['on_tooltip']}; border: none;
    border-radius: 6px; padding: 6px 9px; font-size: 9pt; }}

/* menus (tray) */
QMenu {{ background: {p['surface']}; border: 1px solid {p['border_strong']}; border-radius: 8px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p['selection']}; color: {p['text']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}

/* activity log */
QTreeWidget {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 8px;
    alternate-background-color: {p['surface2']}; }}
QHeaderView::section {{ background: {p['surface2']}; color: {p['muted']}; border: none;
    border-bottom: 1px solid {p['border']}; padding: 6px 8px; font-weight: 600; }}

/* settings flyout */
QFrame#flyout {{ background: {p['surface']}; border: 1px solid {p['border_strong']};
    border-radius: 10px; }}
QMessageBox {{ background: {p['window']}; }}
"""
