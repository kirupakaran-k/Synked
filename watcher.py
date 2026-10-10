"""Watches sync jobs and runs the sync engine in a background thread.

A job has one Main folder and one or more connected folders. Each connected folder is
a "link" with Main and has its own sync type. Changes always pass through Main: an
edit in one connected folder reaches Main, and from there the other folders.

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

from engine import Engine, LooksEmpty, Progress, RootMissing, default_trash, rel_is_ignored
from store import MODES, Config, Job, Member, StateDB, data_dir

DEBOUNCE = 1.5          # seconds of quiet before syncing a burst of changes
MAX_WAIT = 8.0          # ...but never wait longer than this
FULL_SCAN_EVERY = 600   # safety-net rescan of everything (seconds)
HEALTH_EVERY = 10       # check that folders still exist (seconds)
RETRY_AFTER = 15        # retry files that were in use (seconds)
MAX_PASSES = 3          # full scans repeat while changes are still flowing through Main

MAIN = "main"           # marks events that came from a job's Main folder


@dataclass
class Status:
    state: str = "starting"   # starting | syncing | synced | waiting | paused | error | empty
    message: str = ""
    last_sync: float = 0.0
    empty_side: int = -1      # for "empty": which side of `empty_link` is empty
    empty_link: str = ""
    progress: Progress | None = None   # set while files are being copied
    progress_link: str = ""            # which connected folder the progress is for


@dataclass
class _Pending:
    rels: dict = field(default_factory=dict)    # where (MAIN or member id) -> set of rels
    moves: list = field(default_factory=list)   # (where, src, dst)
    full: bool = False


class _Handler(FileSystemEventHandler):
    def __init__(self, ctrl: "Controller", jid: str, where: str, root: str):
        self.ctrl, self.jid, self.where, self.root = ctrl, jid, where, root

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
                self.ctrl._push(self.jid, self.where, move=(src, dst))
            else:
                if s_ok:
                    self.ctrl._push(self.jid, self.where, rel=src)
                if d_ok:
                    self.ctrl._push(self.jid, self.where, rel=dst)
            return
        if not rel_is_ignored(src):
            self.ctrl._push(self.jid, self.where, rel=src)


def _setup_file_log() -> logging.Logger:
    """The log file lives in the settings folder; follow it if that folder changed."""
    log = logging.getLogger("syncmate")
    path = os.path.abspath(os.path.join(data_dir(), "syncmate.log"))
    for h in list(log.handlers):
        if getattr(h, "baseFilename", None) != path:
            log.removeHandler(h)
            h.close()
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2,
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
        self.activity: deque = deque(maxlen=500)
        self._file_log = _setup_file_log()
        self.engine = Engine(self.db, self._log, trash)

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._pending: dict[str, _Pending] = {}
        self._first_event = self._last_event = 0.0
        self._commands: list = []
        self._retry: list = []  # (due, jid, where, rel)
        self.status: dict[str, Status] = {}
        self._watches: dict[str, list] = {}
        self._last_full: dict[str, float] = {}
        self._observer = Observer()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="syncmate-worker", daemon=True)

    # ================================================================== public API

    def start(self) -> None:
        self._observer.start()
        for j in self.config.jobs:
            self.status[j.id] = Status(state="paused" if j.paused else "starting")
            if not j.paused:
                self._command("resume", j.id)
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

    def jobs(self) -> list[Job]:
        with self._lock:
            return [j.copy() for j in self.config.jobs]

    def snapshot(self) -> dict[str, Status]:
        with self._lock:
            out = {}
            for k, v in self.status.items():
                st = Status(**vars(v))
                if v.progress is not None:
                    st.progress = Progress(**vars(v.progress))
                out[k] = st
            return out

    def recent_activity(self, jid: str, n: int = 6) -> list:
        """The newest `n` activity entries for one job: (time, level, message)."""
        with self._lock:
            out = []
            for t, j, level, msg in self.activity:
                if j == jid:
                    out.append((t, level, msg))
                    if len(out) >= n:
                        break
            return out

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

    def validate(self, main: str, members: list, ignore: str | None = None) -> str | None:
        """An error message, or None if the folders are OK for a job.
        `members` is a list of connected-folder paths; `ignore` is the job being edited."""
        if not main:
            return "Choose the Main folder."
        members = [m for m in members if m]
        if not members:
            return "Add at least one folder to sync with Main."
        folders = [main] + members
        for p in folders:
            if not os.path.isdir(p):
                return f"“{p}” is not a folder."
        for i, a in enumerate(folders):
            for b in folders[i + 1:]:
                if _overlaps(a, b):
                    return (f"“{a}” and “{b}” are the same folder or inside each other.")
        for job in self.jobs():
            if job.id == ignore:
                continue
            for new in folders:
                for old in [job.main] + [m.path for m in job.members]:
                    if _overlaps(new, old):
                        return f"“{new}” overlaps a folder that's already being synced."
        return None

    def add_job(self, main: str, members: list) -> Job:
        """members: list of Member (or (path, mode, versions) tuples)."""
        job = Job(main=os.path.abspath(main), members=[_member(m) for m in members])
        with self._lock:
            self.config.jobs.append(job)
            self.status[job.id] = Status()
            self._save()
        self._log(job.id, "info", f"Started syncing {job.main} with "
                                  f"{len(job.members)} folder{'s' if len(job.members) > 1 else ''}")
        self._command("resume", job.id)
        return job

    def update_job(self, jid: str, main: str, members: list) -> None:
        """Change a job's Main folder and/or its connected folders, types and options."""
        self._command("edit", jid, os.path.abspath(main), [_member(m) for m in members])

    def remove_job(self, jid: str) -> None:
        self._command("remove", jid)

    def set_paused(self, jid: str, paused: bool) -> None:
        self._command("pause" if paused else "resume", jid)

    def sync_now(self, jid: str | None = None) -> None:
        for j in self.jobs():
            if jid in (None, j.id) and not j.paused:
                self._push(j.id, MAIN, full=True, now=True)

    def remember(self, **settings) -> None:
        """Save small app settings (e.g. tray_hint_shown=True)."""
        with self._lock:
            for k, v in settings.items():
                setattr(self.config, k, v)
            self._save()

    def resolve_empty(self, jid: str, apply_deletion: bool) -> None:
        """A folder suddenly became empty. Either delete on the other side too,
        or restore its files from the other side."""
        self._command("resolve", jid, apply_deletion)

    # ================================================================== internals

    def _save(self):
        if self.save_config:
            self.config.save()

    def _log(self, jid: str, level: str, msg: str) -> None:
        with self._lock:
            self.activity.appendleft((time.time(), jid, level, msg))
        getattr(self._file_log, {"warn": "warning"}.get(level, level), self._file_log.info)(msg)

    def _set(self, jid: str, **kw) -> None:
        with self._lock:
            st = self.status.setdefault(jid, Status())
            for k, v in kw.items():
                setattr(st, k, v)

    def _job(self, jid: str) -> Job | None:
        with self._lock:
            j = next((j for j in self.config.jobs if j.id == jid), None)
            return j.copy() if j else None

    def _command(self, *cmd) -> None:
        with self._lock:
            self._commands.append(cmd)
        self._wake.set()

    def _push(self, jid: str, where: str, rel: str | None = None, move=None,
              full=False, now=False):
        with self._lock:
            p = self._pending.setdefault(jid, _Pending())
            if full or rel == "":
                p.full = True
            elif rel is not None:
                p.rels.setdefault(where, set()).add(rel)
            if move:
                p.moves.append((where,) + tuple(move))
            t = time.monotonic()
            if not self._first_event:
                self._first_event = t
            self._last_event = 0.0 if now else t
        self._wake.set()

    # --- watching

    def _watch(self, job: Job) -> None:
        self._unwatch(job.id)
        watches = [self._observer.schedule(_Handler(self, job.id, MAIN, job.main),
                                           job.main, recursive=True)]
        for m in job.members:
            watches.append(self._observer.schedule(_Handler(self, job.id, m.id, m.path),
                                                   m.path, recursive=True))
        self._watches[job.id] = watches

    def _unwatch(self, jid: str) -> None:
        for w in self._watches.pop(jid, []):
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
                for j in self.jobs():
                    if not j.paused and now - self._last_full.get(j.id, now) >= self.full_scan_every:
                        self._push(j.id, MAIN, full=True, now=True)
                batch = self._take_batch(now)
                for jid, pend in batch.items():
                    self._process(jid, pend)
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
        for cmd, jid, *args in cmds:
            job = self._job(jid)
            if job is None:
                continue
            if cmd == "remove":
                self._unwatch(jid)
                with self._lock:
                    self.config.jobs = [j for j in self.config.jobs if j.id != jid]
                    self.status.pop(jid, None)
                    self._pending.pop(jid, None)
                    self._save()
                for m in job.members:
                    self.db.delete_subtree(m.id, "")
                self.db.commit()
                self._log(jid, "info", f"Stopped syncing {job.main}")
            elif cmd == "pause":
                self._unwatch(jid)
                self._edit_config(jid, paused=True)
                with self._lock:
                    self._pending.pop(jid, None)
                self._set(jid, state="paused", message="", progress=None)
                self._log(jid, "info", "Paused")
            elif cmd == "resume":
                was_paused = job.paused
                self._edit_config(jid, paused=False)
                self._set(jid, state="starting", message="")
                if was_paused:
                    self._log(jid, "info", "Resumed")
                self._start_job(self._job(jid))
            elif cmd == "edit":
                main, members = args
                self._apply_edit(job, main, members)
            elif cmd == "resolve":
                apply_deletion = args[0]
                st = self.snapshot().get(jid)
                link_id = st.empty_link if st else ""
                self._set(jid, state="syncing", message="", empty_side=-1, empty_link="")
                if not apply_deletion and link_id:
                    self.db.delete_subtree(link_id, "")  # forget history -> plain merge
                    self.db.commit()
                self._start_job(job, allow_empty_link=link_id if apply_deletion else "")

    def _edit_config(self, jid: str, **kw) -> None:
        with self._lock:
            j = next((j for j in self.config.jobs if j.id == jid), None)
            if j is not None:
                for k, v in kw.items():
                    setattr(j, k, v)
                self._save()

    def _apply_edit(self, old: Job, main: str, members: list) -> None:
        """Save a changed job. Sync history is kept for links whose two folders didn't
        change, so only what's actually new gets compared and copied."""
        self._unwatch(old.id)
        main_changed = _norm(main) != _norm(old.main)
        old_paths = {m.id: m.path for m in old.members}
        new_ids = {m.id for m in members}
        for m in old.members:
            if m.id not in new_ids:
                self.db.delete_subtree(m.id, "")
        for m in members:
            if main_changed or m.id not in old_paths or _norm(old_paths[m.id]) != _norm(m.path):
                self.db.delete_subtree(m.id, "")
        self.db.commit()
        with self._lock:
            j = next((j for j in self.config.jobs if j.id == old.id), None)
            if j is None:
                return
            j.main, j.members = main, members
            self._pending.pop(old.id, None)
            self._save()
        self._log(old.id, "info", f"Settings changed: Main {main}, "
                                  f"{len(members)} connected folder{'s' if len(members) != 1 else ''}")
        job = self._job(old.id)
        if not job.paused:
            self._set(job.id, state="starting", message="")
            self._start_job(job)

    def _start_job(self, job: Job, allow_empty_link: str = "") -> None:
        for root in [job.main] + [m.path for m in job.members]:
            if not os.path.isdir(root):
                self._unwatch(job.id)
                self._set(job.id, state="error", message=f"Folder not found: {root}")
                return
        try:
            self._watch(job)
        except Exception as e:
            self._set(job.id, state="error", message=f"Can't watch folder: {e}")
            return
        self._process(job.id, _Pending(full=True), allow_empty_link=allow_empty_link)

    def _health_check(self) -> None:
        for j in self.jobs():
            if j.paused:
                continue
            st = self.snapshot().get(j.id)
            missing = next((p for p in [j.main] + [m.path for m in j.members]
                            if not os.path.isdir(p)), None)
            if missing and j.id in self._watches:
                self._unwatch(j.id)
                self._set(j.id, state="error", message=f"Folder not found: {missing}")
            elif not missing and st and st.state == "error" and st.message.startswith("Folder not found"):
                self._log(j.id, "info", "Folder is back — resuming")
                self._start_job(j)

    def _due_retries(self, now: float) -> None:
        with self._lock:
            due = [r for r in self._retry if r[0] <= now]
            self._retry = [r for r in self._retry if r[0] > now]
        for _, jid, where, rel in due:
            self._push(jid, where, rel=rel, now=True)

    def _process(self, jid: str, pend: _Pending, allow_empty_link: str = "") -> None:
        job = self._job(jid)
        if job is None or job.paused:
            return
        links = job.links()
        self._set(jid, state="syncing", message="", progress=None)
        failed: list = []   # (where, rel)

        def reconcile(link, rel=""):
            def on_progress(p: Progress, lid=link.id):
                with self._lock:
                    st = self.status.get(jid)
                    if st is not None:
                        st.progress, st.progress_link = p, lid
            r = self.engine.reconcile(link, rel, allow_empty=(link.id == allow_empty_link),
                                      progress=on_progress)
            failed.extend((link.id, x) for x in r.failed)
            return r

        current = None
        try:
            if pend.full:
                # Repeat while changes are still flowing through Main to the other folders.
                for _ in range(MAX_PASSES if len(links) > 1 else 1):
                    changes = 0
                    for link in links:
                        current = link
                        changes += reconcile(link).changes
                    if not changes:
                        break
                self._last_full[jid] = time.monotonic()
            else:
                by_id = {link.id: link for link in links}
                rels = {w: set(s) for w, s in pend.rels.items()}
                for where, src, dst in pend.moves:
                    targets = links if where == MAIN else [by_id[where]] if where in by_id else []
                    for link in targets:
                        current = link
                        try:
                            self.engine.mirror_move(link, 0 if where == MAIN else 1, src, dst)
                        except OSError as e:
                            self._log(jid, "warn", f"Couldn't rename “{src}”: {e.strerror or e}")
                    rels.setdefault(where, set()).update((src, dst))
                for where, rs in rels.items():
                    targets = links if where == MAIN else [by_id[where]] if where in by_id else []
                    for link in targets:
                        current = link
                        for rel in _collapse(rs):
                            reconcile(link, rel)
        except RootMissing as e:
            self._unwatch(jid)
            self._set(jid, progress=None, state="error", message=str(e))
            self._log(jid, "error", str(e))
            return
        except LooksEmpty as e:
            self._set(jid, progress=None, state="empty", empty_side=e.side,
                      empty_link=current.id if current else "",
                      message=f"{e.path} is suddenly empty — paused to protect your files")
            self._log(jid, "warn", f"{e.path} is suddenly empty; waiting for you to decide")
            return
        except OSError as e:
            self._set(jid, progress=None, state="error",
                      message=f"Couldn't read folder: {e.strerror or e}")
            self._log(jid, "error", f"Couldn't read folder: {e}")
            with self._lock:
                self._retry.append((time.monotonic() + RETRY_AFTER, jid, MAIN, ""))
            return

        if failed:
            with self._lock:
                due = time.monotonic() + RETRY_AFTER
                # a failed item is retried from Main, which re-checks every folder
                self._retry += [(due, jid, MAIN, rel) for rel in {r for _, r in failed}]
            n = len({r for _, r in failed})
            self._set(jid, state="waiting", last_sync=time.time(), progress=None,
                      message=f"{n} file{'s' if n > 1 else ''} in use — will retry")
        else:
            self._set(jid, state="synced", message="", last_sync=time.time(), progress=None)


def _member(m) -> Member:
    if isinstance(m, Member):
        return Member(path=os.path.abspath(m.path), mode=m.mode if m.mode in MODES else "two-way",
                      versions=bool(m.versions), id=m.id)
    path, mode, versions = (tuple(m) + ("two-way", False))[:3]
    return Member(path=os.path.abspath(path), mode=mode if mode in MODES else "two-way",
                  versions=bool(versions))


def _collapse(rels: set) -> list:
    """Drop paths whose parent folder is already in the set."""
    out: list = []
    for r in sorted(rels, key=lambda r: (r.count(os.sep), r)):
        if r == "":
            return [""]
        if not any(r.startswith(o + os.sep) or r == o for o in out):
            out.append(r)
    return out
