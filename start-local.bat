@echo off
REM PhotoVault lokal starten -- Doppelklick von Windows aus.
REM
REM Ruft nur start-local.sh in WSL auf. Die Arbeit steckt dort, damit es
REM nicht zwei Fassungen derselben Logik gibt, die auseinanderlaufen.
REM
REM Nicht zu verwechseln mit start.bat: das baut den Docker-Verbund fuer eine
REM frische Maschine auf. Dieses hier startet die Installation, die hier
REM laeuft -- venv, Qdrant aus dem ai-stack, Fotos unter /mnt/photo.

setlocal
set REPO=/mnt/d/repos/photovault

wsl.exe -e bash -lc "cd %REPO% && ./start-local.sh %*"
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
  echo.
  echo   Fehlgeschlagen ^(Code %RC%^). Fenster bleibt offen.
  pause
) else (
  REM Browser aufmachen und Fenster schliessen -- bei Erfolg gibt es nichts
  REM zu lesen, was nicht auch im Browser steht.
  start "" http://127.0.0.1:8000
  timeout /t 2 >nul
)
endlocal
