# SyncMate

Keeps any two folders identical, continuously and in both directions. Point one side
at a folder inside OneDrive and that folder is backed up to the cloud, without
moving it.

## Get it running

1. Install Python 3.10 or newer: `winget install Python.Python.3.12`
   (or from python.org; tick **Add python.exe to PATH**).
2. Double-click **`build.bat`**. A couple of minutes later `SyncMate.exe` appears
   in this folder.

   To **update** later, just run `build.bat` again. It closes the running SyncMate
   (letting it finish any copy in progress), deletes the old version, builds the new
   one, starts it again if it was running, and closes its own window when done.
   If something goes wrong, the window stays open so you can read why.
3. Run `SyncMate.exe`, click **Add folder pair**, pick the two folders, and you're done.
   You can also **drag folders from File Explorer**: onto the Folder A / Folder B boxes,
   or straight onto the SyncMate window (drop two at once to fill both boxes).
   Turn on **Start with Windows** so it runs in the background from then on.
   The **⚙ settings** button (top right) has Start with Windows and the Theme slider:
   Auto (follows Windows), Light or Dark.

You can also run it without building: `py app.py`
(after `py -m pip install -r requirements.txt`).

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

## Where things are kept

`%APPDATA%\SyncMate`: settings (`config.json`), sync history (`state.db`) and the
log (`syncmate.log`). **Activity → Open log file** opens this folder.

If you move `SyncMate.exe` after turning on *Start with Windows*, switch it off and on
again so Windows picks up the new location.

## Files

| File | Purpose |
|---|---|
| `app.py` | Window, tray icon, settings panel, start-with-Windows |
| `widgets.py` | The sliding theme switch and the gear icon |
| `watcher.py` | Watches folders, batches changes, runs syncs in the background |
| `engine.py` | The sync rules (what to copy, delete or rename) |
| `store.py` | Settings and sync history |
| `icon.py` | Draws the icon |
| `tests/` | Automated tests: `py -m pytest tests` |
