# SyncMate

Keeps any two folders identical, continuously and in both directions. Point one side
at a folder inside OneDrive and that folder is backed up to the cloud, without
moving it.

## Get it running

1. Install Python 3.10 or newer: `winget install Python.Python.3.12`
   (or from python.org; tick **Add python.exe to PATH**).
2. Double-click **`build.bat`**. A couple of minutes later the app is ready in a folder
   named after its version, e.g. **`build 2.1\SyncMate.exe`** (with its build log).
   Each version gets its own build folder, so older builds stay where they are.

   To **update** later, just run `build.bat` again. It closes the running SyncMate
   (letting it finish any copy in progress), rebuilds that version's folder, starts the new
   SyncMate if one was running, and closes its own window when done. If something goes
   wrong, the window stays open so you can read why. If *Start with Windows* is on, it
   follows to the newest build the next time that build runs.

3. Run `build 2.1\SyncMate.exe`, click **Add folder sync**, choose a **Main folder** and one or more
   **connected folders**, and click **Start syncing**. You can also **drag folders from
   File Explorer** onto the boxes or onto the window (several at once: the first becomes
   Main, the rest connected folders).

   Each folder sync shows live progress while it copies (current file, items and MB done,
   and a progress bar), then the time and day it last synced. **Click a folder sync** (or
   its ⋮ button) to expand it: change folders, sync types and options, add or remove
   connected folders and **Apply**; Pause / Resume, Sync now, see its **recent activity**,
   or Remove it.

   The **⚙ settings** button (top right) has Start with Windows, the Theme slider
   (Auto / Light / Dark) and the full activity log.

You can also run it without building: `py app.py`
(after `py -m pip install -r requirements.txt`).

## Sync types (chosen per connected folder)

Every connected folder syncs with the job's **Main folder**. Changes pass through Main, so
an edit in one connected folder reaches Main and from there the other folders.

| Type | Direction | What it does |
|---|---|---|
| **Two-way** | Main ⇄ folder | Changes flow both ways; if both sides changed, the newer one wins |
| **Mirror** | Main → folder | The folder becomes an exact copy of Main (master/slave): extra files are removed and its own edits are undone |
| **Update** | Main → folder | Incremental: new and changed files are copied; nothing is ever deleted. Good for backups |
| **Collect** | folder → Main | The same, the other way: new and changed files are added to Main, never deleted |

**Old files** (also per connected folder):
- **Recycle Bin**: files that are removed, or a copy's own edits that get overwritten, go to the Recycle Bin.
- **Keep old versions**: every replaced or removed file is kept in a hidden
  `.syncmate-versions` folder inside that folder, named with the date and time,
  e.g. `report [2026-10-10 123805].docx`. The newest 10 versions of each file are kept.

## How it behaves

| What happens | What SyncMate does |
|---|---|
| A file is added or edited on one side | Copies it to the other side within a few seconds |
| A file or folder is deleted | Deletes it on the other side by sending it to the **Recycle Bin** |
| A file or folder is renamed or moved | Renames it on the other side too, with no re-copying |
| The same file changed on both sides | The **newer one wins**; the older one goes to the Recycle Bin |
| Deleted on one side, edited on the other | The edit wins, so nothing is lost |
| Changes happen while SyncMate is closed | It catches up when it next starts |
| A file is open or locked (e.g. in Word) | Retries automatically until it's free |
| A drive is unplugged or a folder disappears | Pauses that pair, deletes nothing, and resumes when the folder is back |
| A whole folder is suddenly empty | Stops and asks: restore the files, or delete them on the other side too |

Files that are never synced: Office lock files (`~$…`), `*.tmp`, `desktop.ini`,
`Thumbs.db`, and partial downloads.

Every copy is written to a temporary file first and swapped in at the end, so a
crash or power cut never leaves a half-written file.

## OneDrive tip

If a OneDrive folder holds cloud-only files (cloud icon), SyncMate downloads each one
the first time it syncs it. To keep things fast, right-click the OneDrive folder and
choose **Always keep on this device**.

## Where your settings are kept

Your folder syncs (`config.json`), their sync history (`state.db`) and the log
(`syncmate.log`) are kept in `%APPDATA%\SyncMate` by default.

To keep them somewhere else (another drive, a backup folder, or to take them to a new PC),
open **⚙ settings → Settings folder → Change…** and pick a folder. SyncMate moves the files
there and carries on syncing. If the folder already has SyncMate settings, you can choose
to use those or replace them. **Use default** moves them back. Pick a folder SyncMate
itself isn't syncing. If the chosen folder can't be found (say, a drive is unplugged),
SyncMate says so and starts with the default folder instead.

## Files

| File | Purpose |
|---|---|
| `app.py` | Window, tray icon, settings panel, start-with-Windows |
| `cards.py` | The folder-sync cards: status, live progress, recent activity, and the expand-to-edit panel |
| `ui_common.py` | Shared helpers for the window (paths, times, sizes, colours) |
| `widgets.py` | The sliding theme switch and the gear icon |
| `watcher.py` | Watches every folder of every job, batches changes, runs syncs in the background |
| `engine.py` | The sync rules for each sync type, and version keeping |
| `store.py` | Jobs, settings and sync history (older single-pair settings are converted automatically) |
| `icon.py` | Draws the icon |
| `version.py` | The version number (shown in settings; names the build folder) |
| `tests/` | Automated tests: `py -m pytest tests` |
