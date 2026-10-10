@echo off
setlocal
cd /d "%~dp0"
title Building SyncMate

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

echo [1/4] Creating a private Python environment...
if not exist .venv %PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat || goto :fail

echo [2/4] Installing libraries...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller || goto :fail

echo [3/4] Drawing the icon...
python icon.py || goto :fail

echo [4/4] Building SyncMate.exe (takes a minute)...
pyinstaller --noconfirm --clean --onefile --windowed --name SyncMate --icon syncmate.ico ^
  --collect-data sv_ttk ^
  --hidden-import pystray._win32 ^
  --collect-submodules send2trash ^
  --collect-submodules darkdetect ^
  --collect-submodules watchdog.observers ^
  app.py >build.log 2>&1 || goto :fail

copy /y dist\SyncMate.exe SyncMate.exe >nul || goto :fail
echo.
echo Done!  SyncMate.exe is ready in this folder.
pause
exit /b 0

:fail
echo.
echo Build failed. See build.log for details.
pause
exit /b 1
