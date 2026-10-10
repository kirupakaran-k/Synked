"""Small helpers shared by the window and the folder-pair cards."""
from __future__ import annotations

import os
import subprocess
import time

from icon import COLORS

IS_WIN = os.name == "nt"
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


def open_folder(path: str) -> None:
    if IS_WIN:
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


def pretty_path(path: str) -> str:
    if ONEDRIVE and os.path.normcase(path).startswith(os.path.normcase(ONEDRIVE)):
        path = "OneDrive" + path[len(ONEDRIVE):]
    return path


def elide(s: str, n: int = 46) -> str:
    return s if len(s) <= n else s[: n // 2 - 1] + "…" + s[-(n // 2):]


def folder_name(path: str) -> str:
    return os.path.basename(path.rstrip("\\/")) or path


def human_bytes(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("bytes", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def synced_at(t: float) -> str:
    """Local time and day, e.g. '12:38 PM, Saturday 10 Oct'."""
    lt = time.localtime(t)
    clock = time.strftime("%I:%M %p", lt).lstrip("0")
    return f"{clock}, {time.strftime('%A', lt)} {lt.tm_mday} {time.strftime('%b', lt)}"


_VERB = {"copy": "Copying", "update": "Updating", "rename": "Renaming"}


def describe(st, pair) -> str:
    """The main status line for a folder pair."""
    if st.state == "synced":
        return f"In sync · synced {synced_at(st.last_sync)}" if st.last_sync else "In sync"
    if st.state in ("syncing", "starting"):
        p = st.progress
        if p is None or not p.items_total:
            return "Checking for changes…"
        if not p.rel:
            return "Finishing up…"
        dest = folder_name(pair.right if p.to_side == 1 else pair.left)
        if p.action == "recycle":
            return f"Moving {elide(p.rel, 44)} to the Recycle Bin (in {dest})"
        return f"{_VERB.get(p.action, 'Syncing')} {elide(p.rel, 44)} → {dest}"
    if st.state == "paused":
        return "Paused"
    return st.message.replace(ONEDRIVE, "OneDrive") if ONEDRIVE else st.message


def progress_detail(p) -> str:
    """Second line under the progress bar, e.g. '3 of 12 items · 45.0 MB of 120.0 MB'."""
    text = f"{min(p.items_done, p.items_total)} of {p.items_total} item{'s' if p.items_total != 1 else ''}"
    if p.bytes_total:
        text += f" · {human_bytes(p.bytes_done)} of {human_bytes(p.bytes_total)}"
    return text
