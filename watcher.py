"""Watches folder pairs and runs the sync engine in a background thread.

The UI only talks to `Controller`; every method returns immediately and the real
work happens on the worker thread, so the window never freezes.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from engine import Engine, LooksEmpty, Result, RootMissing, default_trash, rel_is_ignored
from store import Config, Pair, StateDB, data_dir

DEBOUNCE = 1.5          # seconds of quiet before syncing a burst of changes
MAX_WAIT = 8.0          # ...but never wait longer than this
FULL_SCAN_EVERY = 600   # safety-net rescan of everything (seconds)
HEALTH_EVERY = 10       # check that folders still exist (seconds)
RETRY_AFTER = 15        # retry files that were in use (seconds)


@dataclass
class Status:
    state: str = "starting"   # starting | syncing | synced | waiting | paused | error | empty
    message: str = ""
    last_sync: float = 0.0
    empty_side: int = -1


@dataclass
class _Pending:
    rels: set = field(default_factory=set)
    moves: list = field(default_factory=list)
    full: bool = False


class _Handler(FileSystemEventHandler):
    def __init__(self, ctrl: "Controller", pid: str, side: int, root: str):
        self.ctrl, self.pid, self.side, self.root = ctrl, pid, side, root

    def _rel(self, path) -> str:
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        rel = os.path.relpath(path, self.root)
        return "" if rel in (".", "") or rel.startswith("..") else rel

    def on_any_event(self, event):
        t = event.event_type
        if t not in ("created", "modified", "deleted", "moved"):
            return
        if t == "modified" and event.is_directory:
            return
        src = self._rel(event.src_path)
        if t == "moved":
            dst = self._rel(event.dest_path)
            s_ok, d_ok = not rel_is_ignored(src), not rel_is_ignored(dst)
            if s_ok and d_ok and src and dst:
                self.ctrl._push(self.pid, move=(self.side, src, dst))
            else:
                if s_ok:
                    self.ctrl._push(self.pid, rel=src)
                if d_ok:
                    self.ctrl._push(self.pid, rel=dst)
            return
        if not rel_is_ignored(src):
            self.ctrl._push(self.pid, rel=src)


def _setup_file_log() -> logging.Logger:
    log = logging.getLogger("syncmate")
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(
            os.path.join(data_dir(), "syncmate.log"), maxBytes=1_000_000, backupCount=2,
            encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _overlaps(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    try:
        common = os.path.commonpath([a, b])
    except ValueError:  # different drives
        return False
    return common in (a, b)


class Controller:
    def __init__(self, config: Config | None = None, db: StateDB | None = None,
                 trash=default_trash, debounce: float = DEBOUNCE,
                 full_scan_every: float = FULL_SCAN_EVERY, save_config: bool = True):
        self.config = config or Config.load()
        self.db = db or StateDB()
        self.debounce = debounce
        self.full_scan_every = full_scan_every
        self.save_config = save_config
        self.activity: deque = deque(maxlen=300)
        self._file_log = _setup_file_log()
        self.engine = Engine(self.db, self._log, trash)

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._pending: dict[str, _Pending] = {}
        self._first_event = self._last_event = 0.0
        self._commands: list = []
        self._retry: list = []  # (due, pid, rel)
        self.status: dict[str, Status] = {}
        self._watches: dict[str, list] = {}
        self._last_full: dict[str, float] = {}
        self._observer = Observer()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="syncmate-worker", daemon=True)

    # ================================================================== public API

    def start(self) -> None:
        self._observer.start()
        for p in self.config.pairs:
            self.status[p.id] = Status(state="paused" if p.paused else "starting")
            if not p.paused:
                self._command("resume", p.id)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception:
            pass
        self.db.close()

    def pairs(self) -> list[Pair]:
        with self._lock:
            return [Pair(**vars(p)) for p in self.config.pairs]

    def snapshot(self) -> dict[str, Status]:
        with self._lock:
            return {k: Status(**vars(v)) for k, v in self.status.items()}

    def overall(self) -> str:
        """One word for the tray icon: synced | syncing | paused | error."""
        sts = list(self.snapshot().values())
        if any(s.state in ("error", "empty") for s in sts):
            return "error"
        if any(s.state in ("syncing", "starting", "waiting") for s in sts):
            return "syncing"
        if sts and all(s.state == "paused" for s in sts):
            return "paused"
        return "synced"

    def validate(self, left: str, right: str) -> str | None:
        """An error message, or None if the pair is OK to add."""
        if not left or not right:
            return "Choose both folders."
        for p in (left, right):
            if not os.path.isdir(p):
                return f"“{p}” is not a folder."
        if _overlaps(left, right):
            return "The folders can't be the same or inside each other."
        for pair in self.pairs():
            for new in (left, right):
                for old in (pair.left, pair.right):
                    if _overlaps(new, old):
                        return f"“{new}” overlaps a folder that's already being synced."
        return None

    def add_pair(self, left: str, right: str) -> Pair:
        pair = Pair(left=os.path.abspath(left), right=os.path.abspath(right))
        with self._lock:
            self.config.pairs.append(pair)
            self.status[pair.id] = Status()
            self._save()
        self._log(pair.id, "info", f"Started syncing {pair.left} ⇄ {pair.right}")
        self._command("resume", pair.id)
        return pair

    def remove_pair(self, pid: str) -> None:
        self._command("remove", pid)

    def set_paused(self, pid: str, paused: bool) -> None:
        self._command("pause" if paused else "resume", pid)

    def sync_now(self, pid: str | None = None) -> None:
        for p in self.pairs():
            if pid in (None, p.id) and not p.paused:
                self._push(p.id, full=True, now=True)

    def remember(self, **settings) -> None:
        """Save small app settings (e.g. tray_hint_shown=True)."""
        with self._lock:
            for k, v in settings.items():
                setattr(self.config, k, v)
            self._save()

    def resolve_empty(self, pid: str, apply_deletion: bool) -> None:
        """A folder suddenly became empty. Either delete on the other side too,
        or restore its files from the other side."""
        self._command("resolve", pid, apply_deletion)

    # ================================================================== internals

    def _save(self):
        if self.save_config:
            self.config.save()

    def _log(self, pid: str, level: str, msg: str) -> None:
        with self._lock:
            self.activity.appendleft((time.time(), pid, level, msg))
        getattr(self._file_log, {"warn": "warning"}.get(level, level), self._file_log.info)(msg)

    def _set(self, pid: str, **kw) -> None:
        with self._lock:
            st = self.status.setdefault(pid, Status())
            for k, v in kw.items():
                setattr(st, k, v)

    def _pair(self, pid: str) -> Pair | None:
        with self._lock:
            return next((p for p in self.config.pairs if p.id == pid), None)

    def _command(self, *cmd) -> None:
        with self._lock:
            self._commands.append(cmd)
        self._wake.set()

    def _push(self, pid: str, rel: str | None = None, move=None, full=False, now=False):
        with self._lock:
            p = self._pending.setdefault(pid, _Pending())
            if full or rel == "":
                p.full = True
            elif rel is not None:
                p.rels.add(rel)
            if move:
                p.moves.append(move)
            t = time.monotonic()
            if not self._first_event:
                self._first_event = t
            self._last_event = 0.0 if now else t
        self._wake.set()

    # --- watching

    def _watch(self, pair: Pair) -> None:
        self._unwatch(pair.id)
        watches = []
        for side, root in enumerate((pair.left, pair.right)):
            watches.append(self._observer.schedule(
                _Handler(self, pair.id, side, root), root, recursive=True))
        self._watches[pair.id] = watches

    def _unwatch(self, pid: str) -> None:
        for w in self._watches.pop(pid, []):
            try:
                self._observer.unschedule(w)
            except Exception:
                pass

    # --- worker thread

    def _run(self) -> None:
        last_health = 0.0
        while not self._stop:
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            if self._stop:
                break
            try:
                self._run_commands()
                now = time.monotonic()
                if now - last_health >= HEALTH_EVERY:
                    last_health = now
                    self._health_check()
                self._due_retries(now)
                for p in self.pairs():
                    if not p.paused and now - self._last_full.get(p.id, now) >= self.full_scan_every:
                        self._push(p.id, full=True, now=True)
                batch = self._take_batch(now)
                for pid, pend in batch.items():
                    self._process(pid, pend)
            except Exception as e:  # never let the worker die
                self._file_log.exception("worker error")
                self._log("", "error", f"Unexpected error: {e}")
                time.sleep(1)

    def _take_batch(self, now: float) -> dict:
        with self._lock:
            if not self._pending:
                return {}
            quiet = now - self._last_event >= self.debounce
            overdue = now - self._first_event >= MAX_WAIT
            if not (quiet or overdue):
                return {}
            batch, self._pending = self._pending, {}
            self._first_event = 0.0
            return batch

    def _run_commands(self) -> None:
        with self._lock:
            cmds, self._commands = self._commands, []
        for cmd, pid, *args in cmds:
            pair = self._pair(pid)
            if pair is None:
                continue
            if cmd == "remove":
                self._unwatch(pid)
                with self._lock:
                    self.config.pairs = [p for p in self.config.pairs if p.id != pid]
                    self.status.pop(pid, None)
                    self._pending.pop(pid, None)
                    self._save()
                self.db.delete_subtree(pid, "")
                self.db.commit()
                self._log(pid, "info", f"Stopped syncing {pair.left} ⇄ {pair.right}")
            elif cmd == "pause":
                self._unwatch(pid)
                with self._lock:
                    self._pair(pid).paused = True
                    self._pending.pop(pid, None)
                    self._save()
                self._set(pid, state="paused", message="")
            elif cmd == "resume":
                with self._lock:
                    self._pair(pid).paused = False
                    self._save()
                self._set(pid, state="starting", message="")
                self._start_pair(pair)
            elif cmd == "resolve":
                apply_deletion = args[0]
                self._set(pid, state="syncing", message="", empty_side=-1)
                if not apply_deletion:
                    self.db.delete_subtree(pid, "")  # forget history -> plain merge
                    self.db.commit()
                self._start_pair(pair, allow_empty=apply_deletion)

    def _start_pair(self, pair: Pair, allow_empty: bool = False) -> None:
        for side, root in enumerate((pair.left, pair.right)):
            if not os.path.isdir(root):
                self._unwatch(pair.id)
                self._set(pair.id, state="error", message=f"Folder not found: {root}")
                return
        try:
            self._watch(pair)
        except Exception as e:
            self._set(pair.id, state="error", message=f"Can't watch folder: {e}")
            return
        self._process(pair.id, _Pending(full=True), allow_empty=allow_empty)

    def _health_check(self) -> None:
        for p in self.pairs():
            if p.paused:
                continue
            st = self.snapshot().get(p.id)
            ok = os.path.isdir(p.left) and os.path.isdir(p.right)
            if not ok and p.id in self._watches:
                self._unwatch(p.id)
                missing = p.left if not os.path.isdir(p.left) else p.right
                self._set(p.id, state="error", message=f"Folder not found: {missing}")
            elif ok and st and st.state == "error" and st.message.startswith("Folder not found"):
                self._log(p.id, "info", "Folder is back — resuming")
                self._start_pair(p)

    def _due_retries(self, now: float) -> None:
        with self._lock:
            due = [r for r in self._retry if r[0] <= now]
            self._retry = [r for r in self._retry if r[0] > now]
        for _, pid, rel in due:
            self._push(pid, rel=rel, now=True)

    def _process(self, pid: str, pend: _Pending, allow_empty: bool = False) -> None:
        pair = self._pair(pid)
        if pair is None or pair.paused:
            return
        self._set(pid, state="syncing", message="")
        total = Result()
        try:
            if pend.full:
                total = self.engine.reconcile(pair, "", allow_empty=allow_empty)
                self._last_full[pid] = time.monotonic()
            else:
                rels = set(pend.rels)
                for side, src, dst in pend.moves:
                    try:
                        self.engine.mirror_move(pair, side, src, dst)
                    except OSError as e:
                        self._log(pid, "warn", f"Couldn't rename “{src}”: {e.strerror or e}")
                    rels.update((src, dst))
                for rel in _collapse(rels):
                    r = self.engine.reconcile(pair, rel)
                    total.copied += r.copied
                    total.deleted += r.deleted
                    total.replaced += r.replaced
                    total.failed += r.failed
        except RootMissing as e:
            self._unwatch(pid)
            self._set(pid, state="error", message=str(e))
            self._log(pid, "error", str(e))
            return
        except LooksEmpty as e:
            self._set(pid, state="empty", empty_side=e.side,
                      message=f"{e.path} is suddenly empty — paused to protect your files")
            self._log(pid, "warn", f"{e.path} is suddenly empty; waiting for you to decide")
            return
        except OSError as e:
            self._set(pid, state="error", message=f"Couldn't read folder: {e.strerror or e}")
            self._log(pid, "error", f"Couldn't read folder: {e}")
            with self._lock:
                self._retry.append((time.monotonic() + RETRY_AFTER, pid, ""))
            return

        if total.failed:
            with self._lock:
                due = time.monotonic() + RETRY_AFTER
                self._retry += [(due, pid, rel) for rel in set(total.failed)]
            n = len(set(total.failed))
            self._set(pid, state="waiting", last_sync=time.time(),
                      message=f"{n} file{'s' if n > 1 else ''} in use — will retry")
        else:
            self._set(pid, state="synced", message="", last_sync=time.time())


def _collapse(rels: set) -> list:
    """Drop paths whose parent folder is already in the set."""
    out: list = []
    for r in sorted(rels, key=lambda r: (r.count(os.sep), r)):
        if r == "":
            return [""]
        if not any(r.startswith(o + os.sep) or r == o for o in out):
            out.append(r)
    return out
