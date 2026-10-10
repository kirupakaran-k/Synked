"""Two-way folder sync logic.

For every item, the current state of each side is compared with the state recorded
after the last successful sync. That tells us which side changed:

    only one side changed  -> copy (or delete) that change to the other side
    both sides changed     -> the newer file wins; the older one goes to the Recycle Bin
    deleted vs. modified   -> the modification wins (nothing is lost)

That is the "two-way" type. Each connected folder can instead use:

    mirror   Main -> folder: the folder becomes an exact copy of Main
    update   Main -> folder: new and changed files are copied; nothing is ever deleted
    collect  folder -> Main: the same, in the other direction

Replaced or deleted files go to the Recycle Bin, or, if the folder keeps versions, into
its hidden ".syncmate-versions" folder: one folder per sync moment, named like
"2026-10-10_12-38-05_001", holding the old files in their usual subfolders. Moments older
than the folder's chosen number of days are removed.

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
VERSIONS_DIR = ".syncmate-versions"
MOMENT_FORMAT = "%Y-%m-%d_%H-%M-%S"   # + "_001": one folder per sync moment
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


@dataclass
class Progress:
    """Live progress of one sync run, reported while it works."""
    items_total: int = 0
    items_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    action: str = ""      # copy | update | recycle | rename
    rel: str = ""         # the item being worked on
    to_side: int = 1      # which side it's going to (0 = left, 1 = right)

    @property
    def fraction(self) -> float:
        if self.bytes_total:
            return min(1.0, self.bytes_done / self.bytes_total)
        return min(1.0, self.items_done / self.items_total) if self.items_total else 0.0


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

    def reconcile(self, pair: Pair, rel: str = "", allow_empty: bool = False,
                  progress: Callable[[Progress], None] | None = None) -> Result:
        """Bring `rel` (and everything below it) in sync on both sides. rel="" = everything."""
        roots = (pair.left, pair.right)
        for i, r in enumerate(roots):
            if not os.path.isdir(r):
                raise RootMissing(i, r)

        scans = (scan(roots[0], rel), scan(roots[1], rel))
        base = self.db.get_subtree(pair.id, key_of(rel))

        mode = getattr(pair, "mode", "two-way")
        guarded = (0, 1) if mode == "two-way" else (0,) if mode == "mirror" else ()
        if rel == "" and len(base) >= EMPTY_GUARD_MIN and not allow_empty:
            for i in guarded:
                if not scans[i] and scans[1 - i]:
                    raise LooksEmpty(i, roots[i])

        res = Result()
        self._moments = {}   # one versions folder per side for this whole sync run
        keys = sorted(set(scans[0]) | set(scans[1]) | set(base))
        # What looks changed (for the progress bar); the real decision is made per item.
        todo = {k for k in keys if self._looks_changed(k, scans, base)}
        prog = self._prog = Progress(
            items_total=len(todo),
            bytes_total=sum(self._copy_size(k, scans) for k in todo))
        self._notify = progress or (lambda p: None)
        if todo:
            self._notify(prog)
        skip = None
        for k in keys:
            if skip and k.startswith(skip):
                prog.items_done += k in todo
                continue
            skip = None
            try:
                if self._sync_one(pair, k, scans, base, res):
                    skip = k + os.sep
                if k in todo:
                    prog.items_done += 1
                    self._notify(prog)
            except OSError as e:
                item_rel = (scans[0].get(k) or scans[1].get(k) or base[k])[0]
                res.failed.append(item_rel)
                if isinstance(e, Busy):
                    self._log(pair, "info", f"Waiting for “{item_rel}” (in use)")
                else:
                    self._log(pair, "error", f"Couldn't sync “{item_rel}”: {e.strerror or e}")
        self.db.commit()
        if todo:
            prog.items_done, prog.bytes_done, prog.rel = prog.items_total, prog.bytes_total, ""
            self._notify(prog)
        return res

    @staticmethod
    def _looks_changed(k, scans, base) -> bool:
        rec = base.get(k)
        was = (rec[1], rec[2]) if rec else (None, None)
        return any((scans[i][k][1] if k in scans[i] else None) != was[i] for i in (0, 1))

    @staticmethod
    def _copy_size(k, scans) -> int:
        sizes = [scans[i][k][1][1] for i in (0, 1) if k in scans[i] and not scans[i][k][1][0]]
        return max(sizes, default=0)

    def _report(self, action: str, rel: str, to_side: int) -> None:
        p = getattr(self, "_prog", None)
        if p is not None:
            p.action, p.rel, p.to_side = action, rel, to_side
            self._notify(p)

    def _add_bytes(self, n: int) -> None:
        p = getattr(self, "_prog", None)
        if p is not None:
            p.bytes_done += n
            self._notify(p)

    def mirror_move(self, pair: Pair, side: int, src_rel: str, dst_rel: str) -> bool:
        """A rename/move happened on `side`; repeat it on the other side instead of
        deleting and re-copying. Returns False if it's not safe, so a normal sync runs."""
        mode = getattr(pair, "mode", "two-way")
        if not (mode == "two-way" or (mode == "mirror" and side == 0)):
            return False  # update/collect never delete, so a rename is just a new file
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
        self._log(pair, "info", f"Renamed “{src_rel}” → “{dst_rel}” in {self._name(pair, other)}")
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

        self._was = was   # what both sides looked like at the last sync (for _copy_over)
        mode = getattr(pair, "mode", "two-way")
        if mode == "mirror":
            return self._mirror(pair, k, rel, 0, cur, res)
        if mode in ("update", "collect"):
            return self._update(pair, k, rel, 0 if mode == "update" else 1, cur, changed, res)

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
            where = self._discard(pair, loser, rel)
            cur[loser] = None
            self._copy_over(pair, k, rel, winner, cur, res, quiet=True)
            res.replaced += 1
            self._log(pair, "warn", f"“{rel}” changed in both Main and "
                                    f"{self._name(pair, 1)}. Kept the newer one; the older "
                                    f"copy is in {where}")
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
        self._remove(pair, k, rel, dst, res)
        return bool(d[0])

    # ---- the other sync types ---------------------------------------------------

    def _mirror(self, pair, k, rel, src, cur, res) -> bool:
        """Make the other side an exact copy of `src`."""
        dst = 1 - src
        s, d = cur[src], cur[dst]
        if s is None:
            if d is None:
                self.db.delete_subtree(pair.id, k)
                return False
            self._remove(pair, k, rel, dst, res)
            return bool(d[0])
        if d is not None and s[0] and d[0]:
            self._record(pair, k, rel)
        elif (d is not None and not s[0] and not d[0]
              and self._same_file((pair.left, pair.right), rel, *cur)):
            self._record(pair, k, rel)
        else:
            self._copy_over(pair, k, rel, src, cur, res)
        return False

    def _update(self, pair, k, rel, src, cur, changed, res) -> bool:
        """Copy new and changed files from `src` to the other side; never delete."""
        dst = 1 - src
        s, d = cur[src], cur[dst]
        if s is None:
            self.db.delete_subtree(pair.id, k)   # gone from the source: leave the copy be
            return False
        if d is None:
            self._copy_over(pair, k, rel, src, cur, res)
        elif s[0] and d[0]:
            self._record(pair, k, rel)
        elif not changed[src]:
            if s[0] == d[0]:
                self._record(pair, k, rel)       # only the copy was edited: leave it alone
        elif not s[0] and not d[0] and self._same_file((pair.left, pair.right), rel, *cur):
            self._record(pair, k, rel)
        else:
            self._copy_over(pair, k, rel, src, cur, res)
        return False

    # ---- removing and replacing ---------------------------------------------------

    def _remove(self, pair, k, rel, side, res) -> None:
        self._report("recycle", rel, side)
        where = self._discard(pair, side, rel)
        self.db.delete_subtree(pair.id, k)
        res.deleted += 1
        self._log(pair, "info", f"Removed “{rel}” from {self._name(pair, side)} "
                                f"(it's in {where})")

    def _discard(self, pair, side, rel) -> str:
        """Move an item out of the way: into old versions or the Recycle Bin."""
        root = (pair.left, pair.right)[side]
        if getattr(pair, "versions", False):
            keep_version(root, rel, self._moment(root))
            return "old versions"
        self.trash(os.path.join(root, rel), root)
        return "the Recycle Bin"

    def _moment(self, root: str) -> str:
        """The versions folder for this sync run in `root` (created when first needed)."""
        moments = getattr(self, "_moments", None)
        if moments is None:
            moments = self._moments = {}
        if root not in moments:
            moments[root] = new_moment(root)
        return moments[root]

    def _log(self, pair, level, msg) -> None:
        self.log(getattr(pair, "job", "") or pair.id, level, msg)

    @staticmethod
    def _name(pair, side) -> str:
        names = getattr(pair, "names", None) or ("left", "right")
        return names[side]

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
                self._discard(pair, dst, rel)
            os.makedirs(dst_path, exist_ok=True)
        else:
            if d is not None and d[0]:
                self._discard(pair, dst, rel)
                d = None
            was = getattr(self, "_was", (None, None))
            keep = None
            if d is not None and getattr(pair, "versions", False):
                keep = lambda: keep_version(roots[dst], rel, self._moment(roots[dst]))  # noqa: E731
            elif (d is not None and was[dst] != d
                  and getattr(pair, "mode", "two-way") != "two-way"):
                # The copy here was edited since the last sync: don't just overwrite it.
                def keep():
                    where = self._discard(pair, dst, rel)
                    self._log(pair, "warn", f"“{rel}” in {self._name(pair, dst)} had its own "
                                            f"changes; they're in {where}")
            self._report("update" if d is not None or quiet else "copy", rel, dst)
            # the old file is moved aside only once the new one is fully written
            _safe_copy(src_path, dst_path, s, self._add_bytes, before_replace=keep)
            res.copied += 0 if quiet else 1
            if not quiet:
                verb = "Updated" if d is not None else "Copied"
                versions = keep is not None and getattr(pair, "versions", False)
                self._log(pair, "info", f"{verb} “{rel}” → {self._name(pair, dst)}"
                                        + (" (old version kept)" if versions else ""))
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


def versions_root(root: str) -> str:
    """<root>/.syncmate-versions, created and (on Windows) marked hidden."""
    vroot = os.path.join(root, VERSIONS_DIR)
    if not os.path.isdir(vroot):
        os.makedirs(vroot, exist_ok=True)
        if os.name == "nt":
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                attrs = k32.GetFileAttributesW(vroot)
                if attrs != -1:
                    k32.SetFileAttributesW(vroot, attrs | 0x2)   # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                pass
    return vroot


def new_moment(root: str, when: float | None = None) -> str:
    """Create the folder for one sync moment, e.g. "2026-10-10_12-38-05_001"; the number
    counts up if several moments start in the same second. Returns its name."""
    vroot = versions_root(root)
    stamp = time.strftime(MOMENT_FORMAT, time.localtime(when))
    n = 1
    while True:
        name = f"{stamp}_{n:03d}"
        try:
            os.mkdir(os.path.join(vroot, name))
            return name
        except FileExistsError:
            n += 1


def keep_version(root: str, rel: str, moment: str) -> str:
    """Move `rel` (a file or a whole folder) into the moment's folder, keeping its usual
    subfolders, e.g. .syncmate-versions/2026-10-10_12-38-05_001/Reports/budget.xlsx.
    Returns where it went."""
    dest = os.path.join(root, VERSIONS_DIR, moment, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest):   # the same item twice in one moment: keep both
        stem, ext = os.path.splitext(dest)
        n = 2
        while os.path.lexists(f"{stem} ({n}){ext}"):
            n += 1
        dest = f"{stem} ({n}){ext}"
    os.replace(os.path.join(root, rel), dest)
    return dest


def moment_time(name: str) -> float | None:
    """When a moment folder was made, from its name (None if it isn't one of ours)."""
    try:
        stamp, num = name.rsplit("_", 1)
        if not num.isdigit():
            return None
        return time.mktime(time.strptime(stamp, MOMENT_FORMAT))
    except ValueError:
        return None


def prune_versions(root: str, keep_days: int, now: float | None = None) -> int:
    """Remove moment folders older than `keep_days` (0 = keep forever). Returns how many."""
    if keep_days <= 0:
        return 0
    vroot = os.path.join(root, VERSIONS_DIR)
    try:
        names = os.listdir(vroot)
    except OSError:
        return 0
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    removed = 0
    for name in names:
        t = moment_time(name)
        if t is not None and t < cutoff:
            try:
                shutil.rmtree(os.path.join(vroot, name))
                removed += 1
            except OSError:
                pass
    return removed


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


CHUNK = 1 << 20  # copy 1 MB at a time so progress can be reported


def _copy_data(src: str, dst: str, on_bytes: Callable[[int], None]) -> None:
    """Copy contents in chunks (reporting progress), then times/attributes like copy2."""
    with open(src, "rb") as fs, open(dst, "wb") as fd:
        while True:
            buf = fs.read(CHUNK)
            if not buf:
                break
            fd.write(buf)
            on_bytes(len(buf))
    shutil.copystat(src, dst)


def _safe_copy(src: str, dst: str, expected: tuple,
               on_bytes: Callable[[int], None] = lambda n: None,
               before_replace: Callable[[], object] | None = None) -> None:
    parent = os.path.dirname(dst)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f"{TEMP_PREFIX}{uuid.uuid4().hex[:8]}.tmp")
    try:
        try:
            _copy_data(src, tmp, on_bytes)
        except PermissionError as e:
            raise Busy(e.errno, e.strerror, src) from e
        if stat_path(src) != expected:
            raise Busy(0, "file is still changing", src)
        if before_replace is not None and os.path.exists(dst):
            before_replace()   # e.g. keep the old version
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
