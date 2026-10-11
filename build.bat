@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Building Synked
rem Don't leave __pycache__ folders next to the source files.
set "PYTHONDONTWRITEBYTECODE=1"

rem ---------------------------------------------------------------- where things go
rem The finished app goes in "build <version>" OUTSIDE the version folders:
rem   run from  D:\syncmate\version 3\3.3 - ...\build.bat  ->  D:\syncmate\build 3.3
rem   run from  D:\syncmate\build.bat                      ->  D:\syncmate\build 3.3
for %%I in ("%~dp0.") do set "HERE=%%~fI"
set "PARENTNAME=-"
for %%I in ("%HERE%\..") do (
  set "PARENT=%%~fI"
  set "PARENTNAME=%%~nxI"
)
set "ROOT=%HERE%"
if /i "%PARENTNAME:~0,8%"=="version " for %%I in ("%PARENT%\..") do set "ROOT=%%~fI"
rem The build tools live outside your source folder too (reused by every build).
set "ENV=%LOCALAPPDATA%\Synked\build-env"
set "OLDENV=%LOCALAPPDATA%\SyncMate\build-env"

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
echo [1/6] Preparing the build tools...   (started %TIME:~0,8%)
rem SyncMate (the name up to 3.2) kept them under its own name: move them over instead of
rem downloading everything again, then let venv fix the paths inside.
if not exist "%ENV%\Scripts\python.exe" if exist "%OLDENV%\Scripts\python.exe" (
  echo       Moving the build tools from SyncMate's folder to Synked's...
  if not exist "%LOCALAPPDATA%\Synked" mkdir "%LOCALAPPDATA%\Synked"
  move "%OLDENV%" "%ENV%" >nul && %PY% -m venv "%ENV%"
)
if not exist "%ENV%\Scripts\python.exe" echo       First build on this PC: setting them up in "%ENV%"
if not exist "%ENV%\Scripts\python.exe" %PY% -m venv "%ENV%" || goto :fail
call "%ENV%\Scripts\activate.bat" || goto :fail

echo [2/6] Installing libraries...   (started %TIME:~0,8%)
echo       The first time for version 3 this downloads Qt (about 100 MB). On a slow
echo       connection that takes a while: the bars below show how far each download has got,
echo       with its speed and time left. Libraries already downloaded are reused.
python -m pip install --upgrade pip --progress-bar on --disable-pip-version-check --timeout 60 --retries 10
rem Newer pip can pick up a broken download where it stopped instead of starting over.
set "RESUME="
python -m pip install --help 2>nul | find "--resume-retries" >nul && set "RESUME=--resume-retries 10"
python -m pip install -r requirements.txt pyinstaller --progress-bar on --disable-pip-version-check --timeout 60 --retries 10 %RESUME% || goto :fail
echo       Libraries ready.   (%TIME:~0,8%)

set "VER="
for /f "delims=" %%v in ('python -c "import version; print(version.VERSION)"') do set "VER=%%v"
if not defined VER (
  echo       Couldn't read the version number from version.py.
  goto :fail
)
set "OUT=%ROOT%\build %VER%"
echo       Synked %VER% will be built into "%OUT%"

rem ---------------------------------------------------------------- 3. close the running app
echo [3/6] Closing Synked (or the older SyncMate) if it's running...   (%TIME:~0,8%)
set "WASRUNNING="
call :running && set "WASRUNNING=1"
if not defined WASRUNNING goto :closed
rem Ask it to finish what it's doing and quit (it checks for this file every second).
if not exist "%APPDATA%\Synked" mkdir "%APPDATA%\Synked"
type nul > "%APPDATA%\Synked\quit.flag"
if exist "%APPDATA%\SyncMate" type nul > "%APPDATA%\SyncMate\quit.flag"
for /L %%i in (1,1,10) do (
  call :running || goto :closed
  >nul ping -n 2 127.0.0.1
)
echo       It didn't close by itself, so forcing it to close.
taskkill /F /IM Synked.exe >nul 2>nul
taskkill /F /IM SyncMate.exe >nul 2>nul
>nul ping -n 3 127.0.0.1
:closed

rem ---------------------------------------------------------------- 4. clear old builds
echo [4/6] Clearing out the old build and leftovers...   (%TIME:~0,8%)
rem Leftovers from older build scripts in this folder (tools, caches, old build output):
if exist ".venv" rmdir /s /q ".venv"
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "tests\__pycache__" rmdir /s /q "tests\__pycache__"
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
for %%f in (Synked.exe Synked.spec SyncMate.exe SyncMate.spec build.log synked.ico syncmate.ico exe_info.txt) do if exist "%%f" del /f /q "%%f"
if /i not "%ROOT%"=="%HERE%" if exist "build %VER%" rmdir /s /q "build %VER%"
if exist Synked.exe (
  echo       Couldn't delete the old Synked.exe. Close it ^(tray icon ^> Quit^) and run this again.
  goto :fail
)
rem This version's build folder, if it was built before:
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%OUT%" echo       Couldn't clear "%OUT%". Close anything using it and run this again.
if exist "%OUT%" goto :fail
mkdir "%OUT%" || goto :fail

rem ---------------------------------------------------------------- 5-6. build
echo [5/6] Drawing the icon and the app's details...   (%TIME:~0,8%)
python icon.py "%OUT%\synked.ico" >nul || goto :fail
rem Name, version, company and copyright shown in the exe's Properties ^> Details.
python exe_info.py "%OUT%\exe_info.txt" || goto :fail

echo [6/6] Building Synked.exe (a minute or two)...   (started %TIME:~0,8%)
echo       Each step is shown below and also saved to build.log. The last step (packing
echo       everything into one .exe) is quiet for up to a minute; that's normal.
set "PYI_ARGS=--noconfirm --clean --onefile --windowed --name Synked --collect-submodules send2trash --collect-submodules watchdog.observers --exclude-module tkinter"
set "PYTHONUNBUFFERED=1"
where powershell >nul 2>nul || goto :quietbuild
powershell -NoProfile -ExecutionPolicy Bypass -Command "& python -m PyInstaller $env:PYI_ARGS.Split(' ') --icon \"$env:OUT\synked.ico\" --version-file \"$env:OUT\exe_info.txt\" --distpath \"$env:OUT\" --workpath \"$env:OUT\work\" --specpath \"$env:OUT\" \"$env:HERE\app.py\" 2>&1 | ForEach-Object { \"$_\" } | Tee-Object -FilePath \"$env:OUT\build.log\" | ForEach-Object { '      ' + ($_ -replace '^[0-9]+ (INFO|WARNING): ', '') }; exit $LASTEXITCODE" || goto :fail
goto :built
:quietbuild
rem (No PowerShell to show the steps live: just write them to build.log.)
python -m PyInstaller %PYI_ARGS% --icon "%OUT%\synked.ico" --version-file "%OUT%\exe_info.txt" --distpath "%OUT%" --workpath "%OUT%\work" --specpath "%OUT%" "%HERE%\app.py" > "%OUT%\build.log" 2>&1 || goto :fail
:built
echo       Packed.   (%TIME:~0,8%)
if not exist "%OUT%\Synked.exe" goto :fail

rem Keep the build folder tidy: just the app, its icon and the build log.
if exist "%OUT%\work" rmdir /s /q "%OUT%\work"
if exist "%OUT%\Synked.spec" del /f /q "%OUT%\Synked.spec"
if exist "%OUT%\exe_info.txt" del /f /q "%OUT%\exe_info.txt"
if exist "__pycache__" rmdir /s /q "__pycache__"

rem Windows remembers each file's icon; tell it to refresh so the new icon shows up.
ie4uinit.exe -show >nul 2>nul
ie4uinit.exe -ClearIconCache >nul 2>nul
echo.
echo Done!  Synked %VER% is ready in "%OUT%".

if defined WASRUNNING (
  echo Starting the new Synked...
  start "" "%OUT%\Synked.exe"
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
rem True if Synked, or SyncMate (its name up to 3.2), is running.
tasklist /FI "IMAGENAME eq Synked.exe" /NH 2>nul | find /I "Synked.exe" >nul && exit /b 0
tasklist /FI "IMAGENAME eq SyncMate.exe" /NH 2>nul | find /I "SyncMate.exe" >nul
exit /b %errorlevel%
