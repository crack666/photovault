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
| **Reparatur** | Kopierzeit von Aufnahmezeit trennen, falsche Kamera-Uhren finden, abgeleitete Zeiten ins EXIF zurückschreiben — das Archiv wird besser, nicht nur der Index. |
| **Atlas** | Das ganze Archiv als eine Karte: Ähnliches liegt beieinander, Kontinente tragen Namen aus den Bildbeschreibungen. Ein Lasso um einen Haufen markiert tausend Fotos auf einmal. |

## Voraussetzungen

**Mindestens — damit läuft alles außer den Bildbeschreibungen:**

| | |
|---|---|
| Docker | mit Compose (Docker Desktop oder Engine) |
| Speicher | ~8 GB RAM, ~4 GB Plattenplatz für Modelle und Index |
| Fotos | ein Verzeichnis oder eine gemountete Freigabe |

Gesichtserkennung, Szenenerkennung, Datum, Ereignisse und die Suche brauchen
keine GPU und kein Sprachmodell.

**Optional — Grafikkarte.** Nicht nötig. Der Unterschied ist deutlich, aber
kleiner als man denkt. Gemessen an derselben Maschine, je Foto:

| | GPU | CPU, 4 Kerne | CPU, 8 | CPU, 24 |
|---|---|---|---|---|
| Lesen und Dekodieren | — | 64 ms | 57 ms | 89 ms |
| Gesichter (insightface) | ~30 ms | 150 ms | 154 ms | 149 ms |
| Szene (CLIP ViT-L/14) | ~40 ms | 401 ms | 245 ms | 153 ms |
| **zusammen** | **~26 Fotos/s** | **1,6/s** | **2,2/s** | **2,6/s** |
| 5 000 Fotos | ~3 min | 51 min | 38 min | 33 min |
| 43 000 Fotos | ~30 min | 7,3 h | 5,4 h | 4,7 h |

Die Gesichtserkennung kostet auf der CPU **150 ms**, nicht die 860 ms, die
hier früher standen: `buffalo_l` bringt fünf Modelle mit, gebraucht werden
zwei, und `allowed_modules` schaltet die anderen drei ab (siehe
`ingest/face_embedder.py`). Sie ist auch fast unabhängig von der Kernzahl —
skalieren tut CLIP, und das ist auf vier Kernen der Engpass.

Für ein paar tausend Fotos ist das also ein Kaffee, für ein großes Archiv ein
Abend. Der Ingest läuft wiederaufsetzbar (`--resume` ist Standard), man kann
ihn abbrechen und fortsetzen. Wer eine NVIDIA-Karte hat, braucht ~4 GB VRAM
und `onnxruntime-gpu` passend zur CUDA-Version — siehe [Ohne Docker
entwickeln](#ohne-docker-entwickeln).

**Was ohne Grafikkarte alles funktioniert:** Datum und Herkunft, Gesichter
finden und benennen, Serien, die Suche nach Personen, Jahr, Ort, Album und
Tags — und der **Atlas**, denn UMAP rechnet ohnehin auf der CPU. Nur die
Bildbeschreibungen und die *Freitextsuche* brauchen Ollama; ohne es tragen die
Kontinente ihre Szenen-Tags als Namen statt der Captions. Was fehlt, sagt die
Jobs-Seite: Läufe, deren Voraussetzung nicht da ist, sind dort gesperrt und
nennen den Grund, statt „gestartet" zu melden und still zu sterben.

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

### Der einfache Weg

Auch ohne Vorkenntnisse. Drei Schritte, danach läuft es.

**1. Docker Desktop installieren.** Docker ist ein Programm, das andere
Programme mitsamt allem, was sie brauchen, in einem Paket startet — damit man
nicht Python, Datenbanken und Bibliotheken einzeln einrichten muss.

* [Download für Windows und macOS](https://www.docker.com/products/docker-desktop/)
* Installieren, Rechner neu starten, Docker Desktop öffnen
* Warten, bis das Wal-Symbol ruhig steht (beim ersten Mal ein, zwei Minuten)

**2. PhotoVault herunterladen.**
[Als ZIP herunterladen](https://github.com/crack666/photovault/archive/refs/heads/master.zip)
und irgendwohin entpacken — auf den Desktop reicht.

**3. Starten.**

| | |
|---|---|
| Windows | Doppelklick auf **`start.bat`** |
| macOS / Linux | Terminal im Ordner öffnen, `./start.sh` eingeben |

Das Skript fragt einmal nach deinem Fotoordner, lädt beim ersten Mal rund 2 GB
herunter und öffnet danach den Browser. Anschließend fragt es, ob es die Fotos
einlesen soll — und zeigt vorher, was es gefunden hat, damit du es abnicken
kannst.

Beim nächsten Mal genügt derselbe Doppelklick; die Antworten sind gemerkt.

> **Nichts verlässt deinen Rechner.** Weder Fotos noch Gesichter noch Namen.
> Es gibt keinen Server, bei dem man sich anmeldet.

### Der Weg für Geübte

```bash
git clone https://github.com/crack666/photovault.git
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

### Von einem anderen Gerät aus

PhotoVault hört standardmäßig nur auf `127.0.0.1` — das ist die richtige
Voreinstellung, denn **es gibt keine Anmeldung**. Wer die Adresse erreicht,
sieht alle Fotos, alle Namen und alle Gesichtsvektoren.

Mit [Tailscale](https://tailscale.com) wird es ohne Portfreigabe im eigenen
Netz erreichbar, und die Zugangskontrolle übernimmt Tailscale:

```bash
tailscale serve --bg --http=8000 http://127.0.0.1:8000
```

Danach steht die Oberfläche unter `http://<rechnername>.<tailnet>.ts.net:8000`.
Das gilt **nur im eigenen Tailnet** — `serve`, nicht `funnel`; ins offene
Internet geht davon nichts. Wieder abschalten:

```bash
tailscale serve --http=8000 off
```

Läuft der Server in WSL2 im Standard-NAT-Modus, genügt das trotzdem: der
Proxy verbindet sich auf Windows-`localhost`, und WSLs `localhostForwarding`
leitet von dort in die VM. `--host 0.0.0.0` allein hilft dagegen nicht — von
außen kommt ohne Portproxy nichts in die WSL-VM hinein.

### Wenn etwas klemmt

| | |
|---|---|
| „Docker laeuft nicht" | Docker Desktop öffnen und warten, bis das Symbol ruhig steht |
| Port 8000 belegt | in `.env` `API_PORT=8080` setzen, neu starten |
| Nichts wird gefunden | `sources.txt` prüfen — die Pfade beginnen mit `/photos`, nicht mit `D:\` |
| Seite lädt nicht | `docker compose logs api` zeigt, woran es liegt |
| Von vorn anfangen | `docker compose down -v` löscht den Index. Die Fotos bleiben unberührt. |


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
4. **Karte rechnen** — Tab *Atlas*. Erst jetzt sinnvoll: die Kontinente werden
   aus den Beschreibungen benannt, und ohne sie heißen sie nach den groben
   Szenen-Tags.

```bash
docker compose exec api python -m ingest.caption_pass --dry-run
```

Der Satz landet im Index **und** in der Datei (`ImageDescription` / Windows-Kommentar), mit Herkunftsnotiz — sonst ist er nach dem nächsten Kopieren weg. `--no-exif` schreibt nur den Index; `--exif-only` holt vorhandene Index-Sätze nach.

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
3. **EXIF** — Pillow, optional (darf fehlen); DateTimeOriginal vor DateTime
   (Kopierzeit). Probe vor dem Überschreiben: [docs/dates.md](docs/dates.md)
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

### Atlas — die Karte des Archivs

UI: Tab **Atlas**. Alle Fotos auf einer Fläche, Nähe heißt „sieht sich
ähnlich": die CLIP-Vektoren werden per UMAP auf zwei Dimensionen gebracht.
Für 17 370 Fotos dauert das 23 Sekunden.

```bash
pip install 'photovault[atlas]'   # umap-learn + scikit-learn, nur hierfür
python -m tools.atlas_build
```

Oder ohne Terminal: **Jobs → Karte neu rechnen**. Der Lauf meldet sich mit
Phase und Anteil in derselben Liste (`laden` 0 % → `umap` 10 % → `stapel`
65 % → fertig), denn UMAP allein braucht die Hälfte der Zeit — ein Balken,
der sich gleichmäßig füllt, wäre eine Lüge. Fehlt `umap-learn`, ist der Knopf
gesperrt und nennt den Grund, statt zu starten und still zu sterben.

Das Ergebnis ist eine statische Datei unter `web/static/atlas/atlas.json`
(1,3 MB). Sie wird über den vorhandenen StaticFiles-Mount ausgeliefert — die
Karte neu zu rechnen braucht **keinen API-Neustart**.

**Die Ordneransicht wäre an diesem Archiv sinnlos.** 76 % der Fotos liegen in
drei von 68 Ordnern (`WhatsApp Images` 9 617, `HandyPics` 2 844, `Sent`
1 732). Eine Kartenansicht auch: `location` steht bei 24 von 17 429 Fotos.
Was trägt, ist die Ordnung, die aus den Vektoren selbst folgt.

Zwei Linsen auf denselben Punkten, mit weichem Übergang dazwischen:

| Linse | waagerecht | senkrecht |
|---|---|---|
| **Bedeutung** | UMAP | UMAP |
| **Zeit × Bedeutung** | Jahre, dichte-normalisiert | dieselbe Bedeutungsachse |

Die Zeitachse ist bewusst nicht linear. 67 % der Fotos stammen aus den letzten
vier Jahren — linear bekämen zwanzig Jahre Familiengeschichte ein Drittel der
Breite und die WhatsApp-Jahre zwei Drittel. Jedes Jahr bekommt deshalb Platz
nach `Anzahl^0.4`.

**Beschriftung aus Captions, nicht aus Tags.** An denselben Clustern gemessen:

| Captions im Cluster | aus Captions | aus `scene_tags` |
|---|---|---|
| 97 % | `abistreich, juni, schülern` | `gruppenfoto, party, gruppe` |
| 33 % | `brandenburger, fernsehturm, berlin` | `dokument, urlaub, party` |
| 3 % | `apple, store, ipad, pencil` | `kinder, geburtstag, urlaub` |

Die Tags sind nicht nur grob, sie liegen stellenweise daneben. Gewählt wird
per gewichteter PMI: häufig *in diesem* Kontinent und zugleich selten
anderswo. Prompt-Floskeln fallen doppelt heraus — über eine Wortliste und
über eine Obergrenze für die Dokumentfrequenz (`aufgenommen` steht in 24 %
aller Captions). Ein Kontinent ohne Captions bekommt keinen erfundenen Namen,
sondern seine Tags und einen sichtbaren Vorbehalt.

**Was die Karte nicht weiß: wer zu sehen ist.** CLIP kodiert, wie ein Bild
*aussieht* — nicht, wer darauf ist. Ein Ganzkörper-Spiegelselfie landet neben
anderen Ganzkörper-Spiegelselfies, gleich wer davorsteht: die zwölf nächsten
Nachbarn eines solchen Fotos stammten im Test aus 2016 bis 2025 und zeigten
vier verschiedene Menschen. Identität steckt im 512d-Gesichtsvektor, und der
geht bewusst **nicht** ins Layout ein — sonst zerfielen die inhaltlichen
Kontinente (Strand, Dokumente, Skiurlaub) in Personenhaufen. Wer wissen will,
wo eine Person liegt, wählt sie in der Leiste: ihre Fotos leuchten auf, der
Rest tritt zurück. Beim Überfahren nennt die Karte ohnehin, wer bestätigt ist.

Der Vektorraum ist umschaltbar: `--space text` legt die Karte über die
grounded Textvektoren statt über CLIP, und „Mehr davon" nimmt `using=text`.
Der Textvektor enthält Album, Datum, Personen und Caption — er antwortet auf
„geht worum", nicht auf „sieht aus wie". Achtung: laut den Messungen in
[docs/performance.md](docs/performance.md) liegen Fotos desselben Albums dort
bei Cosinus 0,95–0,997; die Karte zeigt dann eher Alben als Themen.

**Farbe trägt die Frage.** Umschaltbar auf Kontinent, Herkunft, Jahr — oder
**Zustand**: wie weit ein Foto schon eingeordnet ist (Person, Beschreibung,
EXIF-Datum, benannte Serie). Warm heißt „hier liegt noch Arbeit".

**Nahduplikate werden gestapelt, nicht gelöscht.** Bei Cosinus ≥ 0.95 fallen
4 406 Fotos in 1 750 Stapel — BURST-Serien, Feuerwerk-Salven, und 2 060
Fotos, die es doppelt gibt, weil das eigene Bild per WhatsApp zurückkam. Der
Schalter *Stapel falten* zeigt 14 714 statt 17 370. Obenauf liegt das Bild mit
dem meisten Kontext: bestätigte Person vor Beschreibung vor eigener Aufnahme.

**Zwei Ebenen: Fotos oder Gelegenheiten.** 17 370 Einzelbilder sind nicht
stöberbar. Der Umschalter *Serien* zeigt stattdessen eine Kachel je Ereignis —
dieselben Serien wie im Tab *Serien*, über alle Kanäle: 5 239 insgesamt, ab
drei Fotos noch 1 755 über 12 839 Bilder. Größe ∝ √Fotozahl, sonst verdeckt
„Silvester 2012" mit 163 Bildern alles andere.

Jede Serie trägt zusätzlich, **wie weit ihre Fotos im Bedeutungsraum
auseinanderliegen**. Das trennt echte Gelegenheiten von Zeitfenster-Artefakten:

| Serie | Streuung |
|---|---|
| `Abiball` (98 Fotos) | 0,021 |
| `Silvester 2012/13` (163) | 0,141 |
| `Abistreich` (139) | 0,172 — knapp unter der Grenze |
| `WhatsApp Images`, ein Tag im Oktober 2022 (16) | 0,270 |
| `Sent`, ein Tag im November 2022 (24) | 0,325 |

Über 0,18 bekommt die Kachel einen orangen Rahmen und den Satz „hält
inhaltlich nicht zusammen — eher ein Tag als eine Gelegenheit". Bei `whatsapp`
ist die Empfangszeit nicht die Aufnahmezeit, und über einen wachen Tag entsteht
nie eine Drei-Stunden-Lücke (siehe [docs/curation.md](docs/curation.md)) — die
Streuung macht genau das sichtbar.

**„Mehr davon" — die Auswahl wird zur Abfrage.** Das ist die Abfragesprache
eines Vektorraums, und ohne sie ist eine Karte nur ein Poster: man sieht etwas,
kann ihm aber nicht folgen. Strg+Klick auf ein Foto oder der Knopf in der
Auswahl holt die Nachbarn über `POST /api/photos/similar`, die Kamera fährt
hin, und von dort geht es weiter. `← zurück` nimmt den Schritt zurück, damit
man sich traut.

Gefragt wird über die **Punkt-IDs**, nicht über Vektoren — Qdrant holt sie
selbst. Damit kommt weder Ollama noch die GPU ins Spiel: 200 Treffer in 29 ms,
und ein laufender Caption-Lauf merkt nichts davon. Mehrere Beispiele werden zum
Schwerpunkt gemittelt, ein einzelnes ist eine normale Nachbarschaftsabfrage.
Über 64 Beispiele wird gleichmäßig ausgedünnt, nicht vorn abgeschnitten.

**Der Einstieg schlägt Arbeit vor.** Eine Karte, die beim Öffnen nichts sagt,
ist ein Poster. Stattdessen: „3 182 Fotos noch unberührt" plus die fünf
Kontinente mit den meisten offenen Fotos — Klick fliegt hin *und* markiert sie.
Darunter der Weg zur nächsten Arbeit, wenn sie woanders liegt: „3 172 Fotos
zeigen Gesichter ohne Namen".

Sortiert wird nach **Anzahl**, nicht nach Anteil. Ein Anteilsschwellwert lieferte
nach dem Caption-Lauf keinen einzigen Vorschlag mehr — die Karte nannte eine Zahl
und ließ einen stehen.

**Auf der Karte wird gearbeitet.** Ein Lasso (oder Shift+Ziehen) markiert
alles im Umkreis; Alt zieht ab. Die Auswahl bekommt eine Notiz
(`/api/photos/annotate`, danach exakt filterbar) oder eine Beschreibung
(`/api/photos/caption/bulk`), oder läuft als Diaschau. Das Neuberechnen der
Textvektoren ist abschaltbar — es belegt sonst die GPU, die gerade
Beschreibungen erzeugt.

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
python -m tools.atlas_build                          # Karte fuer den Tab "Atlas"
python -m tools.reembed_all --dry-run                # Text-Vektoren neu bauen
```

### Warum im Text-Vektor das Datum fehlt

Die Caption nennt das Aufnahmedatum absichtlich — der Prompt verlangt es, damit
der Satz für Menschen im Kontext steht. Im eingebetteten Dokument steht es
damit aber **dreimal**: strukturiert im Payload (dort filtert es exakt), in der
Kopfzeile, und ausgeschrieben mitten im Satz. `das foto wurde am` kommt 916 mal
vor, `das foto entstand am` 446 mal.

`grounding.caption_for_vector` nimmt nur die dritte Kopie heraus. `caption_de`
selbst bleibt unberührt. An 200 Fotos über alle 40 Kontinente gemessen, mit
`qwen3-embedding:4b`:

| | mit Datum | ohne |
|---|---|---|
| Cosinus zwischen zwei Fotos (Median) | 0,429 | **0,411** |
| Personen finden (R-Präzision) | 55 % | **57 %** |
| Szenen finden (Präzision unter 20) | 46 % | **47 %** |

Eine nackte Jahreszahl fliegt nur heraus, wenn sie das Jahr *dieses* Fotos ist —
sonst verlöre „Abi 08" oder „WM 2014 Trikot" seinen Sinn.

**Verschlagwortung wäre schlechter, nicht besser.** Dieselbe Messung mit
Komma-Listen statt Sätzen: die Streuung wird deutlich größer (Cosinus 0,329),
das Finden aber schlechter (Szenen 31 % statt 46 %). Streuung ist nicht
Trennschärfe — hier war sie Signalverlust. Das Modell ist auf Sprache
trainiert; „Ada, gestikuliert, Weihnachtsfeier" wirft die Beziehungen weg,
die „Ada Lovelace gestikuliert bei der Weihnachtsfeier" trägt.

Bei der **Anfrage** ist es dagegen fast gleichgültig: „Niki und Jonas trinken
gemeinsam ein Bier" und „Niki, Jonas, Bier" liegen bei Cosinus 0,78 und liefern
16 bis 18 derselben ersten 20 Treffer. Man darf tippen, wie man mag.

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
| POST | `/api/photos/similar` | „Mehr davon" — Nachbarn zu einer Auswahl, `using=clip\|text` |
| GET | `/api/capabilities` | Was diese Installation kann — und was sonst fehlt |
| GET | `/api/ingest/state` | Lücken im Index (Fotos ohne Text-/CLIP-Vektor) |
| GET | `/api/jobs` | Alle Jobs mit Fortschritt |
| GET | `/api/jobs/{id}` | Ein Job |
| DELETE | `/api/jobs/{id}` | Einen Eintrag vergessen (nicht den Lauf) |
| POST | `/api/jobs/prune` | Liste aufräumen — `what=aborted\|finished` |
| POST | `/api/jobs/run` | Lauf starten — `caption\|reembed\|atlas` |
| POST | `/api/ingest/start` | Ingest starten (Stub) |
| GET | `/api/ingest/progress` | Stand des jüngsten Ingest-Laufs |
| GET | `/api/ingest/stats` | Anzahl indizierter Fotos |

### Fortschritt

`http://127.0.0.1:8000/jobs.html` zeigt alle Läufe mit Balken, Rate, ETA und
aktuellem Ordner; die Seite pollt alle 2 s. Von dort lassen sich die langen
Läufe auch **starten** — Bildbeschreibungen, Text-Vektoren, Karte — ohne eine
Shell auf dem Rechner mit den Fotos.

Was gestartet werden darf, steht in `api.routes.jobs.RUNNABLE` und nirgends
sonst. Die Oberfläche hat keine Anmeldung; diese Liste ist die einzige
Schranke. Aufgerufen wird ohne Shell, und jedes Argument entsteht im Server
aus einem getippten Feld. Zwei Läufe, die beide die Grafikkarte brauchen,
startet sie nicht gleichzeitig — das macht beide langsamer, nicht schneller.

Die Liste wächst mit jedem Lauf und jedem Abbruch. Sie ist deshalb seitenweise
(20 je Seite, neueste zuerst), nach Art filterbar, und **Liste aufräumen**
entfernt Einträge — nie einen laufenden, und je Art bleiben die drei jüngsten
stehen. Aufgeräumt wird nach Kategorie („läuft nicht mehr"), nicht nach
Zustandsnamen: im Bestand stehen `done`, `succeeded`, `partial` und
`done-with-errors` aus mehreren Programmgenerationen, eine Namensliste hätte
41 von 47 Einträgen stillschweigend nicht angefasst. Der Tracker schreibt nach
`ingest_jobs` in Qdrant und ist über das Feld `kind` generisch — Caption-Nachläufe
oder Re-Embeds können dieselbe Anzeige benutzen. Ein Lauf, dessen Prozess stirbt,
erscheint nach 2 Minuten ohne Aktualisierung als `stale` statt ewig als `running`.

### Wenn eine Änderung nicht ankommt

Die UI besteht aus ES-Modulen, und ein `import "./core/dom.js"` trägt keinen
Cache-Parameter. Der Browser liefert dann beliebig lange die alte Fassung aus,
während `index.html` schon die neue erwartet — eine geänderte Ansicht kommt
nicht an, obwohl die Datei auf der Platte richtig ist. Zwei Vorkehrungen
dagegen:

* `/static` antwortet mit `Cache-Control: no-cache` (`api.main.FreshStatic`).
  Das heißt „vorher nachfragen", nicht „nicht speichern": unveränderte Dateien
  werden mit 304 und null Byte beantwortet, auch die 2,8 MB der Karte.
* Jeder Modul-Import trägt zusätzlich `?v=N` — eine Zahl für alle Module
  gemeinsam, die gemeinsam erhöht wird. Header allein haben in der Praxis nicht
  gereicht.

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
