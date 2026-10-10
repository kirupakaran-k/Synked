@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Building SyncMate
rem Don't leave __pycache__ folders next to the source files.
set "PYTHONDONTWRITEBYTECODE=1"

rem ---------------------------------------------------------------- where things go
rem The finished app goes in "build <version>" OUTSIDE the version folders:
rem   run from  D:\syncmate\version 2\2.2 - ...\build.bat  ->  D:\syncmate\build 2.2
rem   run from  D:\syncmate\build.bat                      ->  D:\syncmate\build 2.2
for %%I in ("%~dp0.") do set "HERE=%%~fI"
set "PARENTNAME=-"
for %%I in ("%HERE%\..") do (
  set "PARENT=%%~fI"
  set "PARENTNAME=%%~nxI"
)
set "ROOT=%HERE%"
if /i "%PARENTNAME:~0,8%"=="version " for %%I in ("%PARENT%\..") do set "ROOT=%%~fI"
rem The build tools live outside your SyncMate folder too (reused by every build).
set "ENV=%LOCALAPPDATA%\SyncMate\build-env"

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

rem ---------------------------------------------------------------- 1-2. Python and libraries
echo [1/6] Preparing the build tools...
if not exist "%ENV%\Scripts\python.exe" echo       First build on this PC: setting them up in "%ENV%"
if not exist "%ENV%\Scripts\python.exe" %PY% -m venv "%ENV%" || goto :fail
call "%ENV%\Scripts\activate.bat" || goto :fail

echo [2/6] Installing libraries...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller || goto :fail

set "VER="
for /f "delims=" %%v in ('python -c "import version; print(version.VERSION)"') do set "VER=%%v"
if not defined VER (
  echo       Couldn't read the version number from version.py.
  goto :fail
)
set "OUT=%ROOT%\build %VER%"
echo       SyncMate %VER% will be built into "%OUT%"

rem ---------------------------------------------------------------- 3. close the running app
echo [3/6] Closing SyncMate if it's running...
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

rem ---------------------------------------------------------------- 4. clear old builds
echo [4/6] Clearing out the old build and leftovers...
rem Leftovers from older build scripts in this folder (tools, caches, old build output):
if exist ".venv" rmdir /s /q ".venv"
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "tests\__pycache__" rmdir /s /q "tests\__pycache__"
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
for %%f in (SyncMate.exe SyncMate.spec build.log syncmate.ico) do if exist "%%f" del /f /q "%%f"
if /i not "%ROOT%"=="%HERE%" if exist "build %VER%" rmdir /s /q "build %VER%"
if exist SyncMate.exe (
  echo       Couldn't delete the old SyncMate.exe. Close it ^(tray icon ^> Quit^) and run this again.
  goto :fail
)
rem This version's build folder, if it was built before:
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%OUT%" echo       Couldn't clear "%OUT%". Close anything using it and run this again.
if exist "%OUT%" goto :fail
mkdir "%OUT%" || goto :fail

rem ---------------------------------------------------------------- 5-6. build
echo [5/6] Drawing the icon...
python icon.py "%OUT%\syncmate.ico" >nul || goto :fail

echo [6/6] Building SyncMate.exe (takes a minute)...
pyinstaller --noconfirm --clean --onefile --windowed --name SyncMate ^
  --icon "%OUT%\syncmate.ico" ^
  --distpath "%OUT%" --workpath "%OUT%\work" --specpath "%OUT%" ^
  --collect-data sv_ttk ^
  --collect-all tkinterdnd2 ^
  --hidden-import pystray._win32 ^
  --collect-submodules send2trash ^
  --collect-submodules darkdetect ^
  --collect-submodules watchdog.observers ^
  "%HERE%\app.py" > "%OUT%\build.log" 2>&1 || goto :fail
if not exist "%OUT%\SyncMate.exe" goto :fail

rem Keep the build folder tidy: just the app, its icon and the build log.
if exist "%OUT%\work" rmdir /s /q "%OUT%\work"
if exist "%OUT%\SyncMate.spec" del /f /q "%OUT%\SyncMate.spec"
if exist "__pycache__" rmdir /s /q "__pycache__"

rem Windows remembers each file's icon; tell it to refresh so the new icon shows up.
ie4uinit.exe -show >nul 2>nul
ie4uinit.exe -ClearIconCache >nul 2>nul
echo.
echo Done!  SyncMate %VER% is ready in "%OUT%".

if defined WASRUNNING (
  echo Starting the new SyncMate...
  start "" "%OUT%\SyncMate.exe"
)
rem Close this window by itself after a moment (it stays open only if something fails).
echo This window will close in 3 seconds...
>nul ping -n 4 127.0.0.1
exit /b 0

:fail
echo.
echo Build failed.
if defined OUT if exist "%OUT%\build.log" echo See "%OUT%\build.log" for details.
pause
exit /b 1

rem ---------------------------------------------------------------- helper
:running
tasklist /FI "IMAGENAME eq SyncMate.exe" /NH 2>nul | find /I "SyncMate.exe" >nul
exit /b %errorlevel%
