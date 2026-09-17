@echo off
REM PhotoVault lokal starten -- Doppelklick von Windows aus.
REM
REM Ruft start-local.sh in WSL auf. Die Arbeit steckt dort, damit es
REM nicht zwei Fassungen derselben Logik gibt, die auseinanderlaufen.
REM
REM start.bat erkennt diese Installation und landet ebenfalls hier.
REM Frische Maschinen ohne ~/.venvs/photovault bleiben beim Docker-Wizard.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"

REM Repo-Pfad in WSL, ohne festes Laufwerk D:\ im Skript.
set "REPO="
for /f "delims=" %%i in ('wsl.exe wslpath -a "%cd%" 2^>nul') do set "REPO=%%i"
if not defined REPO set "REPO=/mnt/d/repos/photovault"

REM Optionale Maschineneinstellungen -- nicht im Git.
set "CFG=%USERPROFILE%\.config\photovault\runtime"
if exist "%CFG%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%CFG%") do (
        if /i "%%A"=="SMB_UNC" set "SMB_UNC=%%B"
        if /i "%%A"=="PHOTO_DIR" set "PHOTO_DIR=%%B"
    )
)

if /i "%ACTION%"=="start" goto preflight
if /i "%ACTION%"=="restart" goto preflight
goto wsl

:preflight
if defined SMB_UNC (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check-windows-share.ps1" -Unc "!SMB_UNC!"
)

:wsl
if defined PHOTO_DIR set "WSLENV=PHOTO_DIR"
wsl.exe -e bash -lc "cd '%REPO%' && ./start-local.sh %*"
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
    echo.
    echo   Fehlgeschlagen ^(Code %RC%^). Fenster bleibt offen.
    pause
    endlocal & exit /b %RC%
)

if /i "%ACTION%"=="start" goto browser
if /i "%ACTION%"=="restart" goto browser
endlocal & exit /b 0

:browser
start "" http://127.0.0.1:8000
if "%~1"=="" ping -n 3 127.0.0.1 >nul
endlocal & exit /b 0
