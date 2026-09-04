#!/usr/bin/env bash
# PhotoVault starten -- fuer Linux und macOS.
# Windows: start.bat doppelklicken.
set -u

cd "$(dirname "$0")" || exit 1
echo
echo "  PhotoVault"
echo "  =========="
echo

# --- 1. Laeuft Docker? -------------------------------------------------------
if ! docker version >/dev/null 2>&1; then
    echo "  Docker laeuft nicht."
    echo
    echo "  macOS:  Docker Desktop starten und warten, bis das Symbol oben"
    echo "          in der Menueleiste ruhig steht."
    echo "  Linux:  sudo systemctl start docker"
    echo
    echo "  Noch nicht installiert? https://www.docker.com/products/docker-desktop/"
    echo
    exit 1
fi

# --- 2. Wo liegen die Fotos? -------------------------------------------------
if [ ! -f .env ]; then
    echo "  Beim ersten Start: Wo liegen deine Fotos?"
    echo
    echo "  Beispiel:  /home/$(whoami)/Bilder   oder   /Volumes/NAS/Fotos"
    echo
    read -rp "   Ordner: " PHOTODIR
    if [ -z "${PHOTODIR}" ]; then
        echo; echo "  Kein Ordner angegeben. Abbruch."; exit 1
    fi
    if [ ! -d "${PHOTODIR}" ]; then
        echo; echo "  Diesen Ordner gibt es nicht: ${PHOTODIR}"; exit 1
    fi
    {
        echo "PHOTO_DIR=${PHOTODIR}"
        echo "API_PORT=8000"
        echo "QDRANT_PORT=6333"
        echo "OLLAMA_URL=http://host.docker.internal:11434"
    } > .env
    echo
    echo "  Gemerkt in .env -- beim naechsten Mal geht es ohne Nachfrage."
fi

# --- 3. Was soll eingelesen werden? ------------------------------------------
if [ ! -f sources.txt ]; then
    cat > sources.txt <<'EOF'
# Welche Verzeichnisse eingelesen werden.
# /photos ist dein Fotoordner, so wie er im Container heisst.
# Zeilen mit '#' am Anfang zaehlen nicht mit.
/photos

# Was draussen bleibt -- Bildschirmfotos und Heruntergeladenes gehoeren
# nicht in eine Foto-Sammlung:
-/photos/Screenshots
-/photos/Download
EOF
fi

# --- 4. Starten --------------------------------------------------------------
echo
echo "  Starte ... (beim ersten Mal dauert das einige Minuten,"
echo "  es werden rund 2 GB heruntergeladen)"
echo
docker compose up -d --build || { echo; echo "  Start fehlgeschlagen."; exit 1; }

echo
printf "  Warte, bis PhotoVault bereit ist "
for _ in $(seq 1 60); do
    if curl -sf -m 2 -o /dev/null http://localhost:8000/; then
        echo " ok"
        break
    fi
    printf "."
    sleep 2
done
echo
echo "  Laeuft: http://localhost:8000"
command -v xdg-open >/dev/null && xdg-open http://localhost:8000 >/dev/null 2>&1
command -v open >/dev/null && open http://localhost:8000 >/dev/null 2>&1

# --- 5. Fotos einlesen -------------------------------------------------------
echo
echo "  Noch sind keine Fotos eingelesen."
echo
read -rp "   Jetzt einlesen? (j/n): " DOSCAN
if [ "${DOSCAN}" != "j" ] && [ "${DOSCAN}" != "J" ]; then
    echo
    echo "  Spaeter mit:"
    echo "    docker compose exec api python -m ingest.pipeline --sources-file sources.txt --skip-caption"
    echo
    exit 0
fi

echo
echo "  Erst ein Blick, was gefunden wird -- es wird noch nichts gespeichert:"
echo
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --dry-run

echo
read -rp "   Passt das? Dann einlesen (j/n): " GO
if [ "${GO}" != "j" ] && [ "${GO}" != "J" ]; then
    echo; echo "  Abgebrochen. Du kannst sources.txt anpassen und neu starten."; exit 0
fi

echo
echo "  Liest ein. Das dauert je nach Menge und Rechner Minuten bis Stunden;"
echo "  ein Abbruch ist unkritisch, es geht danach an derselben Stelle weiter."
echo
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --skip-caption

echo
echo "  Fertig. Oeffne http://localhost:8000 und fang mit \"Wer ist das?\" an."
echo
