# PhotoVault

**Finde die Fotos, die zählen — in einem Archiv, das über zwanzig Jahre
gewachsen ist. Alles auf dem eigenen Rechner.**

Kein Cloud-Dienst, kein Upload, keine Gesichtserkennung bei einem Anbieter,
dem du deine Familie anvertrauen müsstest.

---

## Das Problem

Die Fotos liegen längst irgendwo. Auf der NAS, in Handy-Backups, in
WhatsApp-Ordnern, in Verzeichnissen mit Namen wie `Neuer Ordner (3)`. In
diesem Archiv waren es **43 000 Bilder** — und darin verstreut:

- Alben von echten Anlässen, gut benannt
- Handy-Dumps mit Kamera, Screenshots, Memes und Weiterleitungen durcheinander
- ein Thumbnail-Cache mit 5 119 Miniaturbildern, die aussehen wie Fotos
- Kameras, deren Uhr beim Batteriewechsel auf 2005 zurücksprang
- WhatsApp-Bilder ganz ohne EXIF, deren Datum nur im Dateinamen steht

„Zeig mir die Fotos von Papas 60. Geburtstag" ist damit von Hand nicht zu
beantworten. Und Google Fotos kann es, will dafür aber alles haben.

## Was PhotoVault daraus macht

| | |
|---|---|
| **Personen** | Gesichter werden gruppiert, du benennst sie einmal — der Rest ordnet sich zu. Spitznamen inklusive: „Niki" findet „Annika Wolf". |
| **Ereignisse** | Aufnahmen derselben Gelegenheit werden zu Serien zusammengefasst. Eine Serie benennen heißt 150 Fotos benennen. |
| **Zeit** | Zeitstrahl nach Jahr, Monat und Ereignis — mit echter Uhrzeit, auch wo EXIF fehlt. |
| **Herkunft** | Eigene Aufnahmen, Empfangenes, Screenshots: sauber getrennt, filterbar. Die Bibliothek bleibt frei von Memes. |
| **Suche** | Kriterien zusammenklicken statt Suchsyntax lernen — „Niki **und** Jonas, 2015, Griechenland". |
| **Beschreibungen** | Optional deutsche Bildunterschriften per lokalem Sprachmodell, mit Album und Namen als Kontext. |
| **Reparatur** | Falsche Kamera-Uhren finden, abgeleitete Aufnahmezeiten ins EXIF zurückschreiben — das Archiv wird besser, nicht nur der Index. |

## Voraussetzungen

**Mindestens — damit läuft alles außer den Bildbeschreibungen:**

| | |
|---|---|
| Docker | mit Compose (Docker Desktop oder Engine) |
| Speicher | ~8 GB RAM, ~4 GB Plattenplatz für Modelle und Index |
| Fotos | ein Verzeichnis oder eine gemountete Freigabe |

Gesichtserkennung, Szenenerkennung, Datum, Ereignisse und die Suche brauchen
keine GPU und kein Sprachmodell.

**Optional — Grafikkarte.** Nicht nötig, aber der Unterschied ist groß.
Gemessen an derselben Maschine:

| | mit NVIDIA-GPU | nur CPU |
|---|---|---|
| Gesichtserkennung je Foto | ~30 ms | **~860 ms** |
| 5 000 Fotos | ~4 min | ~1,5 h |
| 43 000 Fotos | ~30 min | **~11 h** |

Reine CPU-Verarbeitung funktioniert also — sie dauert nur. Für ein paar
tausend Fotos ist das ein Nachtlauf, für ein großes Archiv ein Wochenende.
Der Ingest läuft wiederaufsetzbar (`--resume` ist Standard), man kann ihn also
abbrechen und fortsetzen. Wer eine NVIDIA-Karte hat, braucht ~4 GB VRAM und
`onnxruntime-gpu` passend zur CUDA-Version — siehe [Ohne Docker
entwickeln](#ohne-docker-entwickeln).

**Optional — [Ollama](https://ollama.com) für deutsche Bildbeschreibungen.**
Läuft auf dem Host, nicht im Compose-Verbund: es braucht GPU-Durchreichung und
lädt zweistellige Gigabyte. Ohne Ollama fehlen nur die Captions; alles andere
funktioniert.

| Zweck | Empfehlung | VRAM | gemessen |
|---|---|---|---|
| Bildbeschreibung | `qwen3.8:27b` | ~17 GB | 2,7 s/Foto · folgt Namensregeln zuverlässig |
| Alternative | `gemma4:26b` | ~18 GB | 1,3 s/Foto · kürzere Ergebnisse, Namensregeln ungeprüft |
| Textvektor | `qwen3-embedding:4b` | ~4 GB | 20 ms je Foto im Stapel |

Beide Vision-Modelle brauchen viel VRAM. Mit weniger als 16 GB wird es eng —
dann lohnt ein kleineres Modell, das PhotoVault über
`PHOTOVAULT_CAPTION_MODEL` akzeptiert. Getestet haben wir nur die beiden oben;
die Prompt-Regeln gegen erfundene Namen sind auf `qwen3.8` abgestimmt und
sollten mit einem anderen Modell an einer Stichprobe nachgeprüft werden.

Modelle für Gesichter (insightface) und Szenen (CLIP) lädt PhotoVault beim
ersten Lauf selbst, rund 1,5 GB.


## Loslegen

```bash
git clone https://github.com/<dein-konto>/photovault.git
cd photovault
cp .env.example .env        # PHOTO_DIR auf dein Fotoverzeichnis setzen
docker compose up -d
```

Oberfläche: **http://localhost:8000**

Dann festlegen, was überhaupt indiziert wird — ein Archiv enthält selten nur
Alben:

```bash
cp sources.example.txt sources.txt   # Verzeichnisse eintragen
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --dry-run
```

Der Trockenlauf zeigt vor dem Schreiben, was aufgenommen würde — aufgeschlüsselt
bis auf Unterordner. Passt es, ohne `--dry-run` wiederholen:

```bash
docker compose exec api python -m ingest.pipeline --sources-file sources.txt --skip-caption
```

Auf getesteter Hardware (RTX 5090, NAS über SMB) sind das **26 Fotos pro
Sekunde** — 50 000 Bilder in gut einer halben Stunde. `--skip-caption` lässt
die Bildbeschreibungen weg; die kommen später und dauern deutlich länger.

## Und dann?

Die Arbeit läuft in dieser Reihenfolge, weil jeder Schritt auf dem vorigen
aufbaut:

1. **Gesichter benennen** — Tab *Wer ist das?*. Das System schlägt Gruppen vor,
   du gibst ihnen Namen. Wer schon benannt ist, wird beim nächsten Mal
   vorgeschlagen: „Diese 700 Gesichter sehen aus wie Annika — stimmt das?"
2. **Serien benennen** — Tab *Serien*. Der Ordnername ist oft schon der
   richtige Name, dann genügt Bestätigen.
3. **Beschreibungen erzeugen** — zuletzt, denn mit benannten Personen im
   Kontext werden sie deutlich besser: „Annika Wolf und eine weitere Person auf
   einer Feier im April 2008" statt „zwei Personen".

```bash
docker compose exec api python -m ingest.caption_pass --dry-run
```

## So arbeitet es

```
Verzeichnisse aus sources.txt
   → Scan          nur was ausgewählt ist; Punkt-Verzeichnisse bleiben außen vor
   → Datum         EXIF, sonst Dateiname, sonst Dateizeit — mit Uhrzeit
   → Herkunft      Kamera / empfangen / verschickt / Screenshot
   → Gesichter     insightface: Box, 512d-Vektor, Frontalität
   → Abgleich      gegen bereits benannte Personen → Vorschläge, nie still
   → Szene         CLIP: Tags und 768d-Bildvektor
   → Beschreibung  optional, mit Album/Datum/Namen als Kontext
   → Textvektor    aus alldem, 2560d
   → Qdrant        benannte Vektoren + Payload-Filter
```

Zwei Entscheidungen prägen das Ergebnis:

**Die Beschreibung kommt nach den Metadaten**, nicht davor. Das Sprachmodell
sieht nicht nur Pixel, sondern Album, Datum und wer erkannt wurde — und
schreibt deshalb „bei einem Junggesellenabschied im Oktober 2018" statt „zwei
Männer".

**Nichts wird still zugeordnet.** Ein erkanntes Gesicht wird zum *Vorschlag*,
nie zur Tatsache. Ein falsch zugeordnetes Gesicht verfälscht danach jede Suche,
und niemand würde es bemerken.

---

## Unter der Haube

Ab hier wird es technisch. Für die Benutzung reicht alles oben.

### Ingest Pipeline

1. **Scan** — NAS-Scan, UNC/Laufwerk
2. **Datei-Zeiten** — mtime + ctime (Windows: Erstellung)
3. **EXIF** — Pillow, optional (darf fehlen); DateTimeOriginal aus Exif-IFD
4. **Folder-Parser** — `_photovault.json` + Ordner-/Datei-Regeln + Sequenz `IMG_0042`
5. **Face** — insightface detect + embed (ndarray)
6. **Face-Match** — ähnliche gelabelte Gesichter → `person_suggestions`
7. **Scene** — CLIP zero-shot tags + embedding
8. **Normalize** — `EXIF > Folder-JSON > Filename` (kein CLIP-Datum); Location persistieren
9. **Caption** — Qwen 3.8 Vision, Prompt enthält den Kontext
10. **Text Embed** — grounded Dokument (Ordner, Datum, Sequenz, Personen, Caption) → 2560d
11. **Write** — Qdrant upsert

`--skip-caption` überspringt nur die Bildbeschreibung; Metadaten, Gesichter,
CLIP und Textvektor laufen weiter. Das ist der schnelle Pfad für große Bestände.

### Query-Modell

```
"Jonas & Max, 2015, Griechenland"
  → person_ids CONTAINS [lennart, max]
  → taken_at in 2015
  → location_key = griechenland
  → optional text_vec (grounded) / clip_vec
```

### Suche

Personen werden als Gesichts-Bubbles angeklickt, nicht getippt — das Bild zeigt
sofort, ob die richtige Person gemeint ist. Wer doch tippt, kommt mit dem Vornamen
aus: `Jonas` löst auf `lennart-behr` auf, mehrdeutige Vornamen (zwei Roberts)
treffen alle Kandidaten, unbekannte Namen werden gemeldet statt stillschweigend
ignoriert.

Zwei Verknüpfungen sind umschaltbar:

| Schalter | Wirkung |
|---|---|
| `persons_match` | `all` = gemeinsam auf einem Foto · `any` = jede für sich |
| `match` | `all` = jedes Kriterium muss zutreffen · `any` = eines genügt |

Der Freitext ist **kein Filter, sondern eine Rangfolge** — er sortiert innerhalb
dessen, was die Kriterien übriglassen. `caption_min_score` schneidet schwache
Treffer ab.

### Was einen Re-Ingest überlebt — auch bei Gesichtern

Nicht nur am Foto (`caption_de`, `annotations`, `person_ids`, `person_names`),
sondern auch an jedem Gesicht: `person_id` und `person_name`. Ohne das löscht
ein Routinelauf die gesamte Labeling-Arbeit — bei mehreren tausend von Hand
zugeordneten Gesichtern der teuerste denkbare Datenverlust.

Von Hand geschriebene Captions tragen zusätzlich `caption_locked`; ein
Vision-Lauf überspringt sie.

### Orte

Erkannt werden Länder, deutsche Städte und Regionen aus dem Album- und dessen
Elternnamen. Bewusst nur auf ganze Wörter — „Kastenlauf“ darf nicht als Ort
durchgehen, und „Essen 2010“ bleibt lieber ohne Ort, als in der Stadt Essen zu
landen.

Eigene Orte kommen in eine `places.json` im Projektverzeichnis (oder wohin
`PHOTOVAULT_PLACES` zeigt):

```json
{ "uhrmacherweg": "Alte Wohnung", "nase": "Nases Wohnung" }
```

### Album statt Kameraordner

`Abi 08/100MSDCF/foto.jpg` gehört zum Album **Abi 08**, nicht zu `100MSDCF`.
Der Parser steigt über Kamera- und Sammelordner (`DCIM`, `100MSDCF`, `Bilder`,
`Neuer Ordner`, …) bis zu zwei Ebenen nach oben; der übersprungene Ordner
bleibt als `subfolder` erhalten. Ohne das verlor jedes Foto in einem
Kameraverzeichnis seinen Albumkontext samt Jahreshinweis.

### Profile und Ohren aussortieren

Der Detektor meldet auch Ohren und Hinterköpfe als Gesichter — im Archiv rund
**22 %**. Sie liefern ein Embedding, taugen aber nicht zur Wiedererkennung und
verstopfen die Labeling-Queue.

`frontality` (0–1) trennt sie: Frontal stehen die Augen weit auseinander und die
Nase mittig dazwischen, im Profil kollabiert beides. Der Wert entsteht aus den
fünf Landmarks beim Ingest. Cluster werden danach sortiert, und der Tab
**Unbekannte** kann per „schlechteste zuerst“ ganze Blöcke davon wegräumen —
`POST /api/persons/ignore` nimmt sie dauerhaft aus der Queue.

### Person-Labeling (Google-Fotos-Stil)

UI: `http://127.0.0.1:8000/` → Tab **Wer ist das?**

1. Ingest schreibt jedes Gesicht in Collection `faces` (Box + 512d)
2. Unbekannte Gesichter werden geclustert (cosine ≥ 0.4)
3. UI zeigt Crop + ähnliche Treffer; Chips für **bekannte Personen** (Face-Match) oder **neuen Namen**
4. Eine Zuordnung gilt für den ganzen Cluster; `person_ids` am Foto, nie still
5. Überspringen markiert `_skipped`

## Ohne Docker entwickeln

Wer am Code arbeitet, will die Pipeline direkt starten. Qdrant und Ollama
laufen dann als eigene Dienste (oder aus dem Compose-Verbund oben).

Eigene venv (`~/.venvs/photovault`), **nicht** die globale: PhotoVault braucht
`onnxruntime-gpu`, das `onnxruntime` ersetzt und andere Projekte still auf die GPU
zwingen würde.

```bash
python -m venv ~/.venvs/photovault
~/.venvs/photovault/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
~/.venvs/photovault/bin/pip install -e ".[dev]" onnxruntime-gpu
```

Prüfen, dass die Gesichtserkennung wirklich auf der GPU landet — ohne
`CUDAExecutionProvider` läuft insightface auf der CPU und kostet ~860 ms statt ~30 ms pro Foto:

```bash
~/.venvs/photovault/bin/python -c "import onnxruntime;print(onnxruntime.get_available_providers())"
```

```bash
# API
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# Ingest ohne Vision (schnell, Metadaten+Face+CLIP)
python -m ingest.pipeline --source /mnt/photo/Fotos --skip-caption

# Ingest mit Qwen-3.8-Captions (think aus, num_ctx 8192)
python -m ingest.pipeline --source /mnt/photo/Fotos
```

Nützliche Flags: `--limit N`, `--include TEXT` (nur passende Pfade), `--no-resume`
(auch bereits Indiziertes neu verarbeiten), `--progress-every N`. Jeder Lauf gibt am
Ende eine Zeitverteilung pro Pipeline-Stufe aus.

Läuft die Pipeline in WSL, muss der Share dort gemountet sein:

```bash
sudo mkdir -p /mnt/photo && sudo mount -t drvfs '\\192.0.2.10\photo' /mnt/photo
```

Env: `OLLAMA_URL`, `QDRANT_URL`, `PHOTOVAULT_CAPTION_MODEL`, `PHOTOVAULT_EMBED_MODEL`.

### Was einen Re-Ingest überlebt

`upsert` ersetzt das komplette Payload. Alles, was nicht aus der Datei
rekonstruierbar ist, wird deshalb vorher gelesen und zurückgeschrieben:
`caption_de`, `annotations`, `person_ids`, `person_names`. Ohne das löscht ein
`--skip-caption`-Lauf jede Caption und jede bestätigte Person.

### Werkzeuge

```bash
python -m tools.quality_report --prefix /mnt/photo   # Datum, Gesichter, Tags, Vollständigkeit
python -m tools.acceptance --prefix /mnt/photo       # Szenarien aus docs/spec.md + Latenz
```

### API-Endpunkte

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/health` | Health-Check |
| POST | `/api/search` | Komposit-Query; `caption_query` nutzt den 2560d-Textvektor |
| GET | `/api/persons` | Bekannte Personen |
| GET | `/api/persons/unlabeled` | Face-Cluster ohne Namen (gecacht, nach Frontalität sortiert) |
| GET | `/api/persons/unknown` | Alle unbenannten Gesichter flach; `sort=quality\|worst` |
| GET | `/api/persons/{id}/photos` | Fotos der Person, nach Jahr und Ereignis gruppiert |
| GET | `/api/persons/{id}/faces` | Gesichter der Person, schwächste zuerst |
| POST | `/api/persons` | Neue Person + Cluster zuordnen |
| POST | `/api/persons/{id}/assign` | Cluster einer bekannten Person zuordnen |
| POST | `/api/persons/{id}/rename` | Person umbenennen (Fotos + Vektoren ziehen nach) |
| POST | `/api/persons/{id}/aliases` | Spitznamen pflegen — „Karo“ findet „Annika Wolf“ |
| DELETE | `/api/persons/{id}` | Zuordnung auflösen — Gesichter zurück in die Queue |
| POST | `/api/persons/faces/unassign` | Einzelne Gesichter aus einer Person lösen |
| POST | `/api/persons/faces/move` | Einzelne Gesichter einer anderen Person geben |
| POST | `/api/persons/skip` | Cluster überspringen |
| POST | `/api/persons/ignore` | Gesichter dauerhaft aus der Queue nehmen |
| POST | `/api/search/query` | Zusammengeklickter Ausdruck mit UND/ODER und Klammern |
| GET | `/api/photos/{id}` | Alle Metadaten eines Fotos, inkl. Datumsherkunft |
| POST | `/api/photos/{id}/caption` | Beschreibung von Hand setzen (wird gesperrt) |
| POST | `/api/photos/caption/bulk` | Beschreibung für viele Fotos auf einmal |
| GET | `/api/faces/{id}/crop` | JPEG-Ausschnitt (gecacht) |
| GET | `/api/photos/{id}/thumb` | Vorschaubild, `size` = 160/320/640/1280 |
| POST | `/api/photos/annotate` | Eigene Notizen an markierte Fotos (`add`/`remove`/`replace`) |
| POST | `/api/photos/reembed` | Text-Vektoren neu bauen (IDs oder ganzes Album) |
| GET | `/api/jobs` | Alle Jobs mit Fortschritt |
| GET | `/api/jobs/{id}` | Ein Job |
| POST | `/api/ingest/start` | Ingest starten (Stub) |
| GET | `/api/ingest/progress` | Stand des jüngsten Ingest-Laufs |
| GET | `/api/ingest/stats` | Anzahl indizierter Fotos |

### Fortschritt

`http://127.0.0.1:8000/jobs.html` zeigt alle Läufe mit Balken, Rate, ETA und
aktuellem Ordner; die Seite pollt alle 2 s. Der Tracker schreibt nach
`ingest_jobs` in Qdrant und ist über das Feld `kind` generisch — Caption-Nachläufe
oder Re-Embeds können dieselbe Anzeige benutzen. Ein Lauf, dessen Prozess stirbt,
erscheint nach 2 Minuten ohne Aktualisierung als `stale` statt ewig als `running`.

### Vorschaubilder

Die Originale liegen auf dem NAS — ein Trefferraster mit 48 Bildern wären sonst
48 SMB-Reads pro Seitenaufruf. Thumbnails werden deshalb beim Ingest gleich
miterzeugt (das Bild ist dort ohnehin dekodiert) und unter
`~/.cache/photovault-thumbs` abgelegt. `--no-thumbs` schaltet das ab.

### Eigene Notizen (`annotations`)

Wissen, das kein Modell aus Pixeln holt — „das war im Stripclub", „Omas Garten".
50 von 200 JGA-Fotos markieren und beschriften:

```bash
curl -X POST localhost:8000/api/photos/annotate -H 'Content-Type: application/json' \
  -d '{"photo_ids":["..."],"annotations":["Stripclub"],"mode":"add"}'
```

`annotations` ist ein Keyword-Index, also exakt filterbar, und fließt zugleich in den
Text-Vektor. Das ist der Hebel gegen ein gemessenes Problem: Fotos desselben Albums
liegen im Text-Vektor bei Cosinus 0.95–0.997, sind also kaum unterscheidbar. Notizen
und Captions machen Teilmengen innerhalb eines Events wieder trennbar — und verknüpfen
gleichartige Abschnitte über Events hinweg.

Nach dem Annotieren oder Labeling wird **nur der Text-Vektor** neu gerechnet
(~130 ms/Foto), nicht die Caption (~3,0 s/Foto).

## Durchsatz

0,86 → **26,2 Fotos/s**; 50 000 Fotos in rund 32 Minuten (NFR: unter 2 Stunden).

Der größte Posten war nicht Rechenzeit, sondern Wartezeit: Lesen, Dekodieren
und Netzwerk brauchten zusammen ~200 ms pro Foto, die GPU-Arbeit nur 79 ms.
Ein nativer `cifs`-Mount statt des WSL-Durchreichers hat davon den größten
Teil beseitigt (66,6 → 10,9 ms je Datei).
Das Fließband in `ingest/parallel.py` überlappt die Stufen; `--workers 1`
schaltet auf den sequenziellen Ablauf zurück, `--gpu-batch` steuert die
CLIP-Stapelgröße.

Messungen, Begründungen und die Fallstricke — von der still auf CPU
zurückfallenden `onnxruntime` über drei umsonst gerechnete insightface-Modelle
bis zu 56 Fotos, die ein defektes Bild aus dem Index riss — stehen in
[docs/performance.md](docs/performance.md).

## Status

Gegen ein echtes Archiv gemessen (43 000 Bilder auf einer NAS, davon 17 453
ausgewählt und indiziert):

| | |
|---|---|
| indiziert | 17 453 Fotos, 27 968 Gesichter, 0 Fehler |
| Kanäle | 10 014 empfangen · 5 691 eigene Aufnahmen · 1 732 verschickt · 16 Rest |
| mit Datum | 100 % · davon 90,7 % mit echter Uhrzeit |
| Personen benannt | laufend, per „Wer ist das?" |
| Ereignisse | 267 Serien ab 5 Fotos, davon 81 mit Namensvorschlag aus dem Ordner |
| Captions | < 1 % — Vision-Lauf steht aus: `python -m ingest.caption_pass` |
| Query-Latenz | 3–10 ms |
| Durchsatz | 26,2 Fotos/s |

Offen: Captions erzeugen, Ortserkennung ausbauen, Videos einbinden
(2 904 liegen im Archiv, siehe [docs/curation.md](docs/curation.md)).

## Privatsphäre

Ein Foto-Archiv ist voller Namen von Menschen, die nie gefragt wurden, ob sie
in einem öffentlichen Repository auftauchen wollen. Beim Schreiben von Tests
und Dokumentation greift man aber genau nach diesen Namen — sie sind die
Beispiele, die zur Hand liegen.

```bash
python -m tools.privacy_check           # alle versionierten Dateien
python -m tools.privacy_check --install # als pre-commit-Hook einrichten
```

Die Prüfung kennt die echten Namen, weil sie im laufenden Index stehen — eine
handgepflegte Liste veraltet mit jedem neuen Label. Zusätzlich sucht sie nach
Schlüsseln, privaten IP-Adressen und E-Mail-Adressen.

Nicht versioniert werden `sources.txt` (die Ordnernamen eines privaten Archivs
verraten schon viel; Vorlage: `sources.example.txt`) und `.privacy-denylist`.
