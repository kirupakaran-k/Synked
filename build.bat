@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Building SyncMate

rem ---------------------------------------------------------------- find Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3 was not found.
  echo Install it with:  winget install Python.Python.3.12
  echo or from https://www.python.org/downloads/  then run this file again.
  pause
  exit /b 1
)

rem ---------------------------------------------------------------- 1. close the running app
echo [1/6] Closing SyncMate if it's running...
set "WASRUNNING="
call :running && set "WASRUNNING=1"
if not defined WASRUNNING goto :closed
rem Ask it to finish what it's doing and quit (it checks for this file every second).
if not exist "%APPDATA%\SyncMate" mkdir "%APPDATA%\SyncMate"
type nul > "%APPDATA%\SyncMate\quit.flag"
for /L %%i in (1,1,10) do (
  call :running || goto :closed
  >nul ping -n 2 127.0.0.1
)
echo       It didn't close by itself, so forcing it to close.
taskkill /F /IM SyncMate.exe >nul 2>nul
>nul ping -n 3 127.0.0.1
:closed

rem ---------------------------------------------------------------- 2. delete the old version
echo [2/6] Deleting the old version...
if exist SyncMate.exe del /f /q SyncMate.exe
if exist SyncMate.exe (
  echo       Couldn't delete SyncMate.exe. Close it ^(tray icon ^> Quit^) and run this again.
  goto :fail
)
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist SyncMate.spec del /f /q SyncMate.spec
if exist syncmate.ico del /f /q syncmate.ico

rem ---------------------------------------------------------------- 3-6. build the new one
echo [3/6] Preparing a private Python environment...
if not exist .venv %PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat || goto :fail

echo [4/6] Installing libraries...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller || goto :fail

echo [5/6] Drawing the icon...
python icon.py >nul || goto :fail

echo [6/6] Building SyncMate.exe (takes a minute)...
pyinstaller --noconfirm --clean --onefile --windowed --name SyncMate --icon syncmate.ico ^
  --collect-data sv_ttk ^
  --hidden-import pystray._win32 ^
  --collect-submodules send2trash ^
  --collect-submodules darkdetect ^
  --collect-submodules watchdog.observers ^
  app.py >build.log 2>&1 || goto :fail

copy /y dist\SyncMate.exe SyncMate.exe >nul || goto :fail

rem Windows remembers each file's icon; tell it to refresh so the new icon shows up.
ie4uinit.exe -show >nul 2>nul
ie4uinit.exe -ClearIconCache >nul 2>nul
echo.
echo Done!  The new SyncMate.exe is ready in this folder.

if defined WASRUNNING (
  echo Starting the new SyncMate...
  start "" "%~dp0SyncMate.exe"
)
rem Close this window by itself after a moment (it stays open only if something fails).
echo This window will close in 3 seconds...
>nul ping -n 4 127.0.0.1
exit /b 0

:fail
echo.
echo Build failed. See build.log for details.
pause
exit /b 1

rem ---------------------------------------------------------------- helper
:running
tasklist /FI "IMAGENAME eq SyncMate.exe" /NH 2>nul | find /I "SyncMate.exe" >nul
exit /b %errorlevel%
