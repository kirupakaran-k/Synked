"""Settings (JSON) and last-sync state (SQLite) for SyncMate."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field


def data_dir() -> str:
    """Where settings, the state database and the log live."""
    base = os.environ.get("SYNCMATE_HOME")
    if not base:
        if os.name == "nt":
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SyncMate")
        else:
            base = os.path.join(os.path.expanduser("~"), ".config", "syncmate")
    os.makedirs(base, exist_ok=True)
    return base


# --------------------------------------------------------------------------- config

MODES = ("two-way", "mirror", "update", "collect")
MODE_LABELS = {
    "two-way": "Two-way  (Main ⇄ folder)",
    "mirror": "Mirror  (Main → folder, exact copy)",
    "update": "Update  (Main → folder, add & update only)",
    "collect": "Collect  (folder → Main, add & update only)",
}
VERSION_LABELS = {False: "Old files → Recycle Bin", True: "Keep old versions"}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Pair:
    """One link between two folders, as the sync engine sees it.
    left = the job's Main folder, right = a connected folder."""
    left: str
    right: str
    paused: bool = False
    id: str = field(default_factory=_new_id)
    mode: str = "two-way"     # see MODES
    versions: bool = False    # keep old versions instead of using the Recycle Bin
    job: str = ""             # the job this link belongs to
    names: tuple = ("Main", "folder")


@dataclass
class Member:
    """A folder connected to a job's Main folder."""
    path: str
    mode: str = "two-way"
    versions: bool = False
    id: str = field(default_factory=_new_id)


@dataclass
class Job:
    """A sync job: one Main folder and the folders connected to it."""
    main: str
    members: list = field(default_factory=list)   # list[Member]
    paused: bool = False
    id: str = field(default_factory=_new_id)

    def links(self) -> list:
        return [Pair(left=self.main, right=m.path, paused=self.paused, id=m.id, mode=m.mode,
                     versions=m.versions, job=self.id, names=("Main", _name(m.path)))
                for m in self.members]

    def copy(self) -> "Job":
        return Job(main=self.main, paused=self.paused, id=self.id,
                   members=[Member(**vars(m)) for m in self.members])


def _name(path: str) -> str:
    return os.path.basename(path.rstrip("\\/")) or path


@dataclass
class Config:
    jobs: list = field(default_factory=list)      # list[Job]
    tray_hint_shown: bool = False
    theme: str = "auto"  # auto | light | dark

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = path or os.path.join(data_dir(), "config.json")
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return cls()
        jobs = []
        for j in raw.get("jobs", []):
            members = [Member(path=m["path"], id=m.get("id") or _new_id(),
                              mode=m.get("mode") if m.get("mode") in MODES else "two-way",
                              versions=bool(m.get("versions", False)))
                       for m in j.get("members", [])]
            jobs.append(Job(main=j["main"], members=members, paused=j.get("paused", False),
                            id=j.get("id") or _new_id()))
        # Older versions stored simple pairs. Each becomes a job with one two-way folder;
        # the folder keeps the pair's id, so its sync history still applies.
        for p in raw.get("pairs", []):
            jobs.append(Job(main=p["left"], paused=p.get("paused", False), id=p["id"],
                            members=[Member(path=p["right"], id=p["id"])]))
        theme = raw.get("theme", "auto")
        return cls(jobs=jobs, tray_hint_shown=raw.get("tray_hint_shown", False),
                   theme=theme if theme in ("auto", "light", "dark") else "auto")

    def save(self, path: str | None = None) -> None:
        path = path or os.path.join(data_dir(), "config.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"jobs": [{"id": j.id, "main": j.main, "paused": j.paused,
                                 "members": [asdict(m) for m in j.members]} for j in self.jobs],
                       "tray_hint_shown": self.tray_hint_shown,
                       "theme": self.theme}, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


# --------------------------------------------------------------------------- state db

# A side's state is (is_dir, size, mtime_ns); a missing item is None.
Stat = tuple  # (bool, int, int)


class StateDB:
    """Remembers what both sides looked like right after the last successful sync.

    A record exists only for items that were in sync on both sides. Comparing the
    current state of each side with its record tells us which side changed.
    """

    def __init__(self, path: str | None = None):
        path = path or os.path.join(data_dir(), "state.db")
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS entries (
                   pair TEXT NOT NULL, key TEXT NOT NULL, rel TEXT NOT NULL,
                   is_dir INTEGER NOT NULL,
                   l_size INTEGER, l_mtime INTEGER, r_size INTEGER, r_mtime INTEGER,
                   PRIMARY KEY (pair, key))""")
        self._db.commit()

    @staticmethod
    def _row_to_rec(row):
        rel, is_dir, ls, lm, rs, rm = row
        return rel, (bool(is_dir), ls, lm), (bool(is_dir), rs, rm)

    def get_subtree(self, pair: str, key: str) -> dict:
        """Records for `key` and everything below it ("" = whole pair)."""
        with self._lock:
            if key == "":
                cur = self._db.execute(
                    "SELECT key, rel, is_dir, l_size, l_mtime, r_size, r_mtime "
                    "FROM entries WHERE pair=?", (pair,))
            else:
                lo = key + os.sep
                hi = key + chr(ord(os.sep) + 1)
                cur = self._db.execute(
                    "SELECT key, rel, is_dir, l_size, l_mtime, r_size, r_mtime FROM entries "
                    "WHERE pair=? AND (key=? OR (key>=? AND key<?))", (pair, key, lo, hi))
            return {r[0]: self._row_to_rec(r[1:]) for r in cur.fetchall()}

    def get(self, pair: str, key: str):
        with self._lock:
            row = self._db.execute(
                "SELECT rel, is_dir, l_size, l_mtime, r_size, r_mtime FROM entries "
                "WHERE pair=? AND key=?", (pair, key)).fetchone()
            return self._row_to_rec(row) if row else None

    def put(self, pair: str, key: str, rel: str, left: Stat, right: Stat) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?)",
                (pair, key, rel, int(left[0]), left[1], left[2], right[1], right[2]))

    def delete_subtree(self, pair: str, key: str) -> None:
        with self._lock:
            if key == "":
                self._db.execute("DELETE FROM entries WHERE pair=?", (pair,))
            else:
                self._db.execute(
                    "DELETE FROM entries WHERE pair=? AND (key=? OR (key>=? AND key<?))",
                    (pair, key, key + os.sep, key + chr(ord(os.sep) + 1)))

    def move_subtree(self, pair: str, old_key: str, new_key: str, new_rel: str) -> None:
        """Re-key records after a rename was mirrored to the other side."""
        with self._lock:
            recs = self.get_subtree(pair, old_key)
            self.delete_subtree(pair, old_key)
            for k, (rel, l, r) in recs.items():
                suffix_rel = rel[len(rel) - (len(k) - len(old_key)):] if k != old_key else ""
                nk = new_key + k[len(old_key):]
                self.put(pair, nk, new_rel + suffix_rel, l, r)

    def count(self, pair: str) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM entries WHERE pair=?", (pair,)).fetchone()[0]

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.commit()
            self._db.close()
