# SyncMate

Keeps any two folders identical, continuously and in both directions. Point one side
at a folder inside OneDrive and that folder is backed up to the cloud, without
moving it.

## Get it running

1. Install Python 3.10 or newer: `winget install Python.Python.3.12`
   (or from python.org; tick **Add python.exe to PATH**).
2. Double-click **`build.bat`** in the version's folder. A couple of minutes later the app is
   ready in a folder named after its version, **outside** the version folders, e.g.
   `D:\syncmate\build 3.1\SyncMate.exe` (with its icon and build log).

   Version 3 draws its window with **Qt** (PySide6, free and open source). The first build
   downloads it (about 100 MB), so it takes a few minutes; later builds are quick.
   The build tools (Python libraries) are kept in
   `%LOCALAPPDATA%\SyncMate\build-env`, not in your SyncMate folder, and are reused by every
   build. No `.venv` or `__pycache__` folders are left behind; leftovers from older builds
   are removed. You don't need any of them to *run* SyncMate, only to build it.

   To **update** later, just run `build.bat` again. It closes the running SyncMate
   (letting it finish any copy in progress), rebuilds that version's build folder, starts the
   new SyncMate if one was running, and closes its own window when done. If something goes
   wrong, the window stays open so you can read why. If *Start with Windows* is on, it
   follows to the newest build the next time that build runs.

3. Run `build 3.1\SyncMate.exe`, click **Add folder sync**, choose a **Main folder** and one or more
   **connected folders**, and click **Start syncing**. You can also **drag folders from
   File Explorer** onto the boxes or onto the window (several at once: the first becomes
   Main, the rest connected folders).

   Each folder sync shows its Main folder on top with a tree branching down to every
   connected folder, each with a badge for how it syncs (⇄ two-way, → Main to folder,
   ← folder to Main). It shows live progress while it copies (current file, items and MB done,
   and a progress bar), then the time and day it last synced. **Click a folder sync** (or
   its ⋮ button) to expand it: change folders, sync types and options, add or remove
   connected folders and **Apply**; Pause / Resume, Sync now, see its **recent activity**,
   or Remove it.

   Qt draws the whole window off-screen and shows only finished pictures, so nothing is
   ever seen half-drawn: a folder sync slides open downward like a curtain (and back up),
   switching theme cross-fades to the new colours, and the settings menu fades and slides
   in. The window rearranges itself when you make it narrow (rows wrap).
   The **⚙ settings** button (top right) has Start with Windows, the Theme slider
   (Auto / Light / Dark), **Export / Import** of your folder sync configurations, and the full
   activity log.

You can also run it without building: `py app.py`
(after `py -m pip install -r requirements.txt`).

## Sync types (chosen per connected folder)

Hover over a sync type or an old-files choice in SyncMate (also inside the open dropdown
list) to see what it does.

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
- **Keep old versions**, for as long as you choose: drag the slider (1 day up to 1 year, or all
  the way right for forever) or type any number of days in the box next to it: every replaced or removed file is moved into the hidden `.syncmate-versions`
  folder at the top of that synced folder instead (deleting a subfolder never touches it). Each
  sync gets its own folder there, named by date, time and a number, with the old files
  inside in their usual subfolders:

  ```
  Backup\.syncmate-versions\
      2026-10-09_17-42-10_001\Reports\budget.xlsx
      2026-10-10_12-38-05_001\Reports\budget.xlsx
                              \Notes\plan.docx
  ```

  To get something back, open the dated folder and copy the file out. Dated folders older
  than the chosen number of days are removed automatically after each full sync. Main's
  `.syncmate-versions` (used when two-way or collect folders replace files in Main) is kept
  for the longest time any of its folders asks for.

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
| A connected folder's drive is unplugged | Keeps syncing the other folders; that one is shown as *Not connected* and catches up by itself when it's back (deletes nothing meanwhile) |
| The Main folder disappears | Pauses that folder sync, deletes nothing, and resumes when Main is back |
| A whole folder is suddenly empty | Stops and asks: restore the files, or delete them on the other side too |

Files that are never synced: Office lock files (`~$…`), `*.tmp`, `desktop.ini`,
`Thumbs.db`, and partial downloads.

Every copy is written to a temporary file first and swapped in at the end, so a
crash or power cut never leaves a half-written file.

## OneDrive tip

If a OneDrive folder holds cloud-only files (cloud icon), SyncMate downloads each one
the first time it syncs it. To keep things fast, right-click the OneDrive folder and
choose **Always keep on this device**.

## Where your settings are kept, and exporting them

Your folder syncs (`config.json`), their sync history (`state.db`) and the log
(`syncmate.log`) are kept in `%APPDATA%\SyncMate`.

**⚙ settings → Folder sync configurations → Export** saves all your folder syncs (folders,
sync types, old-files options, paused or not) and the theme to a **`.kiru`** file wherever you
like. **Import** loads such a file (or an older `.json` export), on this PC or another one.
You can **replace** your current syncs or **add** to them; syncs that would overlap folders
already being synced are skipped. Folders that don't exist on this PC yet are imported
anyway: those syncs wait and start by themselves when the folders appear. Sync history isn't
exported, because it belongs to this PC's copies of the files; after an import the folders
are compared once.

## Files

| File | Purpose |
|---|---|
| `app.py` | Window, tray icon, settings menu, activity log, export/import, start-with-Windows |
| `cards.py` | The folder-sync cards: the folder tree, status, live progress, and the editor that slides open |
| `controls.py` | Toggle switch, theme picker, wrapping rows, tree lines, folder boxes that take dropped folders |
| `theme.py` | Light and dark colours, the window's style sheet, and the icons (drawn in code) |
| `ui_common.py` | Shared helpers (paths, times, sizes) |
| `watcher.py` | Watches every folder of every job, batches changes, runs syncs in the background |
| `engine.py` | The sync rules for each sync type, and version keeping |
| `store.py` | Jobs, settings and sync history (older single-pair settings are converted automatically) |
| `icon.py` | Draws the icon |
| `version.py` | The version number (shown in settings; names the build folder) |
| `tests/` | Automated tests of syncing and of the window: `py -m pytest tests` |
