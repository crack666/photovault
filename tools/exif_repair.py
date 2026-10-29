"""Fehlende Aufnahmezeiten ins EXIF der Originaldateien schreiben.

WhatsApp und die meisten Weiterleitungen strippen EXIF. Das Datum steckt dann
nur im Dateinamen, die Uhrzeit in der Dateizeit -- PhotoVault leitet beides ab,
aber jeder andere Betrachter zeigt die Datei weiter als undatiert, und jeder
kuenftige Scan muss dieselbe Ableitung wiederholen.

Dieses Werkzeug schreibt den bereits ermittelten Zeitstempel zurueck. Es
arbeitet ueber den Index und nicht ueber das Dateisystem, damit exakt dieselbe
Logik gilt wie beim Ingest -- eine zweite, leicht abweichende Ableitung waere
schlimmer als keine.

Der Index haelt die Uhrzeit in UTC (`...Z`). Geschrieben wird sie in Ortszeit
mit `OffsetTimeOriginal` -- das erledigt `write_capture_time`, sobald der Wert
eine Zone traegt (Issue #43). Die Herkunftsnotiz sagt `src=filename+mtime`,
wenn nur der Tag aus dem Namen stammt und die Uhr aus der Dateizeit.

Kennt der Dateiname nur den Tag und faellt die Dateizeit in Ortszeit nicht
auf diesen Tag (oder ist gar keine Uhr bekannt), wird 12:00:00 Ortszeit
geschrieben, mit `src=filename+noon`: Mittag liegt in jeder Zeitzone auf
demselben Tag, Mitternacht kippt beim naechsten Umrechnen auf den Vortag.

Sicherheiten: Trockenlauf ist der Standard; vorhandene Aufnahmezeiten werden
nie angetastet; jeder geschriebene Wert traegt eine Herkunftsnotiz und ist
ueber `ingest.exif_writer.revert` umkehrbar.

    python -m tools.exif_repair                 # Trockenlauf (schreibt nichts)
    python -m tools.exif_repair --preview       # nur zaehlen, nach Herkunft
    python -m tools.exif_repair --folder "WhatsApp Images" --folder Sent
    python -m tools.exif_repair --apply --limit 200
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timezone

from ingest.exif_writer import (
    WRITABLE_SUFFIXES,
    ExifWriteError,
    read_capture_time,
    write_capture_time,
)
from ingest.localtime import parse_utc, to_local
from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256

#: Herkuenfte, bei denen nur der *Tag* abgeleitet ist. Die Uhrzeit kommt dann
#: aus der Dateizeit (`Normalizer._set_taken_at`), und die Notiz sagt das.
#: `file_time` nennt die Dateizeit schon selbst; `accepted`/`offset` sind
#: ganze Zeitpunkte.
CLOCK_FROM_MTIME = frozenset({"filename", "folder", "folder_name", "folder_json", "album"})


#: Notiz fuer "Tag aus dem Namen, Uhr unbekannt, 12:00:00 Ortszeit gesetzt".
NOON_SOURCE = "filename+noon"
NOON = time(12, 0, 0)

#: Ein Name, der selbst eine Uhrzeit traegt (`20130515_223527`). Dem wird
#: kein Mittag untergeschoben -- er kennt mehr als den Tag; ob seine Uhr
#: gilt, ist eine eigene Frage.
_RE_NAME_WITH_CLOCK = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-_]?(?:0[1-9]|1[0-2])[-_]?(?:0[1-9]|[12]\d|3[01])"
    r"[-_ T]?(?:[01]\d|2[0-3])[-_.:]?[0-5]\d[-_.:]?[0-5]\d(?!\d)"
)


def note_source(date_source: str | None) -> str:
    """Herkunft fuer die Notiz: `filename+mtime` statt `filename`."""
    src = date_source or "abgeleitet"
    return f"{src}+mtime" if src in CLOCK_FROM_MTIME else src


def _parse_taken_at(value: str | None) -> datetime | None:
    """Nur Zeitstempel mit echter Uhrzeit. Mitternacht heisst hier: unbekannt.

    Mit Zone (`Z`) kommt eine bewusste UTC-Zeit zurueck -- der Schreiber
    rechnet sie in Ortszeit um. Ohne Zone bleibt sie, wie sie ist.
    """
    if not value or len(value) < 19:
        return None
    if value[11:19] == "00:00:00":
        return None
    aware = parse_utc(value)
    if aware is not None:
        return aware.astimezone(timezone.utc)
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def in_folders(payload: dict, folders: set[str] | None) -> bool:
    """Ordnerschranke.

    Ohne Angabe gilt das ganze Archiv. Mit Angabe zaehlt nur, was in einem der
    genannten Ordner liegt -- gedacht fuer Gruppen, deren Herkunft *einheitlich*
    belastbar ist. `WhatsApp Images` ist so ein Fall: der Dateiname
    (`IMG-20181021-WA0081`) nennt den Tag verlaesslich. In einem gemischten
    Ordner steht neben so einem Namen auch mal ein `DSC_0001`, dessen Datum
    geraten waere. Der Filter ist deshalb kein Komfort, sondern die Grenze
    zwischen belegt und vermutet.

    Verglichen wird gegen `folder_name`, denselben Wert, den `--preview`
    auflistet -- was dort steht, laesst sich hier eintippen.
    """
    if not folders:
        return True
    return (payload.get("folder_name") or "") in folders


def noon_of(path: str, payload: dict) -> datetime | None:
    """12:00:00 Ortszeit am Tag aus dem Dateinamen -- wo das die ehrliche Uhr ist.

    Nur fuer `date_source=filename` mit vollem Tag, und nur, wenn der Name
    keine eigene Uhrzeit traegt. Ein Ordnerdatum (`folder`, oft nur ein Jahr
    als 1. Januar) bekommt keinen Mittag: dort waere schon der Tag erfunden.
    Ohne Zone, also Ortszeit; der Schreiber setzt dann kein Offset.
    """
    if payload.get("date_source") != "filename":
        return None
    day = str(payload.get("date") or "")
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None
    if _RE_NAME_WITH_CLOCK.search(os.path.basename(path)):
        return None
    return datetime.combine(d, NOON)


def candidate(payload: dict) -> tuple[str, datetime, str] | None:
    """Kommt dieses Foto in Frage? Gibt `(pfad, zeitstempel, herkunft)` zurueck.

    Die Uhr kommt aus der Dateizeit, wenn sie in Ortszeit auf den Tag des
    Namens faellt. Sonst -- oder ganz ohne Uhr (Mitternacht im Index) -- gilt
    bei einem Tag aus dem Dateinamen 12:00:00 Ortszeit (`noon_of`), bei allen
    anderen Herkuenften wird nichts geschrieben.
    """
    path = payload.get("file_path")
    if not path or os.path.splitext(path)[1].lower() not in WRITABLE_SUFFIXES:
        return None
    # Aus EXIF stammende Zeiten stehen schon in der Datei.
    if payload.get("date_source") == "exif":
        return None
    when = _parse_taken_at(payload.get("taken_at"))
    # Der Tag in der Datei muss der Tag des Namens sein. Nahe Mitternacht
    # liegt UTC auf dem Vortag; in Ortszeit muss es stimmen.
    day = str(payload.get("date") or "")
    off_day = (when is not None and len(day) == 10 and when.tzinfo is not None
               and to_local(when).date().isoformat() != day)
    if when is None or off_day:
        noon = noon_of(path, payload)
        return (path, noon, NOON_SOURCE) if noon is not None else None
    return path, when, note_source(payload.get("date_source"))


def preview(client, collection: str = "photos", limit: int | None = None,
            folders: set[str] | None = None) -> Counter:
    """Index-Sicht: wer kaeme in Frage, ohne die Dateien anzufassen."""
    by_src: Counter = Counter()
    by_folder: Counter = Counter()
    seen = 0
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "taken_at", "date", "date_source", "folder_name"],
            with_vectors=False,
        )
        for point in batch:
            seen += 1
            payload = point.payload or {}
            if not in_folders(payload, folders):
                continue
            hit = candidate(payload)
            if hit is None:
                continue
            by_src[hit[2]] += 1
            by_folder[payload.get("folder_name") or "?"] += 1
        if offset is None or (limit and seen >= limit):
            break
    schranke = f", eingeschraenkt auf {len(folders)} Ordner" if folders else ""
    print(f"{seen} Fotos im Index, {sum(by_src.values())} ohne Kameradatum "
          f"(date_source != exif, Uhrzeit bekannt oder Tag aus dem Namen, "
          f"JPEG/TIFF{schranke}).")
    print("Herkunft der Ableitung:")
    for k, n in by_src.most_common():
        print(f"  {n:6d}  {k}")
    print("Ordner (die ersten 20):")
    for k, n in by_folder.most_common(20):
        print(f"  {n:6d}  {k}")
    print("Kamera-EXIF bleibt unangetastet. Trockenlauf: "
          "python -m tools.exif_repair")
    return by_src


def _process(item, apply: bool) -> str:
    path, when, source = item
    try:
        has = retry_io(lambda: read_capture_time(path), what=path)
    except FileNotFoundError:
        return "Datei weg"
    except Exception as e:
        logger.debug("nicht lesbar (%s): %s", path, e)
        return "nicht lesbar"
    if has is not None:
        return "hat schon eine Zeit"
    try:
        out = retry_io(
            lambda: write_capture_time(path, when, source=source, dry_run=not apply),
            what=path,
        )
    except ExifWriteError as e:
        logger.warning("%s", e)
        return "Schreiben fehlgeschlagen"
    except Exception as e:
        logger.warning("%s: %s", path, e)
        return "Schreiben fehlgeschlagen"
    if out["written"]:
        return "geschrieben"
    return "waere geschrieben" if not apply else out["reason"]


def run(client, collection: str = "photos", apply: bool = False,
        workers: int = 8, limit: int | None = None,
        folders: set[str] | None = None) -> Counter:
    tally: Counter = Counter()
    offset = None
    seen = 0
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "taken_at", "date", "date_source", "folder_name"],
            with_vectors=False,
        )
        if not batch:
            break
        work = []
        for point in batch:
            seen += 1
            payload = point.payload or {}
            if not in_folders(payload, folders):
                tally["anderer Ordner"] += 1
                continue
            hit = candidate(payload)
            if hit is None:
                tally["uebersprungen"] += 1
                continue
            work.append(hit)

        if work:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for outcome in pool.map(lambda i: _process(i, apply), work):
                    tally[outcome] += 1

        if offset is None or (limit and seen >= limit):
            break
        logger.info("  %d geprueft", seen)

    logger.info("%s ueber %d Fotos:", "Angewandt" if apply else "Trockenlauf", seen)
    for key, n in tally.most_common():
        logger.info("   %-24s %6d", key, n)
    if not apply and tally.get("waere geschrieben"):
        logger.info("Mit --apply werden diese %d Dateien geaendert.",
                    tally["waere geschrieben"])
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Nur berichten, nichts schreiben (Standard).")
    mode.add_argument("--apply", action="store_true",
                      help="Tatsaechlich schreiben. Ohne das nur berichten.")
    parser.add_argument("--preview", action="store_true",
                        help="Nur zaehlen (Index), Dateien nicht oeffnen")
    parser.add_argument("--folder", action="append", dest="folders", metavar="NAME",
                        help="Nur diesen Ordner (mehrfach angebbar). "
                             "Namen wie in --preview.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    folders = set(args.folders) if args.folders else None

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    q = QdrantClient(url=args.qdrant_url)
    if args.preview:
        preview(q, collection=args.collection, limit=args.limit, folders=folders)
        return
    run(q, collection=args.collection,
        apply=args.apply, workers=args.workers, limit=args.limit, folders=folders)


if __name__ == "__main__":
    main()
