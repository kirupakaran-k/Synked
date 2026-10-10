# Changelog

Every version of Synked, newest first. Up to 3.2 the app was called SyncMate.
Versions before 1.1 had no number in the app; they're labelled 1.0.0 to 1.0.8.

## 3.0 - Qt window, smooth animations

The window is rebuilt with Qt (PySide6, free and open source) instead of Tkinter. Qt draws the whole window off-screen and only shows finished pictures, so nothing is ever seen being drawn piece by piece. Opening a folder sync slides its editor down like a curtain (and back up when closing); switching theme cross-fades to the new colours; the settings menu fades and slides down from the gear with a soft shadow; removing a sync folds its card away. Each sync shows its Main folder with tree lines to every connected folder and a smaller badge for its sync type. New look: Fluent/Material-style cards, buttons, boxes, slider, thin progress bar and hints, in light and dark. Tray icon, drag and drop, Start with Windows, export/import (.kiru) and the activity log now use Qt too, so sv_ttk, tkinterdnd2, pystray and darkdetect are no longer needed. The sync engine, your folder syncs and settings are unchanged and carry over. Later in 3.0: the settings button uses Android's gear; opening or closing a sync no longer stretches its top part (the card grows and shrinks exactly with the sliding editor); the scroll bar keeps its place so the list can't flicker between two widths; Add folder sync slides the new card open from its own top instead of starting very tall; build.bat shows download and build progress.

## 2.5 - Card list layout, smooth updates

Each folder sync shows its Main folder on top with tree lines branching down to every connected folder, each with its own badge in the Add folder sync button's colours: two-way (left and right arrows), Main to folder (right arrow) or folder to Main (left arrow). Clicking a sync opens it downward like a curtain and closes it back up; a new folder sync does the same, and Cancel slides it shut. Switching theme is now a single quick redraw: the styles for both themes are prepared at start-up, Sun Valley's slow whole-window recolouring pass is replaced by a light one, and cards are no longer torn down and rebuilt. The screen-picture cross-fade and paused-painting experiments from late 2.4 / early 2.5 are gone. Hints and the activity window fade in.

## 2.4 - Hover hints, versions slider, cleaner buttons

Old-files choice is now just 'Recycle Bin' or 'Keep old versions', with a slider (1 day to 1 year, or forever) that appears only when keeping versions. Explanations of sync types and old-files choices appear when hovering (also over entries in the open dropdown) instead of a paragraph. No '…' on any button, and the ⋮ menu button has no box. The window title shows the version ('SyncMate - 2.4') and the settings footer reads 'SyncMate 2.4 - By Er.K.K.'. The days can also be typed in a box next to the slider (any number of days, or 'forever'; slide all the way right for forever), with hover hints on the slider and box. The type dropdown fits its text like the old-files one, and the window rearranges itself when made narrow (rows wrap and stack). The button that removes a connected folder is a red box with a bold white cross. Fixed: switching theme could leave some text with the other theme's colours, and a hover hint could stay on screen (e.g. after minimising); hints now close as soon as the mouse leaves or the window is hidden. The arrow between a sync's folders is a bigger badge in the Add folder sync button's colours (pointing down/up when the window is narrow). Resizing the window is smooth: while the edge is being dragged only the window moves, and the contents are laid out once at the new size when the mouse button is let go; unchanged text is no longer redrawn every refresh. Smooth transitions: changing theme, opening or closing a sync and adding a new one cross-fade from the old look to the new one (the redrawing in between is hidden), and the settings menu fades and slides in like a modern drop-down.

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
