@echo off
REM PhotoVault starten -- Doppelklick. Die Arbeit steckt in start.ps1
REM (Windows PowerShell ist auf jedem Windows 10/11 da); diese Datei gibt
REM nur weiter und haelt das Fenster offen, wenn etwas schiefging.
REM
REM   start.bat            starten; beim ersten Mal der Einrichtungsassistent
REM   start.bat stop       Verbund anhalten (Index und Einstellungen bleiben)
REM   start.bat status
REM   start.bat restart
REM   start.bat setup      Assistent erzwingen, auch wo eine lokale Installation liegt
chcp 65001 >nul 2>&1
title PhotoVault
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 (
    echo.
    pause
    exit /b %ERRORLEVEL%
)
if "%~1"=="" (
    echo.
    echo   Dieses Fenster kann zu.
    ping -n 6 127.0.0.1 >nul
)
