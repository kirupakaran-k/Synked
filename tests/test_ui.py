"""Checks of the window (runs without showing anything on screen)."""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("PySide6")


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNKED_HOME", str(tmp_path / "appdata"))
    import app as A
    from watcher import Controller
    qapp = A.make_app([])
    ctrl = Controller()
    ctrl.start()
    win = A.MainWindow(qapp, ctrl)
    win.resize(900, 700)
    win.show()

    def wait(cond=lambda: False, t=0.3):
        end = time.time() + t
        while time.time() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.005)
        return cond()

    def folder(name):
        p = tmp_path / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    yield win, ctrl, wait, folder
    win.timer.stop()
    ctrl.stop()
    win.hide()
    win.deleteLater()
    qapp.processEvents()


def _job(win, ctrl, wait, folder):
    from store import Member
    j = ctrl.add_job(folder("Main"), [Member(folder("A"), "two-way"), Member(folder("B"), "collect")])
    wait(lambda: ctrl.overall() == "synced", 10)
    win.refresh()
    wait()
    return j


def test_tree_shows_each_folder_with_its_badge(ui):
    win, ctrl, wait, folder = ui
    j = _job(win, ctrl, wait, folder)
    card = win.cards[j.id]
    assert [mode for _, mode in card.badges] == ["two-way", "collect"]


def test_card_opens_like_a_curtain(ui):
    win, ctrl, wait, folder = ui
    j = _job(win, ctrl, wait, folder)
    card = win.cards[j.id]
    card.header.clicked.emit()
    heights = []
    end = time.time() + 0.5
    while time.time() < end:
        win.qapp.processEvents()
        heights.append(card.drawer.height() if card.drawer.isVisible() else 0)
        time.sleep(0.004)
    steps = [h for h in heights if h > 0]
    assert len(set(steps)) >= 6                      # many small steps, not a jump
    assert all(b >= a - 1 for a, b in zip(steps, steps[1:]))   # (1px rounding at the end)
    assert abs(card.drawer.height() - card.drawer.full_height()) <= 1
    assert card.editor.height() == card.drawer.full_height()   # revealed, never squashed


def test_theme_switch_recolours(ui):
    win, ctrl, wait, folder = ui
    import theme
    _job(win, ctrl, wait, folder)
    other = "dark" if win.theme == "light" else "light"
    win.switch_theme(other)
    wait(t=0.5)
    assert theme.current() == other
    assert win.qapp.palette().window().color().name().lower() == theme.PALETTES[other]["window"].lower()


def test_days_box(ui):
    win, ctrl, wait, folder = ui
    from store import KEEP_DAY_STOPS
    j = _job(win, ctrl, wait, folder)
    win.set_expanded(j.id)
    wait(t=0.4)
    row = win.cards[j.id].rows[0]
    row.old.setCurrentIndex(1)
    row.slider.setValue(len(KEEP_DAY_STOPS) - 1)
    assert row.days_box.text() == "forever" and row.keep_days == 0
    row.days_box.setText("45")
    row.days_box.textEdited.emit("45")
    assert row.keep_days == 45
    assert row.value().keep_days == 45


def test_top_part_never_stretches(ui):
    """Opening and closing a card must not stretch or squeeze its top part."""
    win, ctrl, wait, folder = ui
    j = _job(win, ctrl, wait, folder)
    card = win.cards[j.id]
    h0 = card.header.height()
    for _ in range(2):                     # open, then close
        card.header.clicked.emit()
        seen = set()
        end = time.time() + 0.45
        while time.time() < end:
            win.qapp.processEvents()
            seen.add(card.header.height())
            time.sleep(0.004)
        assert seen == {h0}


def test_new_sync_card_grows_from_its_top(ui):
    """'Add folder sync' slides the new card open from its own top part: it is never
    taller than it ends up (a brand-new widget starts at Qt's default 640×480)."""
    win, ctrl, wait, folder = ui
    win.add_btn.click()
    heights = []
    end = time.time() + 0.5
    while time.time() < end:
        win.qapp.processEvents()
        card = win.cards.get("new")
        if card is not None:
            heights.append(card.height())
        time.sleep(0.004)
    assert max(heights) - heights[-1] <= 1
    assert all(b >= a - 1 for a, b in zip(heights, heights[1:]))
