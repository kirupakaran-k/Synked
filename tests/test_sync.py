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
    real = engine._copy_data

    def locked(src, dst, on_bytes):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(engine, "_copy_data", locked)
    r = env.sync()
    assert r.failed == ["a.txt"] and not os.path.exists(f"{env.R}/a.txt")
    assert [f for f in os.listdir(env.R)] == []  # no temp files left behind
    monkeypatch.setattr(engine, "_copy_data", real)
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
        try:
            if cond():
                return True
        except OSError:
            pass
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
    ctrl.add_job(str(L), [(str(R), "two-way", False)])
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
    pid = ctrl.jobs()[0].id
    ctrl.set_paused(pid, True)
    assert wait_for(lambda: ctrl.overall() == "paused")
    write(f"{L}/while-paused.txt", "p")
    time.sleep(1)
    assert not os.path.exists(f"{R}/while-paused.txt")
    ctrl.set_paused(pid, False)
    assert wait_for(lambda: os.path.exists(f"{R}/while-paused.txt"))


def test_validation(live):
    ctrl, L, R, _ = live
    assert ctrl.validate(L, [L])
    assert ctrl.validate(L, [])                       # needs at least one folder
    os.makedirs(f"{L}/inner")
    assert ctrl.validate(f"{L}/inner", [R])         # overlaps an existing job
    assert ctrl.validate("/nope", [R])


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
    if not cfg.jobs:
        c.add_job(L, [(R, "two-way", False)])
    assert wait_for(lambda: os.path.exists(f"{R}/gone.txt") and c.overall() == "synced")
    c.stop()

    os.remove(f"{R}/gone.txt")          # changed while SyncMate was closed
    write(f"{R}/added.txt", "a")
    c = run()
    assert wait_for(lambda: os.path.exists(f"{L}/added.txt") and not os.path.exists(f"{L}/gone.txt"))
    assert bin_.names() == ["gone.txt"]
    c.stop()


def test_stale_temp_files_are_cleaned_up(env, monkeypatch):
    """A half-copied temp file left behind by a killed SyncMate is removed once it's an
    hour old; a recent one (a copy that might still be in progress) is left alone."""
    import engine
    temp = f"{env.R}/.syncmate-dead0001.tmp"
    write(temp, "half")
    env.sync()
    assert os.path.exists(temp)                      # fresh: kept
    real_time = engine.time.time
    monkeypatch.setattr(engine.time, "time", lambda: real_time() + 7200)  # two hours later
    env.sync()
    assert not os.path.exists(temp)                  # stale: removed
    assert tree(env.L) == {}                         # and never synced to the other side


def test_progress_is_reported(env):
    import engine
    big = b"x" * (engine.CHUNK * 3 + 10)
    os.makedirs(f"{env.L}/d")
    with open(f"{env.L}/d/big.bin", "wb") as f:
        f.write(big)
    write(f"{env.L}/small.txt", "s")
    seen = []
    env.eng.reconcile(env.pair, progress=lambda p: seen.append(
        (p.action, p.rel, p.items_done, p.items_total, p.bytes_done, p.bytes_total)))
    first, last = seen[0], seen[-1]
    assert first[3] == 3 and first[5] == len(big) + 1          # d/, big.bin, small.txt
    assert any(a == "copy" and r == os.path.join("d", "big.bin") for a, r, *_ in seen)
    mids = [b for a, r, _, _, b, _ in seen if r.endswith("big.bin")]
    assert len(set(mids)) >= 4                                  # bytes grew chunk by chunk
    assert last[2] == last[3] and last[4] == last[5]            # finishes at 100 %
    seen.clear()
    env.eng.reconcile(env.pair, progress=lambda p: seen.append(p))
    assert seen == []                                           # nothing to do -> no noise


def test_copy_keeps_modified_time(env):
    write(f"{env.L}/a.txt", "A", mtime=1_000_000)
    env.sync()
    assert int(os.stat(f"{env.R}/a.txt").st_mtime) == 1_000_000


# ------------------------------------------------------------------ sync types & versions

def link(env, mode="two-way", versions=False):
    return Pair(left=env.L, right=env.R, id=f"{mode}-{versions}", mode=mode, versions=versions,
                names=("Main", "Copy"))


def test_mirror_makes_an_exact_copy(env):
    p = link(env, "mirror")
    write(f"{env.L}/a.txt", "main A", mtime=1_000_500)
    write(f"{env.L}/sub/b.txt", "main B")
    write(f"{env.R}/a.txt", "old copy", mtime=1_000_000)
    write(f"{env.R}/extra.txt", "not in main")
    env.eng.reconcile(p)
    assert tree(env.R) == tree(env.L) == {"a.txt": "main A", "sub/": None, "sub/b.txt": "main B"}
    assert sorted(env.bin.names()) == ["a.txt", "extra.txt"]   # replaced / removed -> bin
    write(f"{env.R}/a.txt", "edited in the copy", mtime=1_000_900)
    env.eng.reconcile(p)
    assert read(f"{env.R}/a.txt") == "main A"                   # copy edits are undone
    assert read(f"{env.L}/a.txt") == "main A"                   # and never reach Main
    shutil.rmtree(f"{env.L}/sub")
    env.eng.reconcile(p)
    assert tree(env.R) == {"a.txt": "main A"}                   # deletions are mirrored


def test_update_copies_but_never_deletes(env):
    p = link(env, "update")
    write(f"{env.L}/a.txt", "v1", mtime=1_000_000)
    write(f"{env.R}/mine.txt", "only in the copy")
    env.eng.reconcile(p)
    assert tree(env.R) == {"a.txt": "v1", "mine.txt": "only in the copy"}
    assert tree(env.L) == {"a.txt": "v1"}                       # nothing flows back
    write(f"{env.L}/a.txt", "v2", mtime=1_000_100)
    env.eng.reconcile(p)
    assert read(f"{env.R}/a.txt") == "v2"                       # updates are copied
    os.remove(f"{env.L}/a.txt")
    env.eng.reconcile(p)
    assert read(f"{env.R}/a.txt") == "v2" and env.bin.items == []   # never deleted


def test_collect_is_update_in_reverse(env):
    p = link(env, "collect")
    write(f"{env.R}/photo.jpg", "pic")
    write(f"{env.L}/keep.txt", "main only")
    env.eng.reconcile(p)
    assert tree(env.L) == {"keep.txt": "main only", "photo.jpg": "pic"}
    assert tree(env.R) == {"photo.jpg": "pic"}
    os.remove(f"{env.R}/photo.jpg")
    env.eng.reconcile(p)
    assert read(f"{env.L}/photo.jpg") == "pic"                  # Main keeps it


def test_versions_are_kept_instead_of_recycled(env):
    import engine
    p = link(env, "two-way", versions=True)
    write(f"{env.L}/Reports/report.docx", "v1", mtime=1_000_000)
    env.eng.reconcile(p)
    write(f"{env.L}/Reports/report.docx", "v2", mtime=1_000_100)
    env.eng.reconcile(p)
    vroot = os.path.join(env.R, engine.VERSIONS_DIR)
    moments = sorted(os.listdir(vroot))
    assert len(moments) == 1 and engine.moment_time(moments[0]) is not None
    assert moments[0].endswith("_001")                          # date_time_number
    kept = os.path.join(vroot, moments[0], "Reports", "report.docx")
    assert read(kept) == "v1"                                   # old copy, usual subfolder
    os.remove(f"{env.L}/Reports/report.docx")
    env.eng.reconcile(p)
    assert not os.path.exists(f"{env.R}/Reports/report.docx")
    all_kept = sorted(read(os.path.join(r, f)) for r, _, fs in os.walk(vroot) for f in fs)
    assert all_kept == ["v1", "v2"] and len(os.listdir(vroot)) == 2   # a new moment folder
    assert env.bin.items == []                                  # nothing went to the bin
    assert engine.VERSIONS_DIR not in os.listdir(env.L)         # versions never sync back


def test_one_moment_folder_per_sync_run(env):
    import engine
    p = link(env, "update", versions=True)
    for n in ("a.txt", "sub/b.txt", "sub/deeper/c.txt"):
        write(f"{env.L}/{n}", "old", mtime=1_000_000)
    env.eng.reconcile(p)
    for n in ("a.txt", "sub/b.txt", "sub/deeper/c.txt"):
        write(f"{env.L}/{n}", "new", mtime=1_000_100)
    env.eng.reconcile(p)                                        # one run replaces three files
    vroot = os.path.join(env.R, engine.VERSIONS_DIR)
    (moment,) = os.listdir(vroot)
    assert tree(os.path.join(vroot, moment)) == {
        "a.txt": "old", "sub/": None, "sub/b.txt": "old", "sub/deeper/": None,
        "sub/deeper/c.txt": "old"}


def test_removed_folder_is_kept_whole(env):
    import engine
    p = link(env, "mirror", versions=True)
    write(f"{env.L}/Project/x.txt", "x")
    write(f"{env.L}/Project/y.txt", "y")
    env.eng.reconcile(p)
    shutil.rmtree(f"{env.L}/Project")
    env.eng.reconcile(p)
    vroot = os.path.join(env.R, engine.VERSIONS_DIR)
    (moment,) = os.listdir(vroot)
    assert tree(os.path.join(vroot, moment)) == {"Project/": None, "Project/x.txt": "x",
                                                  "Project/y.txt": "y"}


def test_moment_names_count_up_within_a_second(tmp_path):
    import engine
    t = time.time()
    names = [engine.new_moment(str(tmp_path), t) for _ in range(3)]
    assert [n[-3:] for n in names] == ["001", "002", "003"]
    assert len({n[:-4] for n in names}) == 1


def test_old_versions_are_removed_after_their_days(tmp_path):
    import engine
    root = str(tmp_path)
    day = 86400
    now = time.time()
    old = engine.new_moment(root, now - 40 * day)
    recent = engine.new_moment(root, now - 5 * day)
    stray = os.path.join(root, engine.VERSIONS_DIR, "my notes")   # not ours: never touched
    os.makedirs(stray)
    assert engine.prune_versions(root, 30, now) == 1
    left = os.listdir(os.path.join(root, engine.VERSIONS_DIR))
    assert recent in left and old not in left and "my notes" in left
    assert engine.prune_versions(root, 0, now + 1000 * day) == 0   # 0 days = keep forever

def test_mirror_guards_against_an_emptied_main(env):
    p = link(env, "mirror")
    for i in range(25):
        write(f"{env.L}/{i}.txt", str(i))
    env.eng.reconcile(p)
    for f in os.listdir(env.L):
        os.remove(os.path.join(env.L, f))
    with pytest.raises(LooksEmpty):
        env.eng.reconcile(p)
    assert len(os.listdir(env.R)) == 25


def test_renames_are_not_mirrored_in_update_mode(env):
    p = link(env, "update")
    write(f"{env.L}/old.txt", "x")
    env.eng.reconcile(p)
    os.rename(f"{env.L}/old.txt", f"{env.L}/new.txt")
    assert not env.eng.mirror_move(p, 0, "old.txt", "new.txt")
    env.eng.reconcile(p)
    assert tree(env.R) == {"old.txt": "x", "new.txt": "x"}      # nothing deleted


def test_old_pairs_migrate_to_jobs(tmp_path):
    import json
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"pairs": [{"left": "A", "right": "B", "paused": True, "id": "p1"}]}))
    c = Config.load(str(cfg))
    j = c.jobs[0]
    assert (j.main, j.paused, j.id) == ("A", True, "p1")
    assert [(m.path, m.mode, m.id) for m in j.members] == [("B", "two-way", "p1")]  # history kept


# ------------------------------------------------------------------ jobs with several folders

@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    paths = {n: str(tmp_path / n) for n in ("Main", "Two", "Mirror", "Backup")}
    for p in paths.values():
        os.makedirs(p)
    bin_ = Bin(str(tmp_path / "bin"))
    os.makedirs(bin_.path)
    ctrl = Controller(config=Config(), db=StateDB(str(tmp_path / "s.db")), trash=bin_,
                      debounce=0.3, save_config=False)
    ctrl.start()
    ctrl.add_job(paths["Main"], [(paths["Two"], "two-way", False),
                                 (paths["Mirror"], "mirror", False),
                                 (paths["Backup"], "update", False)])
    assert wait_for(lambda: ctrl.overall() == "synced")
    yield ctrl, paths, bin_
    ctrl.stop()


def test_live_changes_flow_through_main(job):
    ctrl, P, bin_ = job
    write(f"{P['Two']}/notes.txt", "from Two")                  # edit in a connected folder
    for n in ("Main", "Mirror", "Backup"):
        assert wait_for(lambda n=n: os.path.exists(f"{P[n]}/notes.txt")), n
    write(f"{P['Mirror']}/notes.txt", "edited in the mirror")   # a mirror can't change Main
    assert wait_for(lambda: read(f"{P['Mirror']}/notes.txt") == "from Two")
    assert read(f"{P['Main']}/notes.txt") == "from Two"
    os.remove(f"{P['Main']}/notes.txt")                         # delete in Main
    assert wait_for(lambda: not os.path.exists(f"{P['Two']}/notes.txt")
                    and not os.path.exists(f"{P['Mirror']}/notes.txt"))
    time.sleep(1)
    assert read(f"{P['Backup']}/notes.txt") == "from Two"       # the backup keeps it
    acts = ctrl.recent_activity(ctrl.jobs()[0].id, n=50)
    assert any("notes.txt" in m for _, _, m in acts)


def test_live_edit_job_add_and_remove_folders(job, tmp_path):
    ctrl, P, bin_ = job
    write(f"{P['Main']}/a.txt", "A")
    assert wait_for(lambda: os.path.exists(f"{P['Two']}/a.txt"))
    j = ctrl.jobs()[0]
    extra = str(tmp_path / "Extra")
    os.makedirs(extra)
    members = [m for m in j.members if m.path != P["Mirror"]]   # drop the mirror
    from store import Member
    members.append(Member(path=extra, mode="two-way"))
    ctrl.update_job(j.id, j.main, members)
    assert wait_for(lambda: os.path.exists(f"{extra}/a.txt"))  # new folder gets the files
    write(f"{P['Mirror']}/after.txt", "no longer synced")
    write(f"{extra}/b.txt", "B")
    assert wait_for(lambda: os.path.exists(f"{P['Two']}/b.txt"))
    time.sleep(1)
    assert not os.path.exists(f"{P['Main']}/after.txt")         # removed folder is ignored
    assert bin_.items == []                                     # nothing re-merged or binned


def _unplug(path):
    """Like pulling out a USB drive: the folder is simply gone (moved aside)."""
    os.rename(path, path + ".unplugged")


def _plug_in(path):
    os.rename(path + ".unplugged", path)


def test_unplugged_folder_doesnt_stop_the_others(job, monkeypatch):
    import watcher
    monkeypatch.setattr(watcher, "HEALTH_EVERY", 0.3)
    ctrl, P, bin_ = job
    jid = ctrl.jobs()[0].id
    backup_id = next(m.id for m in ctrl.jobs()[0].members if m.path == P["Backup"])
    write(f"{P['Main']}/old.txt", "old")
    assert wait_for(lambda: os.path.exists(f"{P['Backup']}/old.txt"))

    _unplug(P["Backup"])                                         # the backup drive goes away
    assert wait_for(lambda: backup_id in ctrl.snapshot()[jid].offline)
    write(f"{P['Main']}/new.txt", "made while the drive was out")
    write(f"{P['Two']}/from-two.txt", "two")
    assert wait_for(lambda: os.path.exists(f"{P['Two']}/new.txt")
                    and os.path.exists(f"{P['Mirror']}/new.txt")
                    and os.path.exists(f"{P['Main']}/from-two.txt"))   # the rest keep going
    st = ctrl.snapshot()[jid]
    assert st.state == "synced" and backup_id in st.offline
    assert not os.path.exists(f"{P['Backup']}/new.txt")
    assert any("isn't connected" in m for _, _, m in ctrl.recent_activity(jid, n=20))

    _plug_in(P["Backup"])                                        # it comes back
    assert wait_for(lambda: os.path.exists(f"{P['Backup']}/new.txt")
                    and os.path.exists(f"{P['Backup']}/from-two.txt"))
    assert wait_for(lambda: ctrl.snapshot()[jid].offline == ())
    assert read(f"{P['Backup']}/old.txt") == "old"               # nothing lost on the way
    assert any("is back" in m for _, _, m in ctrl.recent_activity(jid, n=20))


def test_two_way_folder_catches_up_on_deletes_after_being_away(job, monkeypatch):
    import watcher
    monkeypatch.setattr(watcher, "HEALTH_EVERY", 0.3)
    ctrl, P, bin_ = job
    jid = ctrl.jobs()[0].id
    write(f"{P['Main']}/gone.txt", "x")
    assert wait_for(lambda: os.path.exists(f"{P['Two']}/gone.txt"))
    _unplug(P["Two"])
    assert wait_for(lambda: ctrl.snapshot()[jid].offline)
    os.remove(f"{P['Main']}/gone.txt")                           # deleted while Two was away
    assert wait_for(lambda: not os.path.exists(f"{P['Mirror']}/gone.txt"))
    _plug_in(P["Two"])
    assert wait_for(lambda: not os.path.exists(f"{P['Two']}/gone.txt"))   # caught up
    assert not os.path.exists(f"{P['Main']}/gone.txt")           # not brought back to Main


def test_all_connected_folders_away_then_back(tmp_path, monkeypatch):
    import watcher
    monkeypatch.setattr(watcher, "HEALTH_EVERY", 0.3)
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    main, usb = str(tmp_path / "Main"), str(tmp_path / "USB")
    os.makedirs(main), os.makedirs(usb)
    bin_ = Bin(str(tmp_path / "bin"))
    os.makedirs(bin_.path)
    ctrl = Controller(config=Config(), db=StateDB(str(tmp_path / "s.db")), trash=bin_,
                      debounce=0.3, save_config=False)
    ctrl.start()
    try:
        j = ctrl.add_job(main, [(usb, "update", False)])
        assert wait_for(lambda: ctrl.overall() == "synced")
        _unplug(usb)
        assert wait_for(lambda: ctrl.snapshot()[j.id].state == "waiting")
        assert "to be connected" in ctrl.snapshot()[j.id].message
        write(f"{main}/while-away.txt", "x")
        _plug_in(usb)
        assert wait_for(lambda: os.path.exists(f"{usb}/while-away.txt"))
        assert wait_for(lambda: ctrl.snapshot()[j.id].state == "synced")
    finally:
        ctrl.stop()


def test_main_missing_still_stops_the_job(job, monkeypatch):
    import watcher
    monkeypatch.setattr(watcher, "HEALTH_EVERY", 0.3)
    ctrl, P, bin_ = job
    jid = ctrl.jobs()[0].id
    _unplug(P["Main"])
    assert wait_for(lambda: ctrl.snapshot()[jid].state == "error")
    assert "Folder not found" in ctrl.snapshot()[jid].message
    _plug_in(P["Main"])
    assert wait_for(lambda: ctrl.snapshot()[jid].state == "synced")
    assert bin_.items == []                                      # nothing deleted anywhere


def test_can_edit_a_job_while_one_folder_is_unplugged(job, monkeypatch):
    import watcher
    monkeypatch.setattr(watcher, "HEALTH_EVERY", 0.3)
    ctrl, P, bin_ = job
    j = ctrl.jobs()[0]
    _unplug(P["Backup"])
    paths = [m.path for m in j.members]
    assert ctrl.validate(j.main, paths, ignore=j.id) is None     # editing is allowed
    assert ctrl.validate(j.main, paths) is not None              # a brand-new job isn't


def test_pause_one_folder_the_others_go_on(job):
    ctrl, P, bin_ = job
    j = ctrl.jobs()[0]
    two = next(m.id for m in j.members if m.path == P["Two"])
    ctrl.set_member_paused(j.id, two, True)
    assert wait_for(lambda: two in ctrl.snapshot()[j.id].held)
    assert next(m for m in ctrl.jobs()[0].members if m.id == two).paused   # remembered
    write(f"{P['Main']}/while-paused.txt", "x")
    write(f"{P['Two']}/made-in-two.txt", "y")
    assert wait_for(lambda: os.path.exists(f"{P['Mirror']}/while-paused.txt")
                    and os.path.exists(f"{P['Backup']}/while-paused.txt"))
    time.sleep(1)
    assert not os.path.exists(f"{P['Two']}/while-paused.txt")      # paused: left alone
    assert not os.path.exists(f"{P['Main']}/made-in-two.txt")
    st = ctrl.snapshot()[j.id]
    assert st.state == "synced"
    ctrl.set_member_paused(j.id, two, False)                          # resume: catches up
    assert wait_for(lambda: os.path.exists(f"{P['Two']}/while-paused.txt")
                    and os.path.exists(f"{P['Main']}/made-in-two.txt"))
    assert wait_for(lambda: ctrl.snapshot()[j.id].held == ())
    msgs = [m for _, _, m in ctrl.recent_activity(j.id, n=20)]
    assert any(m.startswith("Paused") for m in msgs) and any(m.startswith("Resumed") for m in msgs)


def test_folder_pause_is_saved_and_exported(tmp_path):
    from store import Config, Job, Member, export_settings, read_settings_file
    cfg = Config(jobs=[Job(main=str(tmp_path / "M"),
                           members=[Member(path=str(tmp_path / "A"), paused=True),
                                    Member(path=str(tmp_path / "B"))])])
    cfg.save(str(tmp_path / "config.json"))
    again = Config.load(str(tmp_path / "config.json"))
    assert [m.paused for m in again.jobs[0].members] == [True, False]
    export_settings(str(tmp_path / "x.kiru"), cfg)
    back = read_settings_file(str(tmp_path / "x.kiru"))
    assert [m.paused for m in back.jobs[0].members] == [True, False]


def test_every_folder_paused(job):
    ctrl, P, bin_ = job
    j = ctrl.jobs()[0]
    for m in j.members:
        ctrl.set_member_paused(j.id, m.id, True)
    assert wait_for(lambda: ctrl.snapshot()[j.id].state == "paused")
    assert "paused" in ctrl.snapshot()[j.id].message


# ------------------------------------------------------------------ export / import

def test_export_and_import_round_trip(tmp_path):
    import json
    from store import Job, Member, export_settings, read_settings_file
    cfg = Config(theme="dark", jobs=[
        Job(main="D:/Final project", paused=True,
            members=[Member("E:/Backup", "update", True, keep_days=90), Member("F:/Copy", "mirror")])])
    out = tmp_path / "SyncMate folder syncs.kiru"
    assert export_settings(str(out), cfg, "2.2") == 1
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["kind"] == "SyncMate folder syncs" and raw["app_version"] == "2.2"
    assert "id" not in raw["jobs"][0]                         # ids are this PC's business
    back = read_settings_file(str(out))
    j = back.jobs[0]
    assert back.theme == "dark" and j.main == "D:/Final project" and j.paused
    assert [(m.path, m.mode, m.versions, m.keep_days) for m in j.members] == [
        ("E:/Backup", "update", True, 90), ("F:/Copy", "mirror", False, 30)]
    assert j.id != cfg.jobs[0].id                             # fresh ids on import


def test_import_rejects_other_files(tmp_path):
    from store import read_settings_file
    bad = tmp_path / "notes.json"
    bad.write_text('{"hello": 1}')
    with pytest.raises(ValueError):
        read_settings_file(str(bad))
    junk = tmp_path / "photo.json"
    junk.write_bytes(b"\x89PNG...")
    with pytest.raises(ValueError):
        read_settings_file(str(junk))


def test_main_keeps_versions_for_the_longest_wish():
    from store import Job, Member
    from watcher import _keep_days_for_main
    j = Job(main="M", members=[Member("A", versions=True, keep_days=7),
                               Member("B", versions=True, keep_days=90),
                               Member("C", versions=False, keep_days=1)])
    assert _keep_days_for_main(j) == 90
    j.members.append(Member("D", versions=True, keep_days=0))
    assert _keep_days_for_main(j) == 0                         # one 'forever' wins
