#!/usr/bin/env bash
# PhotoVault starten -- Linux und macOS. Windows: start.bat doppelklicken.
#
#   ./start.sh            starten; beim ersten Mal der Einrichtungsassistent
#   ./start.sh stop       Verbund anhalten (Index und Einstellungen bleiben)
#   ./start.sh status
#   ./start.sh restart
#   ./start.sh setup      Assistent erzwingen, auch wo eine lokale Installation liegt
#
# Was das Skript tut, und warum (die Windows-Fassung start.ps1 tut dasselbe):
#
# 1. Docker pruefen und im Klartext sagen, was fehlt.
# 2. .env anlegen: freie Ports, Ollama aus dem Buendel, gemessener Grafikspeicher.
# 3. docker-compose.override.yml erzeugen: die Orte, an denen Fotos liegen
#    koennen (macOS /Users und /Volumes; Linux $HOME, /media, /mnt, /run/media),
#    *lesend* unter /host/...; die gewaehlten Fotoordner (data/sources.txt)
#    zusaetzlich *beschreibbar* am selben Pfad. Nur dort darf PhotoVault
#    schreiben -- Papierkorb, Verschieben, exif_repair --, und nirgends sonst.
# 4. Verbund hochfahren, Browser auf die Einrichtung.
# 5. Zweite Phase: sobald im Browser die Ordner gewaehlt sind, die Override
#    mit den beschreibbaren Mounts neu schreiben und den Container neu
#    erstellen. Der Nutzer tippt dafuer nichts; die Seite wartet.
#
# ZIP von GitHub: das Ausfuehrbar-Bit fehlt dann -- `bash start.sh` geht immer.
set -u

cd "$(dirname "$0")" || exit 1
REPO="$(pwd)"
ENV_FILE="$REPO/.env"
DATA_DIR="$REPO/data"
SOURCES_FILE="$DATA_DIR/sources.txt"
OVERRIDE_FILE="$REPO/docker-compose.override.yml"
# Netzwerkfreigaben als CIFS-Volumes -- schreibt der Server (ingest/nas.py).
# Hier nur in COMPOSE_FILE eintragen, wenn es die Datei gibt.
NAS_FILE="$DATA_DIR/nas-volumes.yml"

say()  { echo "  $*"; }
ok()   { echo "  * $*"; }
warn() { echo "  ! $*"; }
bad()  { echo "  x $*"; }

# --- Orte, an denen Fotos liegen koennen -----------------------------------

photo_roots() {
    # Nur, was es gibt. Ganz `/` einzubinden waere die Wahl zwischen /etc und /proc.
    local r
    case "$(uname -s)" in
        Darwin) for r in /Users /Volumes; do [ -d "$r" ] && echo "$r"; done ;;
        *)      for r in "${HOME}" /media /mnt /run/media; do [ -d "$r" ] && echo "$r"; done ;;
    esac
}

has_nvidia() { command -v nvidia-smi >/dev/null 2>&1; }

vram_mb() {
    has_nvidia || { echo 0; return; }
    local n
    n="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d '[:space:]')"
    case "$n" in ''|*[!0-9]*) echo 0 ;; *) echo "$n" ;; esac
}

# Ab diesem Treiber gibt es CUDA 13 -- die cuda-Variante des Images ist
# dagegen gebaut (torch cu130, onnxruntime-gpu). Aelter: CPU-Variante.
MIN_DRIVER=580
driver_major() {
    has_nvidia || { echo 0; return; }
    local d
    d="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1 | tr -d '[:space:]')"
    case "$d" in ''|*[!0-9]*) echo 0 ;; *) echo "$d" ;; esac
}

# Aktive Quellen aus data/sources.txt als Host-Pfade: /host/home/x/Bilder -> /home/x/Bilder.
# Ausschluesse (fuehrendes -) und stillgelegte Zeilen (#) zaehlen nicht.
chosen_paths() {
    local file="$1" line p
    [ -f "$file" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"
        line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        case "$line" in
            -*) continue ;;
            /host/*) p="${line#/host}"; p="${p%/}"; [ -n "$p" ] && echo "$p" ;;
        esac
    done < "$file"
}

# Override-Text: Wurzeln lesend, gewaehlte Ordner beschreibbar, GPU wenn gemessen.
#   emit_override <sources-datei> <gpu 0|1> <wurzel...>
# API_GPU=0 in der Umgebung: die Karte nur an Ollama, nicht an die API
# (Treiber zu alt fuer die cuda-Variante).
emit_override() {
    local sources="$1" gpu="$2"; shift 2
    local roots=("$@") r p inside
    local api_gpu="${API_GPU:-$gpu}"
    echo "# Von start.sh erzeugt -- bei jedem Start neu. Nicht von Hand aendern."
    echo "# Orte lesend unter /host/...; die gewaehlten Fotoordner aus data/sources.txt"
    echo "# zusaetzlich beschreibbar am selben Pfad."
    echo "services:"
    # Beide bekommen die Karte: Ollama fuer die Beschreibungen, die API fuer
    # Gesichter und CLIP (Image-Variante `cuda`, ~10x beim Einlesen).
    if [ "$gpu" = "1" ]; then
        echo "  ollama:"
        echo "    deploy:"
        echo "      resources:"
        echo "        reservations:"
        echo "          devices:"
        echo "            - driver: nvidia"
        echo "              count: all"
        echo "              capabilities: [gpu]"
    fi
    echo "  api:"
    if [ "$api_gpu" = "1" ]; then
        echo "    deploy:"
        echo "      resources:"
        echo "        reservations:"
        echo "          devices:"
        echo "            - driver: nvidia"
        echo "              count: all"
        echo "              capabilities: [gpu]"
    fi
    echo "    volumes:"
    for r in "${roots[@]}"; do
        echo "      - type: bind"
        echo "        source: \"$r\""
        echo "        target: /host$r"
        echo "        read_only: true"
    done
    while IFS= read -r p; do
        [ -z "$p" ] && continue
        inside=0
        for r in "${roots[@]}"; do
            case "$p" in "$r"/*) inside=1 ;; esac
        done
        # Ausserhalb der eingebundenen Orte, oder ein Ort selbst: nicht beschreibbar.
        [ "$inside" = "1" ] || continue
        [ -d "$p" ] || continue
        echo "      - type: bind"
        echo "        source: \"$p\""
        echo "        target: /host$p"
    done < <(chosen_paths "$sources")
}

write_override() {
    local gpu="$1" api_gpu="${2:-$1}" roots
    mapfile -t roots < <(photo_roots)
    API_GPU="$api_gpu" emit_override "$SOURCES_FILE" "$gpu" "${roots[@]}" > "$OVERRIDE_FILE"
}

# --- Testmodus --------------------------------------------------------------
if [ "${1:-}" = "--emit-override" ]; then
    # ./start.sh --emit-override <sources> <gpu 0|1> <wurzel...>
    shift
    emit_override "$@"
    exit 0
fi

# --- Lokale Installation? (Entwicklungsmaschine) ---------------------------
force_setup=0
case "${1:-}" in
    setup|--docker) force_setup=1; shift ;;
esac

is_local() {
    [ -x "${HOME}/.venvs/photovault/bin/python" ] && return 0
    local f
    for f in "${HOME}/.config/photovault/runtime" "/mnt/c/Users/${USER}/.config/photovault/runtime"; do
        [ -f "${f}" ] || continue
        grep -q '^RUNTIME=local' "${f}" && return 0
    done
    return 1
}

if [ "${force_setup}" -eq 0 ] && [ -f ./start-local.sh ] && is_local; then
    exec bash ./start-local.sh "$@"
fi

ACTION="${1:-start}"
compose() { docker compose "$@"; }

echo
echo "  PhotoVault"
echo "  =========="
echo

case "$ACTION" in
    stop)   compose down; exit $? ;;
    status) compose ps; exit $? ;;
    start|restart) ;;
    *) bad "Unbekannt: $ACTION (start, stop, status, restart, setup)"; exit 2 ;;
esac

# --- 1. Docker --------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    bad "Docker ist nicht installiert."
    say "macOS: Docker Desktop -- https://www.docker.com/products/docker-desktop/"
    say "Linux: https://docs.docker.com/engine/install/  (dazu: docker compose)"
    exit 1
fi
if ! docker version >/dev/null 2>&1; then
    bad "Docker laeuft nicht."
    say "macOS:  Docker Desktop starten und warten, bis das Symbol oben ruhig steht."
    say "Linux:  sudo systemctl start docker  -- und der Nutzer in der Gruppe docker."
    exit 1
fi

# --- 2. .env ----------------------------------------------------------------
port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
free_port() { local p="$1"; while ! port_free "$p"; do p=$((p + 1)); done; echo "$p"; }

if [ ! -f "$ENV_FILE" ]; then
    api="$(free_port 8000)"; qd="$(free_port 6333)"; ol="$(free_port 11434)"
    {
        echo "# Von start.sh angelegt. Ports frei gewaehlt; Fotoordner stehen in data/sources.txt."
        echo "API_PORT=$api"
        echo "QDRANT_PORT=$qd"
        echo "OLLAMA_PORT=$ol"
        echo "# Ollama aus dem Buendel. Eigenes Ollama? Zeile leeren (COMPOSE_PROFILES=) und"
        echo "# im Wizard die Adresse eintragen, z.B. http://host.docker.internal:11434."
        echo "COMPOSE_PROFILES=ollama"
        echo "# 0 = NVIDIA-Karte nicht benutzen, auch wenn eine da ist (nur Prozessor)."
        echo "PHOTOVAULT_GPU=1"
        echo "GPU_VRAM_MB=0"
    } > "$ENV_FILE"
    [ "$api" != 8000 ] || [ "$qd" != 6333 ] && warn "Port 8000 oder 6333 war belegt -- PhotoVault nimmt $api / $qd."
    [ "$ol" != 11434 ] && say "Port 11434 ist belegt -- laeuft hier schon ein Ollama? Das Buendel nimmt $ol; im Wizard laesst sich auch das eigene eintragen."
fi
set_env() {  # set_env KEY WERT -- Zeile ersetzen oder anhaengen
    if grep -q "^$1=" "$ENV_FILE"; then
        sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "$1=$2" >> "$ENV_FILE"
    fi
}
vram="$(vram_mb)"
set_env GPU_VRAM_MB "$vram"
# Die Karte benutzen, wenn eine da ist -- es sei denn, die .env sagt nein.
# Unter macOS reicht Docker Desktop keine GPU durch; dort bleibt es beim Prozessor.
docker_sees_gpu() {
    # Unter Linux braucht Docker dafuer das NVIDIA Container Toolkit; ohne
    # scheiterte sonst erst `compose up` mit "could not select device driver".
    local out
    out="$(docker run --rm --gpus all python:3.11-slim nvidia-smi -L 2>&1)" && case "$out" in *"GPU 0"*) return 0 ;; esac
    warn "Docker sieht die Grafikkarte nicht -- alles rechnet der Prozessor."
    say "  NVIDIA Container Toolkit installieren: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
    say "  dann: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    say "  Docker sagte: $(printf '%s' "$out" | tail -1)"
    return 1
}

gpu=0; api_gpu=0
driver="$(driver_major)"
if [ "$vram" -gt 0 ] && [ "$(uname -s)" = "Linux" ] && ! grep -q '^PHOTOVAULT_GPU=0' "$ENV_FILE"; then
    if docker_sees_gpu; then
        gpu=1
        [ "$driver" -ge "$MIN_DRIVER" ] && api_gpu=1
    else
        vram=0
    fi
fi
if [ "$api_gpu" = "1" ]; then
    set_env PHOTOVAULT_IMAGE_TAG cuda
    set_env TORCH_INDEX "https://download.pytorch.org/whl/cu130"
    set_env ORT_PACKAGE onnxruntime-gpu
    ok "Grafikkarte: $((vram / 1024)) GB Speicher, Treiber $driver -- Beschreibungen, Gesichter und CLIP rechnen darauf."
    say "Dafuer braucht Docker das NVIDIA Container Toolkit (nvidia-ctk)."
else
    set_env PHOTOVAULT_IMAGE_TAG latest
    set_env TORCH_INDEX "https://download.pytorch.org/whl/cpu"
    set_env ORT_PACKAGE onnxruntime
    if [ "$gpu" = "1" ]; then warn "Grafikkarte gefunden, aber Treiber $driver ist aelter als $MIN_DRIVER (CUDA 13) -- Gesichter und CLIP rechnet der Prozessor, Ollama nutzt die Karte. Treiber aktualisieren, dann neu starten."
    elif [ "$vram" -gt 0 ]; then say "Grafikkarte gefunden, aber nicht benutzt (PHOTOVAULT_GPU=0 oder macOS) -- alles rechnet der Prozessor."
    else say "Keine NVIDIA-Grafikkarte gefunden -- alles rechnet der Prozessor."; fi
    [ "$(uname -s)" = "Darwin" ] && say "macOS: ein natives Ollama (ollama.com) nutzt die GPU; im Wizard die Adresse http://host.docker.internal:11434 eintragen."
fi
port="$(grep '^API_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2)"; port="${port:-8000}"
url="http://localhost:$port"

set_compose_files() {
    local files="docker-compose.yml:docker-compose.override.yml"
    [ -f "$NAS_FILE" ] && files="$files:data/nas-volumes.yml"
    set_env COMPOSE_FILE "$files"
}

# --- 3. Orte und gewaehlte Ordner --------------------------------------------
mkdir -p "$DATA_DIR"
set_compose_files
write_override "$gpu" "$api_gpu"
ok "Lesend eingebunden: $(photo_roots | tr '\n' ' ')"
chosen="$(chosen_paths "$SOURCES_FILE" | tr '\n' ' ')"
[ -n "$chosen" ] && ok "Beschreibbar: $chosen"

# --- 4. Hochfahren ----------------------------------------------------------
[ "$ACTION" = "restart" ] && compose down
say "Hole das fertige Image (falls vorhanden) ..."
compose pull api >/dev/null 2>&1 || say "Kein fertiges Image erreichbar -- baue selbst. Beim ersten Mal einige Minuten."
say "Starte ..."
compose up -d || { bad "Start fehlgeschlagen. Die Meldung oben sagt meist, warum."; exit 1; }

wait_api() {
    local seconds="$1" note="$2" t0 shown=0
    t0=$(date +%s)
    while [ $(( $(date +%s) - t0 )) -lt "$seconds" ]; do
        if curl -sf -m 3 -o /dev/null "$url/api/health"; then echo; return 0; fi
        if [ "$shown" = 0 ] && [ -n "$note" ] && [ $(( $(date +%s) - t0 )) -gt 25 ]; then echo; say "$note"; shown=1; fi
        printf "."
        sleep 2
    done
    echo
    return 1
}
setup_step() {
    curl -sf -m 5 "$url/api/setup/state" 2>/dev/null | grep -o '"step":"[^"]*"' | head -1 | cut -d'"' -f4
}

printf "  Warte, bis PhotoVault bereit ist "
wait_api 900 "Beim ersten Mal laedt PhotoVault rund 1,5 GB Modelle -- das dauert." \
    || { bad "PhotoVault antwortet nicht auf $url. Protokoll: docker compose logs api"; exit 1; }
ok "Laeuft: $url"
command -v xdg-open >/dev/null && xdg-open "$url" >/dev/null 2>&1
command -v open >/dev/null && open "$url" >/dev/null 2>&1

# --- 5. Zweite Phase: nach der Ordnerwahl beschreibbar einbinden -----------
step="$(setup_step)"
case "$step" in
    sources-done|done) say "Eingerichtet. Dieses Fenster kann zu."; exit 0 ;;
esac
echo
say "Im Browser geht es weiter: Ordner waehlen. Dieses Fenster wartet darauf"
say "und bindet die gewaehlten Ordner dann beschreibbar ein -- bitte offen lassen."
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 7200 ]; do
    sleep 3
    step="$(setup_step)"
    case "$step" in
        sources-done|done) break ;;
        nas-added)
            # Freigabe eingetragen: Volume-Datei des Servers aufnehmen, Container neu.
            ok "Freigabe einbinden ..."
            set_compose_files
            compose up -d
            printf "  Warte, bis PhotoVault wieder da ist "
            wait_api 300 "" || { bad "PhotoVault kommt nicht hoch. Protokoll: docker compose logs api"; exit 1; }
            t1=$(date +%s)
            while [ "$(setup_step)" = "nas-added" ] && [ $(( $(date +%s) - t1 )) -lt 300 ]; do sleep 2; done
            ;;
    esac
done
case "$step" in
    sources-done|done) ;;
    *) warn "Zwei Stunden ohne Ordnerwahl -- beim naechsten Start werden gewaehlte Ordner beschreibbar."; exit 0 ;;
esac
write_override "$gpu" "$api_gpu"
set_compose_files
chosen="$(chosen_paths "$SOURCES_FILE" | tr '\n' ' ')"
if [ -n "$chosen" ]; then ok "Beschreibbar einbinden: $chosen"
elif [ -f "$NAS_FILE" ]; then ok "Beschreibbar einbinden: gewaehlte Ordner auf Freigaben (data/nas-volumes.yml)"
else
    warn "Keine Ordner in data/sources.txt gefunden, die unter /host liegen -- nichts einzubinden."
    exit 0
fi
compose up -d
printf "  Warte, bis PhotoVault wieder da ist "
wait_api 300 "" || { bad "PhotoVault kommt nach dem Neustart nicht hoch. Protokoll: docker compose logs api"; exit 1; }
ok "Fertig -- weiter im Browser. Dieses Fenster kann zu."
exit 0
