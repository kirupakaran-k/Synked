"""Tests for the sync engine and the live watcher. Run with:  python -m pytest tests"""
import os
import shutil
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import Engine, LooksEmpty, RootMissing  # noqa: E402
from store import Config, Pair, StateDB  # noqa: E402
from watcher import Controller  # noqa: E402


# ------------------------------------------------------------------ helpers

class Bin:
    """Stand-in Recycle Bin that records what was 'recycled'."""
    def __init__(self, path):
        self.path, self.items = path, []

    def __call__(self, p, root):
        dest = os.path.join(self.path, f"{len(self.items)}_{os.path.basename(p)}")
        shutil.move(p, dest)
        self.items.append((os.path.relpath(p, root), dest))

    def names(self):
        return [r for r, _ in self.items]


def write(path, text, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def read(path):
    with open(path) as f:
        return f.read()


def tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
        for x in dirs:
            out[os.path.relpath(os.path.join(d, x), root).replace(os.sep, "/") + "/"] = None
    return out


@pytest.fixture
def env(tmp_path):
    L, R = tmp_path / "L", tmp_path / "R"
    L.mkdir(), R.mkdir()
    bin_ = Bin(str(tmp_path / "bin"))
    os.makedirs(bin_.path)
    db = StateDB(str(tmp_path / "state.db"))
    eng = Engine(db, trash=bin_)
    pair = Pair(left=str(L), right=str(R))

    class E:
        pass
    e = E()
    e.L, e.R, e.bin, e.db, e.eng, e.pair = str(L), str(R), bin_, db, eng, pair
    e.sync = lambda rel="", **kw: eng.reconcile(pair, rel, **kw)
    yield e
    db.close()


# ------------------------------------------------------------------ engine

def test_new_files_both_directions(env):
    write(f"{env.L}/a.txt", "A")
    write(f"{env.R}/sub/b.txt", "B")
    env.sync()
    assert tree(env.L) == tree(env.R) == {"a.txt": "A", "sub/": None, "sub/b.txt": "B"}


def test_nothing_happens_when_in_sync(env):
    write(f"{env.L}/a.txt", "A")
    env.sync()
    r = env.sync()
    assert r.changes == 0 and not r.failed


def test_edit_propagates(env):
    write(f"{env.L}/a.txt", "v1", mtime=1_000_000)
    env.sync()
    write(f"{env.R}/a.txt", "v2", mtime=1_000_100)
    env.sync()
    assert read(f"{env.L}/a.txt") == "v2"


def test_delete_goes_to_recycle_bin(env):
    write(f"{env.L}/a.txt", "A")
    env.sync()
    os.remove(f"{env.L}/a.txt")
    env.sync()
    assert not os.path.exists(f"{env.R}/a.txt")
    assert env.bin.names() == ["a.txt"]


def test_folder_delete_recycled_as_one_item(env):
    for i in range(5):
        write(f"{env.L}/books/{i}.txt", str(i))
    env.sync()
    shutil.rmtree(f"{env.R}/books")
    env.sync()
    assert tree(env.L) == {}
    assert env.bin.names() == ["books"]


def test_both_changed_newer_wins_older_recycled(env):
    write(f"{env.L}/a.txt", "base", mtime=1_000_000)
    env.sync()
    write(f"{env.L}/a.txt", "left-new", mtime=1_000_500)
    write(f"{env.R}/a.txt", "right-old", mtime=1_000_200)
    r = env.sync()
    assert read(f"{env.L}/a.txt") == read(f"{env.R}/a.txt") == "left-new"
    assert env.bin.names() == ["a.txt"] and read(env.bin.items[0][1]) == "right-old"
    assert r.replaced == 1


def test_modified_beats_deleted(env):
    write(f"{env.L}/a.txt", "v1", mtime=1_000_000)
    env.sync()
    os.remove(f"{env.L}/a.txt")
    write(f"{env.R}/a.txt", "v2", mtime=1_000_100)
    env.sync()
    assert read(f"{env.L}/a.txt") == "v2"


def test_deleted_folder_with_new_file_on_other_side_keeps_new_file(env):
    write(f"{env.L}/d/old.txt", "old")
    env.sync()
    shutil.rmtree(f"{env.L}/d")
    write(f"{env.R}/d/new.txt", "new")
    env.sync()
    assert tree(env.L) == tree(env.R) == {"d/": None, "d/new.txt": "new"}
    assert env.bin.names() == [os.path.join("d", "old.txt")]


def test_first_pairing_merges_identical_files_without_recycling(env):
    write(f"{env.L}/same.txt", "x", mtime=1_000_000)
    write(f"{env.R}/same.txt", "x", mtime=1_000_900)  # same content, different time
    write(f"{env.L}/diff.txt", "new", mtime=1_000_900)
    write(f"{env.R}/diff.txt", "old", mtime=1_000_000)
    env.sync()
    assert read(f"{env.R}/diff.txt") == "new"
    assert env.bin.names() == ["diff.txt"]


def test_ignored_files_are_skipped(env):
    write(f"{env.L}/~$report.docx", "lock")
    write(f"{env.L}/desktop.ini", "x")
    write(f"{env.L}/x.tmp", "x")
    write(f"{env.L}/real.docx", "doc")
    env.sync()
    assert tree(env.R) == {"real.docx": "doc"}


def test_subtree_sync(env):
    write(f"{env.L}/a/1.txt", "1")
    write(f"{env.L}/b/2.txt", "2")
    env.sync("a")
    assert tree(env.R) == {"a/": None, "a/1.txt": "1"}


def test_missing_root_never_deletes(env):
    write(f"{env.L}/a.txt", "A")
    env.sync()
    shutil.move(env.R, env.R + "_unplugged")
    with pytest.raises(RootMissing):
        env.sync()
    with pytest.raises(RootMissing):
        env.sync("a.txt")
    assert os.path.exists(f"{env.L}/a.txt") and env.bin.items == []


def test_suddenly_empty_side_is_guarded(env):
    for i in range(25):
        write(f"{env.L}/{i}.txt", str(i))
    env.sync()
    for f in os.listdir(env.R):
        os.remove(os.path.join(env.R, f))
    with pytest.raises(LooksEmpty) as ei:
        env.sync()
    assert ei.value.side == 1 and len(os.listdir(env.L)) == 25
    env.sync(allow_empty=True)  # user confirmed the deletion
    assert os.listdir(env.L) == []


def test_mirror_rename_file_and_folder(env):
    write(f"{env.L}/old.txt", "A")
    write(f"{env.L}/dir/x.txt", "X")
    env.sync()
    os.rename(f"{env.L}/old.txt", f"{env.L}/new.txt")
    os.rename(f"{env.L}/dir", f"{env.L}/dir2")
    assert env.eng.mirror_move(env.pair, 0, "old.txt", "new.txt")
    assert env.eng.mirror_move(env.pair, 0, "dir", "dir2")
    r = env.sync()
    assert r.changes == 0 and env.bin.items == []
    assert tree(env.R) == {"new.txt": "A", "dir2/": None, "dir2/x.txt": "X"}


def test_mirror_rename_refused_when_other_side_changed(env):
    write(f"{env.L}/a.txt", "v1", mtime=1_000_000)
    env.sync()
    write(f"{env.R}/a.txt", "v2", mtime=1_000_100)
    os.rename(f"{env.L}/a.txt", f"{env.L}/b.txt")
    assert not env.eng.mirror_move(env.pair, 0, "a.txt", "b.txt")
    env.sync()  # falls back to normal sync: nothing lost
    assert read(f"{env.L}/a.txt") == "v2" and read(f"{env.R}/b.txt") == "v1"


def test_busy_file_is_retried_not_lost(env, monkeypatch):
    import engine
    write(f"{env.L}/a.txt", "A")
    real = engine.shutil.copy2

    def locked(src, dst):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(engine.shutil, "copy2", locked)
    r = env.sync()
    assert r.failed == ["a.txt"] and not os.path.exists(f"{env.R}/a.txt")
    assert [f for f in os.listdir(env.R)] == []  # no temp files left behind
    monkeypatch.setattr(engine.shutil, "copy2", real)
    env.sync()
    assert read(f"{env.R}/a.txt") == "A"


def test_many_files(env):
    for i in range(400):
        write(f"{env.L}/d{i % 7}/f{i}.txt", str(i))
    env.sync()
    assert tree(env.L) == tree(env.R)
    assert env.sync().changes == 0


# ------------------------------------------------------------------ live watcher

def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    L, R = tmp_path / "L", tmp_path / "R"
    L.mkdir(), R.mkdir()
    bin_ = Bin(str(tmp_path / "bin"))
    os.makedirs(bin_.path)
    ctrl = Controller(config=Config(), db=StateDB(str(tmp_path / "s.db")), trash=bin_,
                      debounce=0.3, save_config=False)
    ctrl.start()
    ctrl.add_pair(str(L), str(R))
    assert wait_for(lambda: ctrl.overall() == "synced")
    yield ctrl, str(L), str(R), bin_
    ctrl.stop()


def test_live_create_edit_rename_delete(live):
    ctrl, L, R, bin_ = live
    write(f"{L}/notes.txt", "hello")
    assert wait_for(lambda: os.path.exists(f"{R}/notes.txt"))

    write(f"{R}/notes.txt", "edited on the other side")
    assert wait_for(lambda: read(f"{L}/notes.txt") == "edited on the other side")

    os.makedirs(f"{L}/folder/deep")
    write(f"{L}/folder/deep/x.txt", "x")
    assert wait_for(lambda: os.path.exists(f"{R}/folder/deep/x.txt"))

    os.rename(f"{L}/folder", f"{L}/renamed")
    assert wait_for(lambda: os.path.exists(f"{R}/renamed/deep/x.txt")
                    and not os.path.exists(f"{R}/folder"))
    assert bin_.items == []  # rename was mirrored, nothing re-copied or recycled

    os.remove(f"{R}/notes.txt")
    assert wait_for(lambda: not os.path.exists(f"{L}/notes.txt"))
    assert bin_.names() == ["notes.txt"]
    assert wait_for(lambda: ctrl.overall() == "synced")


def test_live_pause_resume_catches_up(live):
    ctrl, L, R, _ = live
    pid = ctrl.pairs()[0].id
    ctrl.set_paused(pid, True)
    assert wait_for(lambda: ctrl.overall() == "paused")
    write(f"{L}/while-paused.txt", "p")
    time.sleep(1)
    assert not os.path.exists(f"{R}/while-paused.txt")
    ctrl.set_paused(pid, False)
    assert wait_for(lambda: os.path.exists(f"{R}/while-paused.txt"))


def test_validation(live):
    ctrl, L, R, _ = live
    assert ctrl.validate(L, L)
    assert ctrl.validate(L, os.path.join(L))
    os.makedirs(f"{L}/inner")
    assert ctrl.validate(f"{L}/inner", R)          # overlaps existing pair
    assert ctrl.validate("/nope", R)


def test_live_office_style_save(live):
    """Word/Excel save by writing a temp file and swapping it in."""
    ctrl, L, R, bin_ = live
    write(f"{L}/report.docx", "v1")
    assert wait_for(lambda: os.path.exists(f"{R}/report.docx"))
    write(f"{L}/~WRD0001.tmp", "v2")
    os.rename(f"{L}/report.docx", f"{L}/~WRL0002.tmp")
    os.rename(f"{L}/~WRD0001.tmp", f"{L}/report.docx")
    os.remove(f"{L}/~WRL0002.tmp")
    assert wait_for(lambda: read(f"{R}/report.docx") == "v2")
    assert wait_for(lambda: ctrl.overall() == "synced")
    assert not any(n.endswith(".tmp") for n in os.listdir(R))


def test_changes_while_app_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    L, R = str(tmp_path / "L"), str(tmp_path / "R")
    os.makedirs(L), os.makedirs(R)
    bin_ = Bin(str(tmp_path / "bin"))
    os.makedirs(bin_.path)
    cfg = Config()
    write(f"{L}/keep.txt", "k")
    write(f"{L}/gone.txt", "g")

    def run():
        c = Controller(config=cfg, db=StateDB(str(tmp_path / "s.db")), trash=bin_,
                       debounce=0.3, save_config=False)
        c.start()
        return c
    c = run()
    if not cfg.pairs:
        c.add_pair(L, R)
    assert wait_for(lambda: os.path.exists(f"{R}/gone.txt") and c.overall() == "synced")
    c.stop()

    os.remove(f"{R}/gone.txt")          # changed while SyncMate was closed
    write(f"{R}/added.txt", "a")
    c = run()
    assert wait_for(lambda: os.path.exists(f"{L}/added.txt") and not os.path.exists(f"{L}/gone.txt"))
    assert bin_.names() == ["gone.txt"]
    c.stop()
