# Setup-Wizard: vom ZIP bis zum ersten benannten Gesicht, ohne Konsole

Stand 18.09.2026, Issue [#17](https://github.com/crack666/photovault/issues/17). Anlass: PhotoVault wurde für eine Maschine gebaut, auf der
alles schon da ist — Docker Desktop, WSL, NVIDIA-Treiber, Ollama mit gezogenen
Modellen, LiteLLM. Ein Freund mit einer 5060 Ti (16 GB) will es bei sich
nutzen. Den Weg vom ZIP bis zur laufenden Oberfläche hat noch nie jemand ohne
dieses Vorwissen beschritten, und einiges daran kann nur hier funktionieren.

Abnahme, bevor irgendetwas gebaut wird: **auf einer fremden Windows-11-Maschine
(Ryzen-APU, keine NVIDIA) vom ZIP bis zum ersten benannten Gesicht, ohne dass
nach dem Doppelklick auf `start.bat` noch einmal eine Konsole angefasst wird.**
Jede Stelle, an der man eingreifen *wollte*, ist ein Fehler.

## Was existiert (gemessen 18.09.2026 am `master`)

- `start.bat` / `start.sh`: prüfen, ob Docker läuft; fragen den Fotoordner ab;
  schreiben `.env` und `sources.txt`; `docker compose up -d --build`; warten
  60 × 2 s auf `:8000`; bieten Trockenlauf und Einlesen an — beides als
  Terminal-Dialog, und der Hinweis für „später" ist ein
  `docker compose exec …`-Befehl.
- Jobs-Seite: startet und bricht ab `ingest`, `caption`, `faces`, `reembed`,
  `thumbs`, `atlas`; liest `sources.txt`.
- `api/capabilities.py`: prüft zur Laufzeit, ob Ollama erreichbar ist.
- Captioner und Text-Embedder haben zwei Wege: `LITELLM_URL` gesetzt →
  OpenAI-Format (`/v1/chat/completions` mit base64-Bild, `/v1/embeddings`);
  sonst Ollama direkt (`/api/chat`, `/api/embed`). Der erste Weg *ist* schon
  ein OpenAI-kompatibler Client — die Cloud-Option ist damit fast umsonst.
- Modellnamen sind Env-Variablen, beim Import gelesen: `local` und `embedder`
  — LiteLLM-Aliasse aus dem ai-stack dieser Maschine. Ein Fremder hat keines
  von beiden. Dahinter: `qwen3.8:27b` (17 GB) und `qwen3-embedding:4b`
  (2,5 GB, 2560 Dimensionen = `TEXT_VECTOR_SIZE`, beim Anlegen der Collection
  fix).
- Compose: Qdrant + API, Ollama auf dem Host über `host.docker.internal`,
  keine GPU im Container, `build: .` (kein vorgebautes Image, keine CI).
- `.env` und `sources.txt` sind nicht getrackt: das ZIP trägt keine fremden
  Pfade mit.

## Entscheidungen

### E1 — Ordner wählen: keine Pfade tippen, kein Dialog, im Browser

Der Container sieht nur, was gemountet ist, und Mounts stehen fest, bevor er
läuft. Drei Wege:

| | Wie | Warum nicht / warum doch |
|---|---|---|
| Pfad tippen | heute | unzumutbar, und `D:\Fotos` gegen `D:/Fotos` gegen Umlaute |
| Ordnerdialog aus `start.bat` (PowerShell `FolderBrowserDialog`) | vor dem Start | funktioniert, aber je Ordner ein Dialog, und jeder spätere Ordner heißt: Skript neu, Container neu |
| **Feste Laufwerke read-only mounten, Baum im Browser, gewählte Ordner rw** | `start.bat` erzeugt `docker-compose.override.yml` mit allen festen Laufwerken (`C:/` → `/host/c`, `D:/` → `/host/d`, …, `read_only: true`, lange Syntax wegen der Doppelpunkte); der Wizard zeigt den Baum lazy, der Nutzer hakt Ordner an; die gewählten Ordner werden **am selben Pfad** noch einmal beschreibbar eingehängt | **gewählt.** Kein Tippen, kein Dialog, Schreibrechte genau dort, wo die Fotos liegen |

Warum nicht einfach alles beschreibbar: Papierkorb (`unlink`), Verschieben
(`/relocate`), `exif_repair` und die Zeitkorrektur schreiben in den Fotobaum —
mit read-only ist das Archiv nur ein Index, und genau die Kuratierung ist das
Produkt. Aber „alles rw" hieße: ein Fehler im Löschpfad trifft `C:\`. Der
Code-Zaun (`photo_root()` = gemeinsames Elternverzeichnis der Quellen) hilft
bei mehreren Laufwerken nicht, er fällt dann auf `/host` zurück. Die Grenze
muss der Kernel ziehen, nicht der Code: **rw nur die gewählten Ordner.**

Wie das ohne Konsole geht — `start.bat` in zwei Phasen, der Nutzer merkt es
nicht:

1. Laufwerke ro, Verbund hoch, Browser auf `/setup`. Das Konsolenfenster
   bleibt offen: „Warte auf deine Ordnerwahl im Browser …" und fragt die API
   alle paar Sekunden.
2. Sobald `sources.txt` geschrieben ist: Override neu erzeugen — je gewählter
   Quelle ein zweiter Mount, `D:/Fotos` → `/host/d/Fotos`, **rw, derselbe
   Pfad** (Docker ordnet verschachtelte Mounts nach Zieltiefe, der tiefere
   deckt den flacheren ab) — und `docker compose up -d`; der Container wird
   in ~10 s neu erstellt, der Wizard wartet und macht mit dem Einlesen weiter.

Konsequenzen, ausgesprochen:

- **Ein** Pfadraum: `/host/<laufwerk>/…` überall — im Baum, in `sources.txt`,
  im Index. Kein `/photos` mehr, kein Umrechnen.
- Ordner später ergänzen: sofort einlesbar (dafür reicht ro), beschreibbar
  nach dem nächsten `start.bat` — der Wizard sagt das dazu. Die Override-Datei
  ist eine reine Funktion aus Laufwerken und `sources.txt`, bei jedem Start
  neu erzeugt, nicht getrackt.
- Der Container sieht die übrigen Platten **lesend**. Es bleibt lokal, nichts
  verlässt den Rechner. Wer auch das nicht will, hat den Dialog als
  dokumentierten Rückfall (`start.bat setup --dialog`), v2.
- Der Baum-Endpunkt schluckt Berechtigungsfehler (Systemordner, Junctions)
  statt zu sterben.
- **Zu messen, nicht zu glauben** (Testliste 3a): verschachtelte Bind-Mounts
  ro/rw am selben Pfad unter Docker Desktop mit WSL2-Backend — auf einer
  Linux-Engine ist das dokumentiertes Verhalten, auf dem 9p/grpc-fuse-Share
  von Docker Desktop muss es einer gesehen haben.
- Netzlaufwerke und UNC sind **nicht** v1. Docker Desktop mountet gemappte
  Laufwerke nicht zuverlässig; die Zeile „Netzlaufwerk geht, wenn es im
  Explorer sichtbar ist" fliegt aus `start.bat`. Wer Fotos auf der NAS hat,
  bekommt den Satz: lokal kopieren.
- macOS: `/Users`, `/Volumes`; Linux: `$HOME`, `/media`, `/mnt`. Gleicher
  Wizard.
- `PHOTO_DIR` in der Compose wird optional (heute Pflicht mit `:?`).

### E2 — Ollama auf dem Host, optional, geführt

Unter Windows ist Ollama ein Installer, GPU inklusive, ohne Docker-Konfiguration;
die Compose zeigt schon auf `host.docker.internal:11434`. Der Wizard erkennt
es, und wenn es fehlt: Link, „später", alles andere läuft. Ollama *im*
Verbund (CPU-Profil für Leute, die nichts installieren wollen) ist v2.

### E3 — Modellwahl aus gemessenem VRAM, nicht aus dem Kartennamen

`start.bat` misst auf dem Host (`nvidia-smi --query-gpu=memory.total`) und
schreibt `GPU_VRAM_MB` in `.env`; kein `nvidia-smi` → 0. Der Wizard schlägt
danach vor — die 5060 Ti gibt es mit 8 und mit 16 GB:

Gemessen am 19.09.2026 gegen die Ollama-Bibliothek: `qwen3.8` gibt es **nur
als 27B** (18 GB) — für 16 GB muss eine andere Familie her, und `gemma4` hat
die Leiter mit Bild-Eingang. Tabelle in `api/routes/setup.py`
(`RECOMMENDATIONS`), ein Test prüft, dass jede Stufe in ihr Band passt:

| VRAM | Caption-Modell | Embedder | Bemerkung |
|---|---|---|---|
| ≥ 24 GB | `qwen3.8:27b` (18 GB) | `qwen3-embedding:4b` (2,5 GB) | derselbe Stand wie hier |
| 12–23 GB | `gemma4:12b` (7,6 GB) | `qwen3-embedding:4b` | 18 GB passen nicht in 16 |
| 8–11 GB | `gemma4:e4b-it-qat` (6,1 GB) | `qwen3-embedding:0.6b` (0,6 GB) | beide müssen nebeneinander passen |
| < 8 GB, keine GPU | `gemma4:e2b-it-qat` (4,3 GB), CPU | `qwen3-embedding:0.6b` | Test-Caption stoppen, hochrechnen, ehrlich sagen — oder Cloud (E4) |

Der Embedder ist nicht frei wählbar, nur die zwei Größen aus der Tabelle.
Die Textvektor-Größe der Collection hängt daran: der Wizard **misst** sie per
Probe-Aufruf (`POST /api/setup/llm/embed`, 4b → 2560, 0.6b → 1024) und legt
die Collection damit an; gibt es sie schon mit einer anderen, wird nichts
überschrieben, sondern gesagt.

Der Wizard zieht das gewählte Modell über die API (`/api/pull` als Stream,
Fortschritt im Browser — der Browser spricht nicht selbst mit Ollama, damit
es auch von einem anderen Gerät aus geht), macht **eine Test-Caption an einem
Foto des Nutzers mit Stoppuhr** und rechnet hoch: „38 s je Bild → 4.800 Fotos
≈ 2 Tage. Nachts laufen lassen, ohne Beschreibungen weiter, oder Cloud?"
Dieser Satz ist auf der Ryzen-Maschine die Abnahme für Ehrlichkeit.

### E4 — Cloud als Alternative, mit Warnung

Modus `openai`: Basis-URL, Schlüssel, Modellnamen — der vorhandene
LiteLLM-Pfad ist OpenAI-Format, also fast keine neue Logik. Der Wizard sagt
vor dem ersten Aufruf in einem Satz, was passiert: **Fotos verlassen den
Rechner, verkleinert, an diesen Anbieter.** Embedding-Dimension wird per
Probe-Aufruf gemessen und mit der Collection gespeichert; ein Wechsel später
heißt `reembed` (Job existiert). Der Schlüssel liegt in `data/settings.json`
im Volume, nie im Repo, nie im Log.

### E5 — Einstellungen zur Laufzeit statt Env beim Import

`data/settings.json`, gelesen von Captioner, Embedder, Capabilities und Jobs.
Vorrang: **Env-Variable > settings.json > Default.** Damit bleibt diese
Maschine (Env, LiteLLM-Aliasse) unberührt: ohne Datei ändert sich nichts.

```json
{
  "setup": {"done": false, "step": "sources"},
  "llm": {"mode": "ollama|openai|off", "url": "", "key": "",
          "caption_model": "", "embed_model": "qwen3-embedding:4b", "embed_dim": 2560}
}
```

### E6 — Fertiges Image statt Bau auf fremder Maschine

GitHub Action → `ghcr.io/crack666/photovault:<tag>`; Compose auf `image:`,
`build:` bleibt für Entwickler. Der Bau auf einer fremden Maschine (Torch-Wheel
~2 GB, Gewichte beim ersten Start) ist der fragilste Schritt der Kette.

## Was gebaut wird, in Reihenfolge

**C — Backend (zuerst, alles andere baut darauf)** — *gebaut 19.09.2026*
- `ingest/settings.py`: laden, speichern, Vorrang wie E5; Schlüssel nie loggen.
- Captioner, Embedder, Capabilities, Jobs und die Pipeline-Vorwärmung lesen
  Modell und Modus zur Laufzeit (`caption_model()`, `embed_model()`,
  `text_vector_size()`), nicht beim Import.
- Ordnerbaum, Haken und Trockenlauf gab es schon (`/api/sources/browse`,
  `/add`, `/toggle`, `/preview`); neu ist nur `PHOTOVAULT_BROWSE_ROOT`
  (`/host` im Verbund) als Startpunkt und Grenze vor der ersten Quelle.
- `api/routes/setup.py`: `GET state`, `POST llm`, `GET llm/models`,
  `POST llm/pull` (NDJSON-Stream), `POST llm/test` (eine Caption mit Dauer
  und Hochrechnung), `POST llm/embed` (Dimension messen und merken),
  `POST step`, `POST done`.
- Erstlauf: `setup.done == false` und keine aktive Quelle → `/` leitet auf
  `/setup`, sobald `web/setup.html` existiert (B). Eine Installation mit
  Quellen kommt dort nie vorbei.

**B — Browser-Wizard `/setup`** — Quellen (Baum, Ausschlüsse, Zahlen aus dem
Trockenlauf) → Einlesen als Job mit Fortschritt (vorhanden) → LLM (E2–E4) →
Fertig-Seite: was geht, was fehlt, wo man klickt.

**A — `start.bat` / `start.sh`** — Docker-Prüfung mit Klartext (Virtualisierung
im BIOS, WSL2-Update), Laufwerke → Override-Datei, zweite Phase nach der
Ordnerwahl (rw-Mounts, Neustart), `GPU_VRAM_MB`, freie Ports wählen, `pull`
statt `build`, Wartezeit gegen das Laden der Gewichte messen, Browser auf
`/setup`, kein Einlesen mehr im Terminal.

**D — Image und Doku** — Action, Compose, README „Der einfache Weg" nach dem
Test neu, Windows-Klemmliste.

**v2, nicht jetzt:** GPU-Overlay für den Container (CLIP/Gesichter), Ollama-
Profil im Verbund, Netzlaufwerke, Ordnerdialog als Rückfall.

## Testliste Ryzen-Maschine (Windows 11, APU, keine NVIDIA)

Genau so, wie der Freund es täte: ZIP von `master` (nicht `git clone`),
Docker Desktop frisch, ein *kopierter* Ordner mit einigen hundert Fotos.
Nicht eingreifen, mitschreiben.

1. Docker Desktop installieren → Neustart → Wal ruhig. *Messen:* Meldung bei
   Virtualisierung aus? WSL2-Nachfrage?
2. ZIP entpacken, `start.bat`. *Messen:* Zeit bis `:8000`; kommt der falsche
   Alarm nach 2 Minuten, weil Gewichte laden?
3. Browser öffnet `/setup`. Ordner anhaken, Ausschlüsse, Trockenlauf-Zahlen
   stimmen mit dem Explorer überein?
   3a. Nach der Ordnerwahl: Konsole meldet den Neustart, Wizard läuft weiter.
   *Messen:* ist `/host/d/Fotos` danach beschreibbar (Papierkorb an einem
   Testfoto), der Rest von `D:/` weiterhin nicht? Dauer des Neustarts.
4. Einlesen: Fortschritt sichtbar, Abbruch und Fortsetzen funktionieren.
   *Messen:* Fotos/s auf dieser CPU (README nennt 1,6–2,6).
5. Ollama fehlt → Wizard erklärt, „später" führt zu einer benutzbaren
   Oberfläche. Dann Ollama installieren, Wizard erneut: erkennt es, schlägt
   CPU-Modell vor, zieht es mit Fortschritt, Test-Caption mit Dauer und
   Hochrechnung. *Messen:* Sekunden je Caption auf CPU.
6. „Wer ist das?" — erstes Gesicht benennen. Fertig.
7. Rechner neu starten, `start.bat` erneut: läuft ohne Fragen, Index da.
