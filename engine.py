"""Two-way folder sync logic.

For every item, the current state of each side is compared with the state recorded
after the last successful sync. That tells us which side changed:

    only one side changed  -> copy (or delete) that change to the other side
    both sides changed     -> the newer file wins; the older one goes to the Recycle Bin
    deleted vs. modified   -> the modification wins (nothing is lost)

Every delete goes to the Recycle Bin, and every copy is written to a temporary
file first and then swapped in, so a crash never leaves a half-written file.
"""
from __future__ import annotations

import filecmp
import fnmatch
import os
import shutil
import stat as st_mod
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from store import Pair, StateDB

# Files and folders that are never synced.
IGNORED = [
    "~$*", ".~lock.*", "*.tmp", "*.temp", "*.crdownload", "*.part", "~*.tmp",
    "desktop.ini", "thumbs.db", ".ds_store", "$recycle.bin", "system volume information",
    ".849c9593-d756-4e56-8d6e-42412f2a707b",  # OneDrive's hidden marker file
    ".syncmate-*",                            # our own temp files and fallback bin
]
_IGNORED = [p.lower() for p in IGNORED]

TEMP_PREFIX = ".syncmate-"
FALLBACK_BIN = ".syncmate-recycle"
MTIME_TOLERANCE_NS = 2_000_000_000  # FAT/exFAT drives store times in 2-second steps
EMPTY_GUARD_MIN = 20  # a side that suddenly lost >= this many items makes us stop and ask

_JUNCTION_TAGS = (0xA0000003, 0xA000000C)  # mount point / junction, symlink


def is_ignored(name: str) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p) for p in _IGNORED)


def rel_is_ignored(rel: str) -> bool:
    return any(is_ignored(part) for part in rel.split(os.sep) if part)


def key_of(rel: str) -> str:
    """Comparison key: case-insensitive on Windows, like the file system."""
    return os.path.normcase(rel)


def _to_stat(s) -> tuple | None:
    if st_mod.S_ISDIR(s.st_mode):
        if getattr(s, "st_reparse_tag", 0) in _JUNCTION_TAGS:
            return None  # don't follow junctions/symlinks (avoids loops)
        return (True, 0, 0)
    if st_mod.S_ISREG(s.st_mode):
        return (False, s.st_size, s.st_mtime_ns)
    return None  # symlinks, devices, ...


def stat_path(path: str) -> tuple | None:
    try:
        return _to_stat(os.lstat(path))
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None


def scan(root: str, rel: str = "") -> dict:
    """Map key -> (rel, stat) for `rel` and everything below it.

    Raises OSError if a folder can't be read, so an unreadable folder is never
    mistaken for an empty one (which would look like a mass deletion).
    """
    out: dict = {}
    base = os.path.join(root, rel) if rel else root
    if rel:
        s = stat_path(base)
        if s is None:
            return out
        out[key_of(rel)] = (rel, s)
        if not s[0]:
            return out
    stack = [(base, rel)]
    while stack:
        d, drel = stack.pop()
        try:
            it = os.scandir(d)
        except FileNotFoundError:
            continue  # vanished mid-scan; the watcher will tell us about it
        with it:
            for e in it:
                if e.name.startswith(TEMP_PREFIX) and e.name.endswith(".tmp"):
                    _remove_stale_temp(e.path)
                    continue
                if is_ignored(e.name):
                    continue
                erel = os.path.join(drel, e.name) if drel else e.name
                try:
                    s = None if e.is_symlink() else _to_stat(e.stat(follow_symlinks=False))
                except FileNotFoundError:
                    continue
                if s is None:
                    continue
                out[key_of(erel)] = (erel, s)
                if s[0]:
                    stack.append((e.path, erel))
    return out


def _remove_stale_temp(path: str) -> None:
    """Delete a half-copied temp file left behind if SyncMate was killed mid-copy."""
    try:
        if time.time() - os.stat(path).st_ctime > 3600:
            os.remove(path)
    except OSError:
        pass


class Busy(OSError):
    """The file changed or was locked while we were copying it; try again later."""


class RootMissing(Exception):
    def __init__(self, side: int, path: str):
        super().__init__(f"Folder not found: {path}")
        self.side, self.path = side, path


class LooksEmpty(Exception):
    def __init__(self, side: int, path: str):
        super().__init__(f"{path} is suddenly empty")
        self.side, self.path = side, path


@dataclass
class Result:
    copied: int = 0
    deleted: int = 0
    renamed: int = 0
    replaced: int = 0
    failed: list = field(default_factory=list)  # rel paths to retry

    @property
    def changes(self) -> int:
        return self.copied + self.deleted + self.renamed + self.replaced


def default_trash(path: str, root: str) -> None:
    """Send to the Recycle Bin; if Windows refuses, park it in a hidden folder instead."""
    try:
        from send2trash import send2trash
        send2trash(path)
        return
    except Exception:
        if not os.path.lexists(path):
            return
    rel = os.path.relpath(path, root)
    stamp = time.strftime("%Y-%m-%d %H%M%S")
    dest = os.path.join(root, FALLBACK_BIN, stamp, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)


Log = Callable[[str, str, str], None]  # (pair_id, level, message)


class Engine:
    def __init__(self, db: StateDB, log: Log | None = None,
                 trash: Callable[[str, str], None] = default_trash):
        self.db = db
        self.log = log or (lambda pid, level, msg: None)
        self.trash = trash

    # ------------------------------------------------------------------ public

    def reconcile(self, pair: Pair, rel: str = "", allow_empty: bool = False) -> Result:
        """Bring `rel` (and everything below it) in sync on both sides. rel="" = everything."""
        roots = (pair.left, pair.right)
        for i, r in enumerate(roots):
            if not os.path.isdir(r):
                raise RootMissing(i, r)

        scans = (scan(roots[0], rel), scan(roots[1], rel))
        base = self.db.get_subtree(pair.id, key_of(rel))

        if rel == "" and len(base) >= EMPTY_GUARD_MIN and not allow_empty:
            for i in (0, 1):
                if not scans[i] and scans[1 - i]:
                    raise LooksEmpty(i, roots[i])

        res = Result()
        skip = None
        for k in sorted(set(scans[0]) | set(scans[1]) | set(base)):
            if skip and k.startswith(skip):
                continue
            skip = None
            try:
                if self._sync_one(pair, k, scans, base, res):
                    skip = k + os.sep
            except OSError as e:
                item_rel = (scans[0].get(k) or scans[1].get(k) or base[k])[0]
                res.failed.append(item_rel)
                if isinstance(e, Busy):
                    self.log(pair.id, "info", f"Waiting for “{item_rel}” (in use)")
                else:
                    self.log(pair.id, "error", f"Couldn't sync “{item_rel}”: {e.strerror or e}")
        self.db.commit()
        return res

    def mirror_move(self, pair: Pair, side: int, src_rel: str, dst_rel: str) -> bool:
        """A rename/move happened on `side`; repeat it on the other side instead of
        deleting and re-copying. Returns False if it's not safe, so a normal sync runs."""
        roots = (pair.left, pair.right)
        other = 1 - side
        ks, kd = key_of(src_rel), key_of(dst_rel)
        rec = self.db.get(pair.id, ks)
        if rec is None:
            return False
        a_dst = stat_path(os.path.join(roots[side], dst_rel))
        if a_dst is None or a_dst[0] != rec[1][0]:
            return False

        if ks == kd:  # only the capitalisation changed (Windows)
            actual = _actual_rel(roots[other], src_rel)
            if not actual or actual == dst_rel:
                return False
            os.rename(os.path.join(roots[other], actual), os.path.join(roots[other], dst_rel))
        else:
            if stat_path(os.path.join(roots[side], src_rel)) is not None:
                return False
            b_src_path = os.path.join(roots[other], src_rel)
            b_src = stat_path(b_src_path)
            if b_src is None or b_src != rec[1 + other]:
                return False  # other side changed too; let the normal sync decide
            b_dst_path = os.path.join(roots[other], dst_rel)
            if os.path.lexists(b_dst_path):
                return False
            os.makedirs(os.path.dirname(b_dst_path) or roots[other], exist_ok=True)
            os.rename(b_src_path, b_dst_path)

        self.db.move_subtree(pair.id, ks, kd, dst_rel)
        self.db.commit()
        self.log(pair.id, "info", f"Renamed “{src_rel}” → “{dst_rel}”")
        return True

    # ------------------------------------------------------------------ internals

    def _sync_one(self, pair: Pair, k: str, scans, base, res: Result) -> bool:
        """Sync one item. Returns True if its whole subtree was handled."""
        roots = (pair.left, pair.right)
        cur = [scans[i][k][1] if k in scans[i] else None for i in (0, 1)]
        rec = base.get(k)
        was = (rec[1], rec[2]) if rec else (None, None)
        rel = next(scans[i][k][0] for i in (0, 1) if k in scans[i]) if (cur[0] or cur[1]) else rec[0]

        changed = [cur[i] != was[i] for i in (0, 1)]
        if changed[0] or changed[1]:
            # Directory listings can lag behind on NTFS; confirm with a fresh stat.
            cur = [stat_path(os.path.join(roots[i], rel)) for i in (0, 1)]
            changed = [cur[i] != was[i] for i in (0, 1)]
        if not (changed[0] or changed[1]):
            return False

        if changed[0] != changed[1]:
            src = 0 if changed[0] else 1
            return self._one_way(pair, k, rel, src, cur, scans, base, res)

        # Both sides changed (or the item is new on both sides).
        a, b = cur
        if a is None and b is None:
            self.db.delete_subtree(pair.id, k)
        elif a is None or b is None:
            src = 0 if a is not None else 1  # a modification beats a deletion
            self._copy_over(pair, k, rel, src, cur, res)
        elif a[0] and b[0]:
            self._record(pair, k, rel)
        elif a[0] != b[0]:
            self.log(pair.id, "error",
                     f"“{rel}” is a file on one side and a folder on the other; skipped")
        elif self._same_file(roots, rel, a, b):
            self._record(pair, k, rel)
        else:
            winner = 0 if a[2] >= b[2] else 1
            loser = 1 - winner
            self.trash(os.path.join(roots[loser], rel), roots[loser])
            cur[loser] = None
            self._copy_over(pair, k, rel, winner, cur, res, quiet=True)
            res.replaced += 1
            self.log(pair.id, "warn",
                     f"“{rel}” changed on both sides — kept the newer one, "
                     f"older copy is in the Recycle Bin")
        return False

    def _one_way(self, pair, k, rel, src, cur, scans, base, res) -> bool:
        roots = (pair.left, pair.right)
        dst = 1 - src
        s, d = cur[src], cur[dst]
        if s is not None:
            self._copy_over(pair, k, rel, src, cur, res)
            return False

        # Deleted on `src`.
        if d is None:
            self.db.delete_subtree(pair.id, k)
            return False
        if d[0] and self._has_new_content(k, dst, scans, base):
            # Folder was deleted on one side but got new files on the other: keep it.
            os.makedirs(os.path.join(roots[src], rel), exist_ok=True)
            self._record(pair, k, rel)
            return False
        self.trash(os.path.join(roots[dst], rel), roots[dst])
        self.db.delete_subtree(pair.id, k)
        res.deleted += 1
        self.log(pair.id, "info", f"Moved “{rel}” to the Recycle Bin ({_side_name(dst)})")
        return bool(d[0])

    def _has_new_content(self, k, side, scans, base) -> bool:
        prefix = k + os.sep
        for kk, (_, s) in scans[side].items():
            if kk.startswith(prefix):
                rec = base.get(kk)
                if rec is None or rec[1 + side] != s:
                    return True
        return False

    def _copy_over(self, pair, k, rel, src, cur, res, quiet=False) -> None:
        roots = (pair.left, pair.right)
        dst = 1 - src
        src_path, dst_path = os.path.join(roots[src], rel), os.path.join(roots[dst], rel)
        s, d = cur[src], cur[dst]
        if s[0]:
            if d is not None and not d[0]:
                self.trash(dst_path, roots[dst])
            os.makedirs(dst_path, exist_ok=True)
        else:
            if d is not None and d[0]:
                self.trash(dst_path, roots[dst])
            _safe_copy(src_path, dst_path, s)
            res.copied += 0 if quiet else 1
            if not quiet:
                self.log(pair.id, "info", f"Copied “{rel}” → {_side_name(dst)}")
        self._record(pair, k, rel)

    def _record(self, pair, k, rel) -> None:
        l = stat_path(os.path.join(pair.left, rel))
        r = stat_path(os.path.join(pair.right, rel))
        if l is not None and r is not None:
            self.db.put(pair.id, k, rel, l, r)

    @staticmethod
    def _same_file(roots, rel, a, b) -> bool:
        if a[1] != b[1]:
            return False
        if abs(a[2] - b[2]) <= MTIME_TOLERANCE_NS:
            return True
        return filecmp.cmp(os.path.join(roots[0], rel), os.path.join(roots[1], rel),
                           shallow=False)


def _side_name(i: int) -> str:
    return "right" if i == 1 else "left"


def _actual_rel(root: str, rel: str) -> str | None:
    """The real (on-disk) spelling of `rel`, case-insensitively."""
    parent, name = os.path.split(rel)
    try:
        for n in os.listdir(os.path.join(root, parent) if parent else root):
            if os.path.normcase(n) == os.path.normcase(name):
                return os.path.join(parent, n) if parent else n
    except OSError:
        pass
    return None


def _safe_copy(src: str, dst: str, expected: tuple) -> None:
    parent = os.path.dirname(dst)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}.tmp")
    try:
        try:
            shutil.copy2(src, tmp)
        except PermissionError as e:
            raise Busy(e.errno, e.strerror, src) from e
        if stat_path(src) != expected:
            raise Busy(0, "file is still changing", src)
        try:
            os.replace(tmp, dst)
        except PermissionError:
            if not os.path.exists(dst):
                raise
            try:  # read-only target: clear the flag and retry
                os.chmod(dst, st_mod.S_IWRITE | st_mod.S_IREAD)
                os.replace(tmp, dst)
            except PermissionError as e:
                raise Busy(e.errno, e.strerror, dst) from e
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
