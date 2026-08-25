# PhotoVault — Spec
> Status: Locked (Phase 3) · 2026-08-23 (Modelle + Anfüttern)

## Problem
Riesiges privates Fotoarchiv (~50k Fotos) auf TrueNAS, kaum gezielt durchsuchbar.

## JTBD
"Zeig mir [Personen] gemeinsam, zu [Zeit/Ort], in [Szene]" — ohne Ordner zu wühlen.

## Zielfragen
- "Jonas & Max gemeinsam"
- "Mareike & Sophie, 2015, Griechenland"
- "Strandfotos mit Max im Sommer"
- "Geburtstagsfotos"

## Constraints
- 100 % lokal (private Personen)
- Bestehende Infrastruktur: TrueNAS, crackdesk-GPU (RTX 5090), Qdrant, Ollama, ai-stack
- EXIF nicht immer vorhanden — Ordner, Dateiname, Sequenz, File-ctime sind First-Class-Kontext
- Caption darf nicht nur Pixel beschreiben; sie wird mit diesem Kontext angefüttert

## Non-Goals (v1)
- Video-Dateien, Cloud-Sync, Foto-Editing, Multi-User
- Auto-Labeling ohne Bestätigung (`person_ids` nie still setzen)

## Architektur
NAS → EXIF/Folder/Filezeiten/Sequenz → Face → Face-Match → CLIP → Normalize
    → Caption(Kontext) → Text-Embed(grounded) → Qdrant → FastAPI → UI

### Model-Stack
| Layer | Modell | Output |
|---|---|---|
| Face | insightface buffalo_l | 512d, BGR-ndarray |
| Face-Match | Qdrant `face` cosine ≥ 0.4 | `person_suggestions` (kein Auto-`person_ids`) |
| Scene | CLIP ViT-L/14 | 768d + zero-shot tags |
| Caption | Qwen 3.8-27B Vision (`qwen3.8:27b-ctx8k`) | DE JSON; `think: false`, Kontext 8192 |
| Text Embed | qwen3-embedding:4b-ctx2k | **2560d** über Ordner+Datum+Sequenz+Personen+Caption |

### Caption-Kontext (Anfüttern)
Nicht nur „zwei Männer vor Palmen“. Der Prompt (und der Text-Vektor) enthalten:

| Feld | Quelle |
|---|---|
| Aufnahmedatum | EXIF > Folder-JSON > Filename/Ordnerjahr |
| Dateierstellung / mtime | Filesystem (`st_ctime` / `st_mtime`) |
| Ordner/Album | Parent-Name / `_photovault.json` |
| Sequenz | `IMG_0042` → 42 im Album „Griechenland 2015“ |
| Ort | Folder-JSON / bekannte Ortsnamen im Ordner |
| Gesichter | insightface `face_count` **vor** Caption |
| Namen | Face-Match (`person_suggestions`) stark; `folder_people` nur Hinweis |

LLM-Regel: Namen nicht erfinden. Ohne Match: „zwei Personen“.

### Qdrant Collection: photos
Named Vectors: face (512d), clip (768d), text (**2560d**)

Payload u. a.: `date`, `taken_at`, `location` / `location_key`, `folder_name`,
`sequence_in_folder`, `file_ctime`, `file_mtime`, `person_ids`, `person_suggestions`,
`caption_de`, `scene_tags`.

### Merge-Strategie
| Feld | Priorität |
|---|---|
| date | EXIF > Folder-JSON > Filename/Ordnerjahr — **kein** CLIP-Datum |
| location | Folder-JSON / Orts-Hint > GPS (Reverse-Geocode später) |
| scene_tags | CLIP, Caption-Tags ergänzen |
| person_ids | Nur Labeling-UI |
| person_suggestions | Face-Match, für Caption und UI |

### Folder-JSON (_photovault.json)
location, date_range, description, people, type, notes

### Person-Labeling-Flow
1. Ingest → **jedes** Gesicht als Punkt in Collection `faces`
2. UI „Wer ist das?“: Cluster (cosine ≥ 0.4), Cover-Crop, Match-Chips bekannter Namen **oder** neuer Name
3. User bestätigt → `person_id` auf Faces, `person_ids` am Foto
4. Neue Fotos: Face-Match als Chip/Caption-Kontext, nie stilles Auto-Label

## NFRs
| NFR | Ziel |
|---|---|
| Lokal | 100 % lokal, kein Cloud |
| Skalierung | 50k Metadaten+Face+CLIP in < 2h mit `--skip-caption`; Vision nachziehen |
| Query-Latenz | < 500ms |
| Idempotent | Re-Ingest = Upsert (Pfad-Hash) |
| Robustheit | EXIF-Fehler → skip + log, nie crash |

## Acceptance Scenarios
1. Komposit-Query: "Jonas & Max, 2015, Griechenland" → Payload-Filter
2. Kein EXIF: Ordner "Griechenland 2015" + IMG_0042 → date/location/sequence im Payload **und** im Text-Vektor
3. Caption mit Kontext erwähnt Jahr/Ort, halluziniert keine Namen ohne Match
4. Person-Labeling: Vorschlag → Bestätigung → filterbar
5. `--skip-caption`: trotzdem grounded Text-Embed aus Metadaten

## Tech-Stack
| Layer | Tech |
|---|---|
| Ingest | Python 3.11, insightface, open_clip, Pillow, qdrant-client, Ollama HTTP |
| GPU | crackdesk (RTX 5090), Chat-Slot `NUM_PARALLEL=1` |
| Vector Store | Qdrant (:6333) |
| Caption/Embed | Ollama (:11434) |
| API | FastAPI |
| UI | Next.js (noch nicht im Repo) |
| Storage | TrueNAS (Fotos) |
