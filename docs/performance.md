# Durchsatz — Messungen und Fallstricke

> Gemessen am 23.08.2026 gegen `\\192.0.2.10\photo\Fotos` (2162 Fotos,
> 2,9 GB) auf crackdesk: RTX 5090, WSL2, Qdrant und Ollama in Docker.
> Alle Zahlen mit `--skip-caption` — dem 50k-Hot-Path.

Das NFR aus [spec.md](spec.md) verlangt **50 000 Fotos in unter 2 Stunden**,
also 6,9 Fotos/s. Die erste Messung lag bei 0,86.

## Was gewirkt hat

| Eingriff | Fotos/s | Faktor |
|---|---|---|
| Ausgangslage: `onnxruntime` ohne CUDA-Provider | 0,86 | — |
| eigene venv mit passendem `onnxruntime-gpu`, CLIP in fp16, Text-Embeddings gebündelt | 3,31 | 3,8× |
| JPEG einmal statt dreimal dekodieren | 3,54 | |
| EXIF aus dem bereits geöffneten Bild | 4,04 | |
| insightface auf die zwei genutzten Modelle beschränkt | 4,15 | |
| **Fließband mit 6 Lesern** (`ingest/parallel.py`) | **17,7** | **20,6×** |

50 000 Fotos → **rund 47 Minuten**.

## Wo die Zeit blieb

Der entscheidende Befund war nicht, dass etwas langsam rechnete, sondern dass
fast alles wartete. Sequenziell, gemittelt über alle 2162 Fotos:

| Ressource | Stufen | ms/Foto |
|---|---|---|
| SMB + CPU | Dateizeiten, Dekodieren, EXIF, Ordner | ~200 |
| GPU | Gesichter, CLIP | 79 |
| Netzwerk | Text-Embedding, Qdrant | 35 |

Die 5090 stand also zwei Drittel der Zeit still — im Taskmanager sichtbar als
nominell 65 % „Auslastung" bei **130 W von 550 W**. Dieser Wert misst, ob
*irgendein* Kernel läuft, nicht ob die Karte rechnet. Viele winzige Aufrufe
sehen aus wie Last.

Warum Parallelität hier so viel bringt, zeigt die Einzelmessung:

| | seriell | parallel | Faktor |
|---|---|---|---|
| SMB-Lesen | 66,6 ms | 8,0 ms (4 Threads) | 8,4× |
| JPEG-Dekodieren | 165,6 ms | 23,3 ms (8 Threads) | 7,1× |
| Text-Embedding | 120,9 ms | 21,5 ms (Batch 32) | 5,6× |

SMB ist latenz-, nicht bandbreitengebunden, und PIL gibt beim Dekodieren den
GIL frei. Beides skaliert dadurch fast linear.

Nach dem Umbau ist die GPU der Engpass (50 ms von 59 ms pro Foto) — das ist
der Zustand, den man haben will.

---

## Fallstricke

### `onnxruntime-gpu` muss zur CUDA-Version von torch passen

Die aktuelle Version erwartet CUDA 13, torch liefert 12.8 mit. Der
CUDA-Provider lädt dann nicht und **fällt still auf die CPU zurück** — die
Gesichtserkennung kostet 860 statt 30 ms, ohne dass irgendwo ein Fehler
erscheint. Passend ist `onnxruntime-gpu==1.22`.

Zusätzlich findet onnxruntime die CUDA-Bibliotheken nicht, die torch als
pip-Pakete unter `nvidia/*/lib` mitbringt; sie werden in
`face_embedder._preload_cuda_libs()` vorab geladen. `FaceEmbedder` warnt
inzwischen laut, wenn der Provider fehlt — vorher merkte man es nur an der
Laufzeit.

### insightface rechnet drei Modelle umsonst

`buffalo_l` bringt fünf Modelle mit. Gebraucht werden zwei: `detection`
(Boxen und Landmarks) und `recognition` (das 512d-Embedding). `landmark_3d_68`,
`landmark_2d_106` und `genderage` laufen ohne `allowed_modules` bei **jedem
Gesicht** mit, und ihr Ergebnis wird nirgends gelesen. Auf einem Gruppenfoto
mit 20 Gesichtern sind das 60 nutzlose Inferenzen.

Isoliert gemessen: 182 → 75 ms pro Foto, Faktor 2,4. Landmarks und Embedding
bleiben erhalten, die Frontalitätsmessung funktioniert weiter.

### CPU-Arbeit gehört nicht in den GPU-Thread

Zwei Posten haben dort zunächst gesteckt und ihn um Faktor drei ausgebremst:
die BGR-Konvertierung (bei 12 MP eine Array-Kopie von 36 MB) und das
CLIP-Preprocessing (Skalierung auf 224×224). Beides erledigt jetzt der
Leser-Pool und reicht das Ergebnis durch.

Faustregel: Im GPU-Thread steht nur Inferenz.

### Ein defektes Bild darf nicht den Stapel mitreißen

`torch.stack` über einen Batch scheitert komplett, wenn ein einziges Element
nicht passt. Dadurch gingen **56 von 2162 Fotos still verloren** — 3,5 Batches
à 16. Aufgefallen ist es nur, weil die Fehlerzahl bei 3 und bei 6 Workern
*exakt gleich* war; ein Race Condition hätte gestreut.

Zwei Lehren: Problematische Elemente vor dem Stapeln herausfiltern, und ein
Foto ohne lesbares Bild trotzdem mit Datum, Album und Pfad indizieren, statt
es zu verwerfen.

### Mehr Worker sind nicht besser

Der GIL kostet gemessen 1,2–1,4×, wenn nebenher dekodiert wird. Über etwa
6 Lesern bremst diese Konkurrenz den GPU-Thread stärker, als die zusätzlichen
Leser einbringen. `--workers 6` ist der Standard, `--workers 1` schaltet auf
den sequenziellen Ablauf zurück.

### Warteschlangen müssen begrenzt sein

Ein dekodiertes Bild belegt 5–20 MB. Ohne Deckel liest der Leser-Pool dem
GPU-Worker davon und der Speicher läuft voll. `IMAGE_QUEUE = 24` bremst die
schnelleren Stufen automatisch aus. Die Bilddaten werden freigegeben, sobald
die GPU-Stufe fertig ist — nicht erst beim Schreiben.

### `--limit N` taugt nicht zum Messen

Der Scanner liefert alphabetisch sortiert. `--limit 260` trifft in diesem
Archiv genau die Abi-08-Bilder mit bis zu 72 Gesichtern pro Foto — dort kostet
die Gesichtserkennung 230 statt 44 ms. Zwei Läufe mit unterschiedlichem
`--limit` sind nicht vergleichbar, und ein `--limit`-Lauf ist nicht
repräsentativ für das Archiv.

Vergleichsmessungen laufen über den kompletten Bestand.

### KV-Cache: rechnen, nicht `size_vram` glauben

Ollamas `size_vram` aus `/api/ps` **zählt den KV-Cache nicht mit**. Über eine
16-fache Kontextänderung (8k → 128k) bewegte sich das Feld von 16 478 auf
16 894 MiB — 400 MB, während der Cache real um über 4 GB wächst. Wer damit
plant, verschätzt sich um eine Größenordnung.

Die Größe folgt der Standardformel; nur muss man die Architektur kennen:

    S_KV = 2 · L · h_KV · d_h · b   [Bytes pro Token]

`qwen3.8:27b` ist **kein reiner Transformer**. Das GGUF meldet
`full_attention_interval = 4` neben `ssm.*`-Parametern: von 64 Layern machen
nur **16** echte Attention, die übrigen 48 sind SSM-Layer mit konstantem,
kontextunabhängigem State. Mit `h_KV = 4`, `key_length = 256` und q8_0
(1,0625 B/Wert inkl. fp16-Skalierung je 32er-Block):

    S_KV = 2 · 16 · 4 · 256 · 1,0625 = 34 816 B = 34 KiB/Token

| Kontext | KV-Cache |
|---|---|
| 8 192 | 272 MiB |
| 32 768 | 1,06 GiB |
| 131 072 | 4,25 GiB |

Zum Vergleich: ein klassisches Mistral-7B liegt bei 128 KiB/Token. Ein Viertel
davon kommt hier aus drei Richtungen — GQA 6:1 statt 4:1, halb so viele
Attention-Layer durch die Hybrid-Architektur, und q8_0 statt FP16. Die doppelte
Head-Dimension zahlt einen Teil zurück.

### `num_parallel` — bei qwen3.8 wirkungslos, bei gemma4 wirkungsarm

**qwen3.8 lehnt Parallelität ab.** Im Ollama-Log:

    level=WARN source=sched.go:509
      msg="model architecture does not currently support parallel requests"
      architecture=qwen35
    srv load_model: initializing, n_slots = 1, n_ctx_slot = 8192

`OLLAMA_NUM_PARALLEL=16` wird gesetzt, im Container-Env bestätigt — und dann
verworfen. Ollama 0.32.14 legt für die Hybrid-Architektur `qwen35` genau einen
Slot an, weil der rekurrente SSM-State mehrere Sequenzen nicht traegt. Das gilt
für `qwen3.8:27b` wie für `qwen3.6:5090-27b`.

**gemma4 bekommt echte Slots** — es ist dicht gebaut, `n_slots = 16` wird
angelegt. Der Speicher ist dabei günstig, weil 25 der 30 Layer mit Sliding
Window (1024) laufen und nur 5 den vollen Kontext halten:

    llama_kv_cache_iswa: non-SWA KV: 340 MiB (8192 cells,  5 layers, 4/4 seqs)
    llama_kv_cache_iswa:     SWA KV: 850 MiB (2048 cells, 25 layers, 4/4 seqs)

Macht 298 MiB pro Slot, exakt linear skalierend, ohne rekurrenten State.

**Nur bringt es fast nichts.** Gemessen mit vorkodierten Payloads, 48 Bilder:

| Slots | Fotos/min | Tokens/s | Median-Latenz | GPU | Leistung |
|---|---|---|---|---|---|
| 1 | 35,0 | 48 | 1,3 s | 41 % | 195 W |
| 4 | **47,4** | **65** | 2,8 s | 33 % | 189 W |
| 8 | 45,2 | 61 | 6,0 s | 31 % | 206 W |
| 16 | 42,8 | 58 | 15,9 s | 30 % | 183 W |

Der Durchsatz erreicht bei 4 Slots ein Plateau von rund 65 Tokens/s und faellt
darueber wieder ab, waehrend die Latenz sich verachtfacht. Und der 1-Slot-Wert
taeuscht nach unten: die erste Anfrage kostet 19,9 s (Aufwaermen des
Vision-Encoders), die restlichen 47 laufen mit 1,33 s. Im eingeschwungenen
Zustand sind das 45,2 Fotos/min — **fuenf Prozent unter der besten
Parallelkonfiguration**.

Entscheidend ist die letzte Spalte: **in jeder Konfiguration liegt die GPU bei
30–41 % und 183–206 W.** Weder Speicherbandbreite noch Rechenwerk sind der
Engpass. Bei Bild-Captioning serialisiert der multimodale Pfad — llama.cpp
verarbeitet Bilder im mtmd-Pfad einzeln, nicht als Stapel — und mehr Slots
verlaengern nur die Warteschlange davor.

**Konsequenz:** `num_parallel` ist fuer diesen Job kein Hebel. Bei qwen3.8 ist
er nicht vorhanden, bei gemma4 bringt er fuenf Prozent. Client-seitige
Concurrency von 2–4 holt bei qwen3.8 dagegen 19,1 → 25,8 Fotos/min, weil sie
JPEG-Kodierung und Netzwerkweg mit der Serverrechnung ueberlappt.

Ein Wort zur Modellwahl: gemma4 ist mit 47 gegen 26 Fotos/min schneller, leistet
pro Foto aber auch weniger (333 statt 830 Prompt-Tokens, 81 statt 136 Ausgabe-
Tokens). Ob es die Namensregeln aus `captioner.py` genauso zuverlaessig befolgt,
ist ungeprueft — ein Wechsel braucht eine eigene Stichprobe auf Halluzinationen.

### Der Caption-Lauf: was hilft und was nicht

Gemessen an 48 Fotos, komplette Pipeline, qwen3.8:

| `--caption-workers` | Laufzeit |
|---|---|
| 1 | 146,2 s |
| 4 | 145,2 s |

**Kein Unterschied.** Der Grund steht in `parallel.py`: `finish()` laeuft in
jedem der `write_workers` Threads, und das sind bei `--workers 6` drei. Die
Pipeline stellte Ollama also immer schon drei gleichzeitige Anfragen -- ziemlich
genau das gemessene Optimum. Ein Pool obendrauf treibt die Zahl auf zwoelf und
verlaengert nur die Warteschlange. Der Standard ist deshalb `1`.

Wer aus einem Client-Benchmark einen Pipeline-Gewinn ableitet, muss die
Nebenlaeufigkeit vergleichen, die dort schon vorhanden ist. Der Benchmark
mass 19,1 -> 25,8 Fotos/min gegen einen einthreadigen Client; die Pipeline war
nie einthreadig.

**Was tatsaechlich hilft**, ist das Caption-JPEG im Leser-Pool zu erzeugen statt
im Caption-Schritt: sonst wird dieselbe Datei ein zweites Mal ueber SMB gelesen
und dekodiert. Kosten dort 33,6 ms statt rund 200 ms. In der Wanduhr sieht man
es nicht, weil Ollama die Grenze setzt -- die Arbeit faellt trotzdem weg.

**Das Modell muss mit der richtigen Kontextgroesse geladen sein.** Der erste
Lauf brauchte 155 s fuer 16 Fotos, der zweite 56 s -- identischer Code,
identische Daten. Der Unterschied war allein der Reload von 128k auf die 8192,
die `CAPTION_NUM_CTX` anfordert. `_warm_caption_model()` erledigt das jetzt
einmal kontrolliert beim Start und heftet danach den Embedder wieder an, den
der Scheduler beim Reload aus dem VRAM wirft.

### Kontextgroesse wird vorab allokiert

Die Frage, ob ein kleinerer Kontext wirklich Speicher freigibt, beantwortet der
Allocator beim Laden -- vor dem ersten Token:

    llama_kv_cache:         4352.00 MiB (131072 cells, 16 layers)  K/V q8_0
    llama_kv_cache:          512.00 MiB (131072 cells,  1 layer)   K/V f16
    llama_memory_recurrent:  598.50 MiB (konstant)

Die 4 352 MiB sind auf die Megabyte genau die berechneten 34 KiB/Token. Dazu
kommt ein zweiter kontextabhaengiger Cache fuer den Lightning Indexer.
Kontextabhaengig sind damit 4 864 MiB bei 128k gegen 304 MiB bei 8192; gemessen
belegt Ollama 29 915 statt 25 673 MiB.

**Zwei gleichwertige Wege, den Speicher freizubekommen.** Entweder ein eigenes
Profil (`qwen3.8:27b-ctx8k`) und das 128k-Profil vorher entladen, oder dasselbe
Profil weiterverwenden und `num_ctx` pro Anfrage senken. Beide laden die
Gewichte neu, beide geben dieselben 4,6 GB frei.

Der Weg ueber ein eigenes Profil hat zwei Vorteile: die Absicht ist in
`ollama ps` sichtbar, und ein gleichzeitig laufender Coding-Agent auf dem
128k-Tag loest kein staendiges Hin-und-Her-Laden derselben Modell-ID aus.
Wichtig dabei ist nur, das 128k-Profil wirklich zu entladen
(`ollama stop qwen3.8:27b-ctx128k`) -- zwei residente Tags waeren zwei Kopien
der 16,5 GB Gewichte.

    PHOTOVAULT_CAPTION_MODEL=qwen3.8:27b-ctx8k
    PHOTOVAULT_CAPTION_NUM_CTX=0     # sonst ueberschreibt der Captioner das Profil

Die zweite Variable ist der Fallstrick: ohne sie schickt der Captioner weiterhin
`num_ctx: 8192` mit, und ein `ctx16k`-Profil wuerde bei jeder Anfrage genau den
Reload ausloesen, den es vermeiden sollte. `0` heisst "kein `num_ctx` senden --
es gilt, was das Profil sagt".

**Warum der Caption-Lauf den kleinen Kontext braucht.** Nicht, weil grosser
Kontext langsam waere -- sondern weil er den Platz wegnimmt. Gemessen, 8 Fotos,
im Wechsel und reproduzierbar unter 1 % Streuung:

| Kontext waehrend des Laufs | Laufzeit | `w:caption` |
|---|---|---|
| 8192 | 47,3 / 46,8 s | 3,1 / 3,0 s/Foto |
| 131072 | 167,0 / 166,3 s | 17,1 / 17,5 s/Foto |

**Faktor 3,5.** Die Gegenprobe trennt die Ursachen: dieselben Captions bei
131072, aber ohne die Pipeline -- also ohne insightface und CLIP auf der Karte
-- laufen mit **2,68 s/Foto**, so schnell wie bei 8192. Die Kontextgroesse
selbst kostet nichts.

Es ist reine Speicherkonkurrenz. Bei 128k belegt Ollama rund 21 GB, dazu der
Embedder mit 3,4 GB; die Pipeline legt im selben Durchgang insightface
(~1,5 GB) und CLIP fp16 (~0,9 GB) dazu. Gemessenes Maximum 31 278 von
32 607 MiB. Bei 8192 sind 6,8 GB frei und alles laeuft normal.

Fuer jeden Lauf, der Captions *und* Gesichter/CLIP macht, ist der kleine
Kontext also nicht Feinschliff, sondern Voraussetzung.

### Prompt-Caching greift bei dieser Architektur nicht

Bei jeder Anfrage steht im Log:

    forcing full prompt re-processing due to lack of cache data
      (likely due to SWA or hybrid/recurrent memory)

Der fuer alle Fotos identische Regelblock im Caption-Prompt wird also jedes Mal
neu verarbeitet. Bei rund 0,2 s Prefill von 2,9 s kein grosser Posten, aber ein
Argument dafuer, den festen Teil des Prompts knapp zu halten.

**Realistischer Durchsatz:** 3,0 s/Foto, also rund **110 Minuten** fuer die
2 162 Fotos des Testbestands.

### Der Caption-Lauf gehoert nicht in die Pipeline

Aus den Messungen oben folgt eine Entwurfsaenderung: `ingest/caption_pass.py`
zieht Captions nach, **ohne insightface und CLIP zu laden**. Es liest die Fotos
aus Qdrant, denn Gesichtszahl, bestaetigte Namen und CLIP-Tags stehen dort
laengst; nur die Bilddatei muss noch einmal vom NAS.

Damit loest sich der Konflikt vollstaendig auf:

- **Kein `num_ctx`** mehr von Photovault (`num_ctx=0` ist der Standard des
  Moduls) → kein Reload, das geladene Modell bleibt unangetastet, OpenClaw
  behaelt seine 128k. Verifiziert: nach einem Lauf steht Ollama unveraendert
  auf 131 072.
- **Keine Speicherkonkurrenz**, weil die GPU-Modelle gar nicht geladen werden.
- **Wiederholbar auf Teilmengen** — `--person`, `--album`, `--path`. Nach einer
  Labeling-Runde nur die betroffenen Fotos neu beschriften zu lassen kostet
  Sekunden statt eines vollen Ingest.

Gemessen: **3,1 s/Foto** — dieselbe Groessenordnung wie in der Pipeline bei
8192, aber ohne deren Nebenwirkungen.

```bash
python -m ingest.caption_pass --dry-run --limit 20
python -m ingest.caption_pass --person "Annika Wolf"
```

Die Pipeline behaelt ihren eigenen Caption-Pfad fuer den Erstlauf ueber neues
Material, wo Gesichter und CLIP ohnehin gerechnet werden. Dort bleibt
`num_ctx 8192` richtig — dort **braucht** es den Platz.

#### Was der erste Praxislauf gezeigt hat

Zwei Fehler, die erst an echten Fotos sichtbar wurden:

**Der Prompt liess Namen unter den Tisch fallen.** Bei zwei Gesichtern und
*einem* bekannten Namen wich das Modell auf „Zwei Personen posieren“ aus,
obwohl `Tobias Krueger` im Kontext stand. Die Regel fuer den Teilfall fehlte
— der Prompt sagte nur, was bei *keinem* Namen zu tun ist. Mit der
ergaenzten Regel: „Tobias Krueger und eine weitere Person posieren im April
2008 …“

**Tags wuchsen unbegrenzt und doppelt.** CLIP schreibt `getraenke`, das LLM
`getränke` — beide landeten nebeneinander im Payload. `merge_tags()`
vergleicht jetzt umlautgefaltet und deckelt bei 16, sonst legt jeder erneute
Lauf ein paar neue Formulierungen obendrauf.

### Messfehler, die ich unterwegs gemacht habe

Zwei Thesen, die die Daten widerlegt haben, als Warnung:

*„Der Client bremst durch den GIL."* Das JPEG-Kodieren im Messthread schien der
Grund fuer die fallende Auslastung. Vorkodieren aller Bilder vor der Messung
zeigte: 1,1 s fuer 48 Bilder, 23 ms pro Bild, gegen 67 s Messdauer. Nie relevant.

*„VRAM laesst sich mit `nvidia-smi` messen."* Auf einem Arbeitsplatz nicht. Die
Baseline schwankte um mehrere hundert MB zwischen zwei Messungen, zwei Laeufe
derselben Konfiguration lieferten 2,9 und 5,3 GB. Belastbar sind die Zahlen, die
llama.cpp beim Laden selbst ausgibt.

### Was llama.cpp beim Laden selbst ausgibt

Die verlässlichste Quelle für die Speicheraufteilung ist nicht `nvidia-smi` und
nicht `size_vram`, sondern der Allocator im Container-Log:

    llama_kv_cache: size = 272.00 MiB (8192 cells, 16 layers, 1/1 seqs),
                    K (q8_0): 136.00 MiB, V (q8_0): 136.00 MiB
    llama_memory_recurrent: size = 598.50 MiB (64 layers, 1 seqs),
                    R (f32): 22.50 MiB, S (f32): 576.00 MiB

Zwei Dinge daran sind wichtig. Erstens bestätigt es die Rechnung oben exakt:
272 MiB bei 8 192 Token über 16 Layer sind genau die berechneten 34 KiB/Token,
und die 16 Attention-Layer aus `full_attention_interval = 4` stehen dort
ausgeschrieben. Zweitens ist der **rekurrente State mit 598 MiB pro Sequenz
mehr als doppelt so groß wie der KV-Cache** — eine überschlägige Rechnung aus
den `ssm.*`-Parametern lag hier um Faktor 4 zu niedrig.

Hätte Ollama 16 Slots angelegt, wären es 16 × (272 + 598) MiB = **13,6 GiB**
gewesen. Zusammen mit 16,5 GiB Gewichten und 3,4 GiB für den Embedder passt
das nicht auf eine 32-GB-Karte. Der Wert 16 wäre also auch dann nicht gegangen;
8 wäre die Obergrenze gewesen.

### VRAM messen geht auf diesem Rechner nicht

Baseline-Subtraktion über `nvidia-smi` ist auf einem Arbeitsplatz wertlos:
Chrome, Teams, ComfyUI und im Zweifel ein laufendes Spiel verschieben die
Baseline um mehrere hundert MB zwischen zwei Messungen. Zwei Läufe derselben
Konfiguration lieferten 2,9 und 5,3 GB für dieselbe Größe. Die theoretische
Rechnung ist hier die belastbarere Quelle, nicht die Messung.

### Der Kontext-Wechsel kostet einen Reload

`num_ctx` wirkt pro Anfrage — der Server lädt das Modell aber neu, wenn der
Wert vom geladenen abweicht. Das kostet **80–112 Sekunden** und verdrängt
nebenbei andere Modelle aus dem VRAM. Ein Job, der zwischen 8k und 128k
wechselt, zahlt das jedes Mal.

### Was ein Re-Ingest zerstören kann

`upsert` ersetzt in Qdrant das komplette Payload. Ohne Schutz löscht ein
Routinelauf alles, was nicht aus der Datei rekonstruierbar ist:

- am Foto: `caption_de`, `caption_locked`, `annotations`, `person_ids`, `person_names`
- an jedem Gesicht: `person_id`, `person_name`

Das zweite ist das teuerste — mehrere tausend von Hand zugeordnete Gesichter.
Beides wird vor dem Schreiben gelesen und zurückgeschrieben; Tests decken es ab.

### `Path.exists()` wirft bei totem Netzpfad

Ein abgestandener SMB-Mount gibt bei `exists()` nicht `False` zurueck, sondern
`OSError: Host is down`. Der Aufruf stand im Folder-Parser *vor* dem try-Block
und riss damit den kompletten Datensatz mit -- bei 50k Fotos ueber Netz reicht
dafuer ein kurzer Aussetzer. Aufgefallen ist es nur, weil zwei Tests
nebenbei ueber den echten Mount liefen.

Direkt oeffnen und `FileNotFoundError` fangen ist robuster und spart einen
Syscall pro Ebene.

### Die Netzfreigabe darf kurz weg sein

Startet der SMB-Dienst der NAS neu, liefert der WSL-Mount fuer einige Sekunden
`EHOSTDOWN` (errno 112) und kommt dann von selbst zurueck. `drvfs` ist kein
Linux-SMB-Client, sondern ein Durchreicher zum Windows-Redirector: der
verbindet sich neu, aber Aufrufe **waehrend** des Fensters scheitern hart,
statt zu warten.

Ohne Wiederholung kostet das bei einem Lauf ueber Stunden dutzende Fotos, die
anschliessend als "unlesbar" gelten. `ingest/netfs.py` wiederholt darum mit
1/2/4 s -- aber nur Transportfehler. Eine fehlende oder kaputte Datei ist
endgueltig und wird sofort durchgereicht, sonst wartet der Lauf sieben Sekunden
auf etwas, das es nicht gibt.

Wer das grundsaetzlich loesen will, mountet nativ statt durchzureichen: `cifs`
ist per Default `hard` und blockiert bis der Server zurueck ist. Vorbehalte und
fstab-Zeile stehen in der Vault-Notiz `TrueNAS-SMB`.

### Stille Fehlerbehandlung verdeckt tote Features

Zwei Kernfeatures waren funktionsunfähig, ohne dass es auffiel: `qdrant-client`
1.19 hat `search()` entfernt, und sowohl die semantische Suche als auch der
Face-Match fingen die `AttributeError` ab und lieferten ein leeres Ergebnis.
Die Suche meldete „0 Treffer" statt eines Fehlers.

Messbar war es nur indirekt — die Stufe `face_match` brauchte für 260 Fotos
0,03 Sekunden.

---

## Messen

Jeder Lauf gibt am Ende eine Zeitverteilung aus. Im Parallelmodus addieren
sich die Stufenzeiten auf mehr als die Laufzeit — sie laufen ja gleichzeitig.
Genau das ist die Aussage: Wo viel Zeit anfällt, die nicht die Wanduhr kostet,
arbeitet das Fließband.

```bash
python -m ingest.pipeline --source /mnt/photo/Fotos --skip-caption --no-resume \
  --workers 6 --gpu-batch 16
```

Zur Gegenprobe der sequenzielle Ablauf mit `--workers 1`.
