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

rem ---------------------------------------------------------------- 1-2. Python and libraries
echo [1/6] Preparing a private Python environment...
if not exist .venv %PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat || goto :fail

echo [2/6] Installing libraries...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller || goto :fail

rem The build goes into its own folder named after the version, e.g. "build 2.1".
set "VER="
for /f "delims=" %%v in ('python -c "import version; print(version.VERSION)"') do set "VER=%%v"
if not defined VER (
  echo       Couldn't read the version number from version.py.
  goto :fail
)
set "OUT=build %VER%"
echo       Version %VER% will be built into "%OUT%"

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
echo [4/6] Clearing out the old build...
rem Leftovers from before builds got their own folder:
for %%f in (SyncMate.exe SyncMate.spec build.log syncmate.ico) do if exist "%%f" del /f /q "%%f"
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist SyncMate.exe (
  echo       Couldn't delete the old SyncMate.exe. Close it ^(tray icon ^> Quit^) and run this again.
  goto :fail
)
rem This version's folder, if it was built before:
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%OUT%" (
  echo       Couldn't clear "%OUT%". Close anything using it and run this again.
  goto :fail
)
mkdir "%OUT%" || goto :fail

rem ---------------------------------------------------------------- 5-6. build
echo [5/6] Drawing the icon...
python icon.py "%OUT%\syncmate.ico" >nul || goto :fail

echo [6/6] Building SyncMate.exe (takes a minute)...
pyinstaller --noconfirm --clean --onefile --windowed --name SyncMate ^
  --icon "%~dp0%OUT%\syncmate.ico" ^
  --distpath "%~dp0%OUT%" --workpath "%~dp0%OUT%\work" --specpath "%~dp0%OUT%" ^
  --collect-data sv_ttk ^
  --collect-all tkinterdnd2 ^
  --hidden-import pystray._win32 ^
  --collect-submodules send2trash ^
  --collect-submodules darkdetect ^
  --collect-submodules watchdog.observers ^
  "%~dp0app.py" > "%OUT%\build.log" 2>&1 || goto :fail
if not exist "%OUT%\SyncMate.exe" goto :fail

rem Keep the build folder tidy: just the app, its icon and the build log.
if exist "%OUT%\work" rmdir /s /q "%OUT%\work"
if exist "%OUT%\SyncMate.spec" del /f /q "%OUT%\SyncMate.spec"

rem Windows remembers each file's icon; tell it to refresh so the new icon shows up.
ie4uinit.exe -show >nul 2>nul
ie4uinit.exe -ClearIconCache >nul 2>nul
echo.
echo Done!  SyncMate %VER% is ready in the "%OUT%" folder.

if defined WASRUNNING (
  echo Starting the new SyncMate...
  start "" "%~dp0%OUT%\SyncMate.exe"
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
