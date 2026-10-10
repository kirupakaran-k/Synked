# Changelog

Every version of Synked, newest first. Up to 3.2 the app was called SyncMate.
Versions before 1.1 had no number in the app; they're labelled 1.0.0 to 1.0.8.

## 2.3 - kiru exports, dated version folders

Exports are saved as .kiru files; the settings section is called 'Folder sync configurations' with plain Export / Import buttons. Kept old versions are organised in one folder per sync moment (e.g. 2026-10-10_12-38-05_001\Reports\budget.xlsx) and kept for 7 days, 30 days, 90 days, 1 year or forever, chosen per connected folder; the .syncmate-versions folder is now hidden on Windows.

## 2.2 - Export and import, cleaner builds

Settings has Export… / Import… for your folder syncs (replace or add; overlapping syncs skipped; missing folders wait until they appear). The 'settings folder' option from 2.1 is replaced by this. build.bat now puts the app in 'build <version>' outside the version folders (e.g. D:\syncmate\build 2.2), keeps its tools in %LOCALAPPDATA%\SyncMate\build-env and leaves no .venv or __pycache__ behind.

## 2.1 - Build folders, choosable settings folder

build.bat builds each version into its own 'build <version>' folder (e.g. 'build 2.1'); Settings has a 'Settings folder' option to keep your syncs, sync history and log in a folder of your choice.

## 2.0 - Multi-folder syncs and sync types

Jobs with a Main folder and any number of connected folders; per-folder sync type (Two-way, Mirror, Update, Collect) and old-files setting (Recycle Bin or keep the last 10 versions); recent activity in each sync.

## 1.4 - Live progress, expandable syncs

Live progress (file, items, MB, progress bar) and 'synced at time, day'; folder syncs expand in place to edit (⋮ or click anywhere); 'Add folder sync' opens a blank expanded card.

## 1.3 - Drag and drop folders

Drag folders from File Explorer onto the folder boxes or onto the window.

## 1.2 - Thinner plus, icon cache refresh

Slimmer + sign; build.bat refreshes Windows' icon cache after building.

## 1.1 - Sharp window icon, version shown

Window/taskbar icon generated as a proper multi-size icon; version number shown in settings.

## 1.0.8 - Same-height buttons, activity log in settings

All buttons the same height, big bold + on Add folder pair, larger Theme label, activity log moved into settings.

## 1.0.7 - Settings panel with theme slider

Gear button opens a settings panel with Start with Windows and a sliding Auto/Light/Dark switch; build.bat closes its own window when done.

## 1.0.6 - build.bat closes and replaces the app

build.bat closes a running SyncMate cleanly, deletes the old version, builds and restarts it; leftover temp files from a forced close are cleaned up.

## 1.0.5 - Pale blue logo background

Logo tile changed from cream to pale blue.

## 1.0.4 - Blue retro logo

New logo: retro 70s-style striped sync loop in blues on a cream tile; status shown as a dot on the tray icon.

## 1.0.3 - Light, dark and auto theme switch

Theme switch (Auto / Light / Dark) in the bottom bar; the choice is remembered.

## 1.0.2 - Folder B suggestion dropdown

Folder B became a dropdown that offers OneDrive\<Folder A name> as a suggestion.

## 1.0.1 - Folder B not auto-filled

Folder B is no longer filled in automatically when you pick Folder A.

## 1.0.0 - First build

The first working app: two-way sync of folder pairs, tray icon, Start with Windows, Recycle Bin for deletes, Add folder pair dialog (Folder B was auto-filled with a OneDrive suggestion).
