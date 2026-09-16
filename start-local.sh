#!/usr/bin/env bash
# PhotoVault lokal starten -- venv statt Docker-Verbund.
#
# Es gibt zwei Betriebsmodi, und sie sind nicht dasselbe:
#
#   start.sh        der Weg fuer neue Nutzer. Baut einen eigenen
#                   Docker-Verbund mit eigenem Qdrant, haengt den Fotoordner
#                   als /photos hinein und fragt beim ersten Mal, wo er
#                   liegt. Fuer eine frische Maschine richtig.
#
#   start-local.sh  dieser hier. Nimmt die venv unter ~/.venvs/photovault,
#                   das Qdrant aus dem ai-stack und die Fotos unter
#                   /mnt/photo. Das ist die Installation, die auf dieser
#                   Maschine laeuft und deren Index auf /mnt/photo-Pfade
#                   zeigt -- start.sh wuerde daneben einen zweiten,
#                   anders konfigurierten Stapel hochziehen.
#
# Fragt nichts. Was schon laeuft, bleibt laufen; was fehlt, wird benannt.
#
#   ./start-local.sh            starten (oder melden, dass es laeuft)
#   ./start-local.sh status     nur nachsehen
#   ./start-local.sh stop       beenden
#   ./start-local.sh restart    beenden und neu starten
#   ./start-local.sh logs       die letzten Zeilen zeigen
set -u

cd "$(dirname "$0")" || exit 1

PHOTO_DIR="${PHOTO_DIR:-/mnt/photo}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
VENV="${VENV:-$HOME/.venvs/photovault}"
LOG="logs/uvicorn.log"

# Wo die Vorschaubilder liegen.
#
# Die Voreinstellung im Code ist `data/thumbs` im Projektordner -- richtig
# fuer den Container, wo ein benanntes Volume darunter liegt. Hier nicht:
# der Projektordner liegt auf /mnt/d, also NTFS ueber 9p, und das ist
# gemessen viermal langsamer als die Linux-Platte (0,89 ms gegen 3,62 ms je
# Kachel). Bei 14.593 Kacheln sind das 13 Sekunden gegen 53.
#
# Wer es anders will, setzt PHOTOVAULT_THUMB_CACHE selbst.
export PHOTOVAULT_THUMB_CACHE="${PHOTOVAULT_THUMB_CACHE:-$HOME/.cache/photovault-thumbs}"

# LiteLLM ist der Chokepoint: Captions = Pool `local`, Embeddings = `embedder`.
# Welches Gewicht dahinter liegt, steht nur in der LiteLLM-Config.
export LITELLM_URL="${LITELLM_URL:-http://127.0.0.1:4000}"
export PHOTOVAULT_CAPTION_MODEL="${PHOTOVAULT_CAPTION_MODEL:-local}"
export PHOTOVAULT_EMBED_MODEL="${PHOTOVAULT_EMBED_MODEL:-embedder}"
export PHOTOVAULT_CAPTION_NUM_CTX="${PHOTOVAULT_CAPTION_NUM_CTX:-0}"
if [ -z "${LITELLM_MASTER_KEY:-}" ]; then
    _ai_env="${AI_STACK_ENV:-/mnt/d/ai/ai-stack/.env}"
    if [ -f "${_ai_env}" ]; then
        _line=$(grep -E '^LITELLM_MASTER_KEY=' "${_ai_env}" | tail -n1 || true)
        _val="${_line#LITELLM_MASTER_KEY=}"
        _val="${_val#\"}"; _val="${_val%\"}"
        _val="${_val#\'}"; _val="${_val%\'}"
        export LITELLM_MASTER_KEY="${_val}"
    fi
    unset _ai_env _line _val
fi

# Und derselbe Ort als Merkzettel neben den Daten.
#
# Ein `export` gilt nur fuer die Prozesse, die dieses Skript startet. Ein
# spaeter direkt aufgerufenes `python -m tools.thumbs` erbt ihn nicht --
# es fiel auf `data/thumbs` zurueck und hat beim Umbenennen 29.083 Kacheln
# dorthin geschoben, wo der Server sie nicht mehr fand. Der Merkzettel
# macht die Wahl unabhaengig davon, wer gerade wie gestartet wurde.
mkdir -p data
printf '%s\n' "${PHOTOVAULT_THUMB_CACHE}" > data/thumbs.path

ok()   { printf '  \033[32m*\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mx\033[0m %s\n' "$1"; }

laeuft() { curl -sf -m 2 -o /dev/null "http://${API_HOST}:${API_PORT}/"; }
pids()   { pgrep -f "uvicorn api.main:app" 2>/dev/null; }

# --- Voraussetzungen ---------------------------------------------------------

pruefe_venv() {
    if [ ! -x "${VENV}/bin/python" ]; then
        bad "Keine venv unter ${VENV}"
        echo "     python3 -m venv ${VENV} && ${VENV}/bin/pip install -e '.[dev]'"
        echo "     (nicht ins Repo legen: /mnt/d ist NTFS ueber 9p und macht"
        echo "      den Modell-Ladevorgang um Minuten langsamer)"
        return 1
    fi
    ok "venv: ${VENV}"
}

pruefe_fotos() {
    if mountpoint -q "${PHOTO_DIR}" 2>/dev/null; then
        ok "Fotos: ${PHOTO_DIR} ($(ls -1 "${PHOTO_DIR}" 2>/dev/null | wc -l) Eintraege)"
        return 0
    fi
    # Der Mount steht in /etc/fstab mit `nofail` -- nach einem WSL-Neustart
    # ist er weg, wenn das NAS beim Boot noch nicht da war. WSL startet ihn
    # ueber /usr/local/sbin/photovault-mount.sh nach; hier nur nachholen,
    # und zwar mit `sudo -n`, damit nicht-interaktive Starts nicht an einer
    # Passwortabfrage haengen.
    #
    # `sudo -n` setzt eine NOPASSWD-Regel voraus -- ohne sie scheitert der
    # Aufruf sofort und still, und der Zweig unten meldet "Mount
    # fehlgeschlagen", obwohl nur die Berechtigung fehlt. Auf crackdesk
    # gemessen 2026-09-17 per `sudo -n -l`:
    #     (root) NOPASSWD: /usr/bin/mount /mnt/photo, /usr/bin/umount /mnt/photo
    # Auf einer frischen Maschine muss diese Zeile erst in /etc/sudoers.d/
    # stehen; sie gehoert nicht ins Repo, weil sie Maschinenzustand ist.
    warn "${PHOTO_DIR} ist nicht gemountet -- hole das nach"
    if sudo -n mount "${PHOTO_DIR}" 2>/dev/null && mountpoint -q "${PHOTO_DIR}"; then
        ok "Fotos: ${PHOTO_DIR} nachgemountet"
        return 0
    fi
    bad "Mount fehlgeschlagen -- laeuft das NAS? (Eintrag: /etc/fstab)"
    echo "     Ohne Fotos startet die Oberflaeche trotzdem: der Index liegt in"
    echo "     Qdrant, nur Vorschaubilder und neue Laeufe brauchen die Dateien."
    return 0   # kein Abbruch: lesen geht ohne Mount
}

pruefe_qdrant() {
    if curl -sf -m 3 -o /dev/null "${QDRANT_URL}/"; then
        local n
        n=$(curl -sf -m 5 "${QDRANT_URL}/collections/photos" \
            | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null)
        ok "Qdrant: ${QDRANT_URL}${n:+ (${n} Fotos im Index)}"
        return 0
    fi
    warn "Qdrant antwortet nicht -- versuche den Container zu starten"
    docker start qdrant >/dev/null 2>&1
    for _ in $(seq 1 15); do
        curl -sf -m 2 -o /dev/null "${QDRANT_URL}/" && { ok "Qdrant: gestartet"; return 0; }
        sleep 1
    done
    bad "Kein Qdrant. Ohne es gibt es keinen Index -- die Oberflaeche bleibt leer."
    echo "     Der Container gehoert zum ai-stack, nicht zu PhotoVault:"
    echo "     cd /mnt/d/ai/ai-stack && docker compose up -d qdrant"
    return 1
}

pruefe_cache() {
    local d="${PHOTOVAULT_THUMB_CACHE}"
    if [ -d "${d}" ]; then
        local n b
        n=$(find "${d}" -name "*.jpg" 2>/dev/null | wc -l)
        b=$(du -sm "${d}" 2>/dev/null | cut -f1)
        ok "Vorschaubilder: ${d} (${n} Kacheln, ${b} MB)"
    else
        warn "Vorschaubilder: ${d} -- noch leer, wird beim ersten Ansehen gefuellt"
    fi
}

pruefe_ollama() {
    if curl -sf -m 3 -o /dev/null "${OLLAMA_URL}/api/tags"; then
        ok "Ollama: ${OLLAMA_URL}"
    else
        # Kein Fehler: alles ausser Bildbeschreibungen und Freitextsuche
        # funktioniert ohne. Die Oberflaeche sperrt die betroffenen Felder
        # selbst und sagt warum.
        warn "Ollama antwortet nicht -- Bildbeschreibungen und Freitextsuche bleiben gesperrt"
    fi
}

# --- Befehle -----------------------------------------------------------------

tu_status() {
    if laeuft; then
        ok "PhotoVault laeuft: http://${API_HOST}:${API_PORT}  (PID $(pids | tr '\n' ' '))"
    else
        local p; p=$(pids)
        if [ -n "${p}" ]; then
            warn "Prozess da (PID ${p}), antwortet aber nicht -- siehe ${LOG}"
        else
            echo "  PhotoVault laeuft nicht."
        fi
    fi
}

tu_stop() {
    local p; p=$(pids)
    if [ -z "${p}" ]; then echo "  Laeuft nicht."; return 0; fi
    echo "  beende ${p}"
    kill ${p} 2>/dev/null
    for _ in $(seq 1 10); do
        [ -z "$(pids)" ] && { ok "beendet"; return 0; }
        sleep 1
    done
    warn "reagiert nicht, erzwinge"
    kill -9 $(pids) 2>/dev/null
}

tu_start() {
    echo
    echo "  PhotoVault (lokal)"
    echo "  =================="
    echo
    if laeuft; then
        ok "Laeuft schon: http://${API_HOST}:${API_PORT}"
        echo
        echo "  Neustart mit:  ./start-local.sh restart"
        return 0
    fi
    pruefe_venv || return 1
    pruefe_fotos
    pruefe_qdrant || return 1
    pruefe_cache
    pruefe_ollama
    echo
    mkdir -p logs
    nohup "${VENV}/bin/python" -m uvicorn api.main:app \
        --host "${API_HOST}" --port "${API_PORT}" >> "${LOG}" 2>&1 &
    disown
    printf '  starte '
    for _ in $(seq 1 40); do
        laeuft && { echo " ok"; echo; ok "http://${API_HOST}:${API_PORT}"; return 0; }
        printf '.'
        sleep 1
    done
    echo
    bad "Nach 40 Sekunden keine Antwort. Letzte Zeilen:"
    tail -n 20 "${LOG}" | sed 's/^/     /'
    return 1
}

case "${1:-start}" in
    start)   tu_start ;;
    stop)    tu_stop ;;
    restart) tu_stop; tu_start ;;
    status)  tu_status ;;
    logs)    tail -n "${2:-40}" "${LOG}" ;;
    *)       echo "  Bekannt: start | stop | restart | status | logs"; exit 2 ;;
esac
