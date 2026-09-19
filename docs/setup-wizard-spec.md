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

### E2 — Ollama im Bündel, eigenes erlaubt

*Geändert 19.09.2026 auf Wunsch des Nutzers* (vorher: nur auf dem Host).
Die Compose bringt Ollama mit (Profil `ollama`, von `start.bat` in der `.env`
eingeschaltet; mit gemessener NVIDIA-Karte schreibt das Skript die
GPU-Reservierung in die Override, sonst rechnet der Prozessor). Die Compose
setzt nur eine *Vorgabe* (`PHOTOVAULT_OLLAMA_DEFAULT=http://ollama:11434`);
der Wizard zeigt die Adresse vorbelegt, und wer ein eigenes Ollama hat,
trägt es ein und verbindet — die Modelle dort erscheinen in den Listen. Ein
eigenes muss auf allen Adressen lauschen (`OLLAMA_HOST=0.0.0.0`), sonst
erreicht der Container es nicht; die Seite sagt das. Unter macOS reicht
Docker Desktop keine GPU durch — dort ist das native Ollama die bessere
Wahl, das Skript sagt es. Wer es ganz weglassen will: `COMPOSE_PROFILES=`
in der `.env`.

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

**B — Browser-Wizard `/setup`** — *gebaut 19.09.2026* (`web/setup.html`,
`web/static/setup.css`; eigenständig, hängt nicht an `app.js`). Quellen
(Baum ab `PHOTOVAULT_BROWSE_ROOT`, Ausschlüsse, Zählen) → nach „Weiter"
`POST /api/setup/step sources-done`; im Verbund (`PHOTOVAULT_TWO_PHASE=1`)
wartet die Seite auf den Neustart durch `start.bat` und erkennt ihn an
`started_at` → Einlesen als Job mit Fortschritt, Abbruch, Zusammenfassung →
Beschreibungen: Ollama (Erkennung, Empfehlung nach VRAM, Auswahl aus
vorhandenen Modellen, Pull mit Balken, Probe: Dimension, Test-Caption mit
Stoppuhr und Hochrechnung, dann „jetzt im Hintergrund" / „später"),
Anbieter (Formular mit Warnung, Schlüssel wird nie zurückgegeben) oder
„später" → Fertig-Seite, `POST /api/setup/done`.

Im Browser durchgespielt gegen ein eigenes Qdrant und das echte Ollama
(isolierte Instanz auf :8123, `/tmp/pvw` als `/host`): Quellen auf zwei
„Laufwerken" plus Ausschluss, 146 Fotos eingelesen, Neustart erkannt,
Probe 2 560 Dimensionen in 3,7 s, Test-Caption 15 s mit `qwen3.8:27b`,
Ollama-nicht-da-Karte und Anbieter-Formular. **Nicht** im Browser geprüft:
ein echter Pull (nur das Stream-Parsing serverseitig getestet) — Messpunkt
für die Ryzen-Maschine.

Zwei Fehler, die erst der Browserlauf zeigte, mit Tests festgehalten:
`browse`/`preview` starben ohne `sources.txt` (500) — die erste Quelle legt
die Datei jetzt an; und nach der ersten Quelle auf `D:` verweigerte die
Bibliothekswurzel jede zweite auf `C:` — im Verbund ist die Grenze des
Wählers jetzt `PHOTOVAULT_BROWSE_ROOT`, die Wurzel gilt weiter fürs Löschen.
Dazu: `ingest/spaces.py` las die Quellen fest aus `<repo>/sources.txt` statt
aus `PHOTOVAULT_SOURCES` — zwei Wahrheiten, jetzt eine.

Bekannte Grenze (v1): mit Quellen auf zwei Laufwerken ist die gemeinsame
Wurzel `/host`, und die **Bereiche** (erste Ordnerebene darunter) heißen dann
`c` und `d`. Für eine Sammlung auf einem Laufwerk ändert sich nichts.

**A — `start.bat` / `start.sh`** — *gebaut 19.09.2026.* `start.bat` ist ein
Fünfzeiler, die Arbeit steckt in `start.ps1` (Windows PowerShell, auf jedem
Windows da); `start.sh` tut dasselbe für macOS/Linux. Docker-Prüfung mit
Klartext (Virtualisierung im BIOS per `Win32_Processor.VirtualizationFirmwareEnabled`
gemessen, WSL-Hinweis), freie Ports, `.env` mit `COMPOSE_PROFILES=ollama` und
gemessenem `GPU_VRAM_MB`, Override mit den festen Laufwerken
(`Win32_LogicalDisk DriveType=3`; macOS `/Users`, `/Volumes`; Linux `$HOME`,
`/media`, `/mnt`, `/run/media`) lesend und den aktiven Quellen aus
`data/sources.txt` beschreibbar (nur existierende, nur unterhalb der Orte,
nie ein Ort selbst — Tests in `tests/test_start_scripts.py` führen beide
Skripte wirklich aus), GPU-Reservierung für Ollama nur mit gemessener Karte,
`compose pull` vor `up` (Bau als Rückfall), Wartezeit bis 15 Minuten mit
Hinweis auf die Modell-Downloads, Browser auf `/`, zweite Phase: warten auf
`setup.step == sources-done`, Override neu, `up -d`, warten, fertig. `./data`
ist jetzt ein Bind-Mount (`PHOTOVAULT_SOURCES`, `PHOTOVAULT_SETTINGS`
darunter), damit Quellen und Einstellungen den Neubau des Containers
überleben und das Skript sie lesen kann. Dazu `.dockerignore` — vorher
wanderten `.env`, `data/` und `logs/` ins Image.

**D — Image und Doku** — *Image gebaut 19.09.2026.* Gemessen in einem
frischen `python:3.11-slim`: `insightface` kommt als Quellpaket und
kompiliert eine Cython-Erweiterung — ohne `g++` bricht der Bau ab. Das alte
Dockerfile hätte auf jeder fremden Maschine an dieser Stelle versagt; dazu
zog `pip install torch` die CUDA-Fassung (~3 GB) für einen Container ohne
GPU, und `ffmpeg`/`onnxruntime` fehlten. Jetzt zweistufig: Baustufe mit
Compiler, Image ohne; torch **und** torchvision aus dem CPU-Index (getrennt
installiert passten sie nicht zueinander — „operator torchvision::nms does
not exist"), `onnxruntime`, `ffmpeg`, `[atlas]`, Healthcheck; das Projekt
wird nicht als Paket installiert (setuptools verweigert das Flat-Layout mit
`api`, `ingest`, `web`), es läuft aus `/app`. Rauchtest im fertigen Image:
alle Importe, torch 2.14+cpu, onnxruntime 1.30, ffmpeg 7.1; 3,4 GB
entpackt, davon 773 MB torch. `.github/workflows/image.yml` baut amd64 und
arm64 nativ, macht denselben Rauchtest vor dem Push und schiebt nach
`ghcr.io/crack666/photovault:latest`. Einmalig vom Besitzer: das Paket auf
GHCR öffentlich stellen. Bis dahin baut `start.bat` lokal — mit demselben
Dockerfile, gemessen funktionsfähig. README „Der einfache Weg" ist auf den
neuen Ablauf umgeschrieben; Feinschliff und Klemmliste nach dem Test.

**v2, nicht jetzt:** Ordnerdialog als Rückfall. *(GPU für den Container,
Ollama im Verbund und Netzwerkfreigaben waren als v2 geplant und sind am
19.09.2026 in v1 gewandert — siehe E2, E7 und E8.)*

### E8 — Netzwerkfreigaben als CIFS-Volumes

*Nachgezogen 19.09.2026, Anlass: der erste Fremde.* Er hatte den alten
`master` und die alte README; die alte `start.bat` versprach „ein
Netzlaufwerk geht auch, wenn es im Explorer sichtbar ist" — und seine Fotos
liegen auf einem NAS. Es ging nicht: Docker Desktop reicht gemappte
Netzlaufwerke nicht als Bind-Mount durch. Was geht, **gemessen** gegen einen
Samba-Container unter Docker Desktop (WSL2): ein Volume vom Typ `cifs` — die
Docker-VM mountet die Freigabe selbst, ohne Laufwerksbuchstaben; die Freigabe
lesend unter `/host/nas/<name>`, gewählte Unterordner am selben Pfad noch
einmal beschreibbar (CIFS mountet Unterpfade; `touch` im Unterordner klappt,
darüber „Read-only file system").

Die Definitionen schreibt **der Server** (`ingest/nas.py` →
`data/nas-volumes.yml`, 0600): er kennt Zugangsdaten (`settings.json`) und
gewählte Ordner (`sources.txt`) und hat Python — die Skripte müssten sonst
JSON in Batch parsen. Sie tragen die Datei nur in `COMPOSE_FILE` ein
(Compose führt beliebig viele Dateien zusammen) und reagieren auf den
Wizard-Schritt `nas-added` mit einem `up -d`; die Seite wartet auf den
Neustart wie bei der Ordnerwahl und springt dann in den Baum der Freigabe.
Volume-Namen hängen am Inhalt (Hash aus Adresse und Optionen): geänderte
Zugangsdaten sind ein neues Volume, sonst behielte Docker das alte Passwort.
Das Passwort steht in der Datei im Klartext — anders kennt der Kernel-Mount
es nicht; die Seite sagt das nicht extra, die Datei selbst tut es.

Im Browser geprüft (isolierte Instanz): Freigabe eintragen, Liste zeigt sie
mit Status, Passwort nirgends im Zustand, Datei geschrieben. **Nicht**
geprüft: der Neustart-Ablauf mit echtem NAS — das ist der Messpunkt beim
Freund. macOS/Linux: eine gemountete Freigabe (`/Volumes`, `/mnt`) geht wie
ein Ordner; CIFS-Volumes funktionieren dort ebenso, sind aber ungemessen.

### E7 — Die Karte auch für PhotoVault selbst, als Wahl

*Nachgezogen 19.09.2026.* Ein Nutzer mit starker Karte soll sie nicht nur
für Ollama nutzen dürfen: Gesichter und CLIP sind auf der GPU ~10× schneller
(README: 26 gegen 1,6–2,6 Fotos/s). Deshalb zwei Varianten desselben Images
(`Dockerfile`, Build-Argumente): `latest` = CPU-torch, `cuda` = torch cu130 +
`onnxruntime-gpu`. `start.bat`/`start.sh` wählen `cuda`, wenn sie eine
NVIDIA-Karte **und** einen Treiber ab 580 messen (CUDA 13; älter → CPU-Image,
Ollama bekommt die Karte trotzdem, das Skript sagt es), schreiben
`PHOTOVAULT_IMAGE_TAG`, `TORCH_INDEX`, `ORT_PACKAGE` in die `.env` und die
GPU-Reservierung für `ollama` **und** `api` in die Override. Abschalten:
`PHOTOVAULT_GPU=0`. macOS: keine GPU im Container, natives Ollama empfohlen.

Gemessen im cuda-Image auf der RTX 5090 (Blackwell, sm 12.0, gleiche
Generation wie die 5060 Ti): torch 2.14.0+cu130 rechnet auf der Karte,
onnxruntime 1.30 mit `CUDAExecutionProvider`; **Gesichter 42 ms, CLIP 32 ms
je Foto** im Container. Drei Fehler dabei gefunden, alle mit Test oder
Dockerfile-Kommentar festgehalten: cu128-torch gegen PyPI-onnxruntime-gpu
(„Require cuDNN 9.* and CUDA 13.*" → beide auf CUDA 13); eine Abhängigkeit
zog das CPU-onnxruntime mit und verdrängte den CUDA-Provider (jetzt zuletzt
und allein installiert); und `ingest/face_embedder.py` stürzte an
`nvidia.__file__ is None` — die CUDA-13-Pakete sind ein Namensraum, die
Bibliotheken liegen unter `nvidia/cu13/lib` (Vorladen über `__path__`,
beide Layouts, zwei Durchgänge).

## Testliste Ryzen-Maschine (Windows 11, APU, keine NVIDIA)

Genau so, wie der Freund es täte: ZIP des Branches (bis zum Merge
`archive/refs/heads/setup-wizard.zip`, danach `master`), nicht `git clone`;
Docker Desktop frisch; ein *kopierter* Ordner mit einigen hundert Fotos.
Nicht eingreifen, mitschreiben — jede Stelle, an der man eingreifen wollte,
ist ein Fehler. Solange das Paket auf GHCR nicht öffentlich ist, sagt
`start.bat` „kein fertiges Image erreichbar — baue selbst" und baut lokal
(gemessen 19.09.2026 auf der Entwicklungsmaschine: das geht, siehe D).

1. Docker Desktop installieren → Neustart → Wal ruhig. *Messen:* Was sagt
   `start.bat`, wenn Docker noch nicht läuft? Bei Virtualisierung aus im BIOS
   (einmal absichtlich ausschalten, falls das Board es zulässt): kommt der
   gemessene Satz „Virtualisierung ist im BIOS AUS"?
2. ZIP entpacken, `start.bat`. *Messen:* Zeit bis „Läuft"; Zeit des lokalen
   Baus; kommt der Hinweis auf die Modell-Downloads; öffnet der Browser
   `/setup`; steht in der `.env` `GPU_VRAM_MB=0` und `COMPOSE_PROFILES=ollama`;
   listet die Override alle festen Laufwerke lesend?
3. Browser: Ordner anhaken (auch einen auf einem zweiten Laufwerk, falls es
   eines gibt), einen Unterordner ausschließen, „Zählen". *Messen:* Stimmen
   die Zahlen mit dem Explorer überein? Dauer des Zählens.
   3a. „Weiter": das Konsolenfenster meldet „Beschreibbar einbinden", der
   Container wird neu erstellt, die Seite geht von allein zu Schritt 2.
   *Messen:* Dauer; danach ist `/host/d/…/<Quelle>` beschreibbar (Papierkorb
   an einem Testfoto leeren, Datei ist weg), der Rest von `D:/` nicht
   (Jobs-Seite → Ordner außerhalb der Quelle als Quelle hinzufügen und dort
   ein Foto löschen → muss scheitern). **Das ist der Messpunkt für
   verschachtelte ro/rw-Mounts unter Docker Desktop.**
4. Einlesen: Fortschritt sichtbar, Abbruch und Fortsetzen funktionieren.
   *Messen:* Fotos/s auf dieser CPU (README nennt 1,6–2,6); wie lange die
   Modell-Downloads beim ersten Mal dauern.
5. Beschreibungen: Ollama-Karte zeigt die vorbelegte Adresse
   (`http://ollama:11434`), „Verbinden" findet das mitgelieferte Ollama;
   Empfehlung ist das CPU-Modell (`gemma4:e2b-it-qat`, Embedder 0.6b); Pull
   zeigt Balken; Probe nennt die Dimension (1 024); Test-Caption mit Dauer
   und Hochrechnung. *Messen:* Sekunden je Caption auf CPU, Pull-Dauer, ist
   der Hochrechnungs-Satz ehrlich? Danach „Später" oder „jetzt starten".
6. „Wer ist das?" — erstes Gesicht benennen. Fertig.
7. Rechner neu starten, `start.bat` erneut: läuft ohne Fragen, Index da,
   Fenster sagt „Eingerichtet" und geht zu.
8. Optional: natives Ollama installieren, im Wizard „Eigenes Ollama auf
   diesem Rechner" → *Messen:* erreicht der Container es ohne
   `OLLAMA_HOST=0.0.0.0`? (Erwartung: nein; die Seite sagt es.)
9. **NAS** (beim Freund): Freigabe eintragen (`\\nas\fotos`, Benutzer,
   Passwort) → Konsole meldet „Freigabe einbinden", Neustart, Baum zeigt
   `nas/<name>` mit den Ordnern. Ordner darunter als Quelle, „Weiter" →
   zweiter Neustart, Papierkorb an einem Testfoto auf dem NAS. *Messen:*
   Dauer, Fehlertext bei falschem Passwort (`docker compose logs api`),
   Fotos/s über das Netz.
