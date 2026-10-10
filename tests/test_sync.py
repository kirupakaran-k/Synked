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
    write(f"{env.L}/report.docx", "v1", mtime=1_000_000)
    env.eng.reconcile(p)
    write(f"{env.L}/report.docx", "v2", mtime=1_000_100)
    env.eng.reconcile(p)
    vdir = os.path.join(env.R, engine.VERSIONS_DIR)
    vs = os.listdir(vdir)
    assert len(vs) == 1 and vs[0].startswith("report [") and vs[0].endswith("].docx")
    assert read(os.path.join(vdir, vs[0])) == "v1"              # the old content is kept
    os.remove(f"{env.L}/report.docx")
    env.eng.reconcile(p)
    assert not os.path.exists(f"{env.R}/report.docx")
    assert sorted(read(os.path.join(vdir, f)) for f in os.listdir(vdir)) == ["v1", "v2"]
    assert env.bin.items == []                                  # nothing went to the bin
    assert engine.VERSIONS_DIR not in os.listdir(env.L)         # versions never sync back


def test_versions_keep_only_the_newest_ten(env):
    import engine
    p = link(env, "update", versions=True)
    for i in range(14):
        write(f"{env.L}/a.txt", f"v{i}", mtime=1_000_000 + i * 10)
        env.eng.reconcile(p)
    vdir = os.path.join(env.R, engine.VERSIONS_DIR)
    kept = sorted(read(os.path.join(vdir, f)) for f in os.listdir(vdir))
    assert len(kept) == engine.KEEP_VERSIONS
    assert "v0" not in kept and "v12" in kept and read(f"{env.R}/a.txt") == "v13"


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


# ------------------------------------------------------------------ settings folder

def test_settings_folder_can_be_chosen(tmp_path, monkeypatch):
    import store
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    assert store.data_dir() == store.app_dir() and store.chosen_data_dir() is None
    elsewhere = tmp_path / "My settings"
    elsewhere.mkdir()
    store.set_data_dir(str(elsewhere))
    assert store.data_dir() == str(elsewhere) and store.data_dir_problem() is None
    Config(theme="dark").save()                                  # saves into the chosen folder
    assert (elsewhere / "config.json").exists()
    store.set_data_dir(None)                                     # back to the default
    assert store.data_dir() == store.app_dir()


def test_missing_settings_folder_falls_back_safely(tmp_path, monkeypatch):
    import store
    monkeypatch.setenv("SYNCMATE_HOME", str(tmp_path / "home"))
    gone = tmp_path / "USB drive" / "SyncMate"
    gone.mkdir(parents=True)
    store.set_data_dir(str(gone))
    shutil.rmtree(tmp_path / "USB drive")                        # drive unplugged
    assert store.data_dir() == store.app_dir()                   # nothing is written there
    assert "not found" in store.data_dir_problem()
    assert not gone.exists()
