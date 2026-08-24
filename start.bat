@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title PhotoVault

echo.
echo   PhotoVault
echo   ==========
echo.

REM --- 1. Laeuft Docker? -----------------------------------------------------
docker version >nul 2>&1
if errorlevel 1 (
    echo   Docker laeuft nicht.
    echo.
    echo   Bitte "Docker Desktop" starten und warten, bis unten links das
    echo   Symbol gruen ist. Dann dieses Fenster erneut oeffnen.
    echo.
    echo   Noch nicht installiert? https://www.docker.com/products/docker-desktop/
    echo.
    pause
    exit /b 1
)

REM --- 2. Wo liegen die Fotos? -----------------------------------------------
if not exist ".env" (
    echo   Beim ersten Start: Wo liegen deine Fotos?
    echo.
    echo   Beispiel:  D:\Fotos
    echo   Ein Netzlaufwerk geht auch, wenn es im Explorer sichtbar ist.
    echo.
    set /p PHOTODIR="   Ordner: "
    if "!PHOTODIR!"=="" (
        echo.
        echo   Kein Ordner angegeben. Abbruch.
        pause
        exit /b 1
    )
    if not exist "!PHOTODIR!" (
        echo.
        echo   Diesen Ordner gibt es nicht: !PHOTODIR!
        pause
        exit /b 1
    )
    REM Docker mag Schraegstriche lieber als Backslashes.
    set "PHOTODIR=!PHOTODIR:\=/!"
    > .env echo PHOTO_DIR=!PHOTODIR!
    >> .env echo API_PORT=8000
    >> .env echo QDRANT_PORT=6333
    >> .env echo OLLAMA_URL=http://host.docker.internal:11434
    echo.
    echo   Gemerkt in .env -- beim naechsten Mal geht es ohne Nachfrage.
)

REM --- 3. Was soll eingelesen werden? ----------------------------------------
if not exist "sources.txt" (
    > sources.txt echo # Welche Verzeichnisse eingelesen werden.
    >> sources.txt echo # /photos ist dein Fotoordner, so wie er im Container heisst.
    >> sources.txt echo # Zeilen mit '#' am Anfang zaehlen nicht mit.
    >> sources.txt echo /photos
    >> sources.txt echo.
    >> sources.txt echo # Was draussen bleibt -- Bildschirmfotos und Heruntergeladenes
    >> sources.txt echo # gehoeren nicht in eine Foto-Sammlung:
    >> sources.txt echo -/photos/Screenshots
    >> sources.txt echo -/photos/Download
)

REM --- 4. Starten ------------------------------------------------------------
echo.
echo   Starte ... (beim ersten Mal dauert das einige Minuten,
echo   es werden rund 2 GB heruntergeladen)
echo.
docker compose up -d --build
if errorlevel 1 (
    echo.
    echo   Start fehlgeschlagen. Die Meldung oben sagt meist warum.
    pause
    exit /b 1
)

echo.
echo   Warte, bis PhotoVault bereit ist ...
set /a TRIES=0
:waitloop
set /a TRIES+=1
curl -s -o nul -m 2 http://localhost:8000/ && goto ready
if !TRIES! GEQ 60 (
    echo   Dauert ungewoehnlich lange. Schau mit: docker compose logs api
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto waitloop

:ready
echo.
echo   Laeuft: http://localhost:8000
start "" http://localhost:8000

REM --- 5. Fotos einlesen -----------------------------------------------------
echo.
echo   Noch sind keine Fotos eingelesen.
echo.
set /p DOSCAN="   Jetzt einlesen? (j/n): "
if /i not "!DOSCAN!"=="j" (
    echo.
    echo   Spaeter mit:  docker compose exec api python -m ingest.pipeline --sources-file sources.txt --skip-caption
    echo.
    pause
    exit /b 0
)

echo.
echo   Erst ein Blick, was gefunden wird -- es wird noch nichts gespeichert:
echo.
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --dry-run

echo.
set /p GO="   Passt das? Dann einlesen (j/n): "
if /i not "!GO!"=="j" (
    echo.
    echo   Abgebrochen. Du kannst sources.txt anpassen und neu starten.
    pause
    exit /b 0
)

echo.
echo   Liest ein. Das dauert je nach Menge und Rechner Minuten bis Stunden;
echo   ein Abbruch ist unkritisch, es geht danach an derselben Stelle weiter.
echo.
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --skip-caption

echo.
echo   Fertig. Oeffne http://localhost:8000 und fang mit "Wer ist das?" an.
echo.
pause
