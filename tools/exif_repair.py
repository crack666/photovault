"""Fehlende Aufnahmezeiten ins EXIF der Originaldateien schreiben.

WhatsApp und die meisten Weiterleitungen strippen EXIF. Das Datum steckt dann
nur im Dateinamen, die Uhrzeit in der Dateizeit -- PhotoVault leitet beides ab,
aber jeder andere Betrachter zeigt die Datei weiter als undatiert, und jeder
kuenftige Scan muss dieselbe Ableitung wiederholen.

Dieses Werkzeug schreibt den bereits ermittelten Zeitstempel zurueck. Es
arbeitet ueber den Index und nicht ueber das Dateisystem, damit exakt dieselbe
Logik gilt wie beim Ingest -- eine zweite, leicht abweichende Ableitung waere
schlimmer als keine.

Sicherheiten: Trockenlauf ist der Standard; vorhandene Aufnahmezeiten werden
nie angetastet; jeder geschriebene Wert traegt eine Herkunftsnotiz und ist
ueber `ingest.exif_writer.revert` umkehrbar.

    python -m tools.exif_repair                 # Trockenlauf (schreibt nichts)
    python -m tools.exif_repair --preview       # nur zaehlen, nach Herkunft
    python -m tools.exif_repair --apply --limit 200
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ingest.exif_writer import (
    WRITABLE_SUFFIXES, ExifWriteError, read_capture_time, write_capture_time,
)
from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256


def _parse_taken_at(value: str | None) -> datetime | None:
    """Nur Zeitstempel mit echter Uhrzeit. Mitternacht heisst hier: unbekannt."""
    if not value or len(value) < 19:
        return None
    if value[11:19] == "00:00:00":
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def candidate(payload: dict) -> tuple[str, datetime] | None:
    """Kommt dieses Foto in Frage? Gibt `(pfad, zeitstempel)` zurueck."""
    path = payload.get("file_path")
    if not path or os.path.splitext(path)[1].lower() not in WRITABLE_SUFFIXES:
        return None
    when = _parse_taken_at(payload.get("taken_at"))
    if when is None:
        return None
    # Aus EXIF stammende Zeiten stehen schon in der Datei.
    if payload.get("date_source") == "exif":
        return None
    return path, when


def preview(client, collection: str = "photos", limit: int | None = None) -> Counter:
    """Index-Sicht: wer kaeme in Frage, ohne die Dateien anzufassen."""
    by_src: Counter = Counter()
    by_folder: Counter = Counter()
    seen = 0
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "taken_at", "date_source", "folder_name"],
            with_vectors=False,
        )
        for point in batch:
            seen += 1
            payload = point.payload or {}
            if candidate(payload) is None:
                continue
            by_src[payload.get("date_source") or "abgeleitet"] += 1
            by_folder[payload.get("folder_name") or "?"] += 1
        if offset is None or (limit and seen >= limit):
            break
    print(f"{seen} Fotos im Index, {sum(by_src.values())} ohne Kameradatum "
          f"(date_source != exif, Uhrzeit bekannt, JPEG/TIFF).")
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
        workers: int = 8, limit: int | None = None) -> Counter:
    tally: Counter = Counter()
    offset = None
    seen = 0
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "taken_at", "date_source"], with_vectors=False,
        )
        if not batch:
            break
        work = []
        for point in batch:
            seen += 1
            payload = point.payload or {}
            hit = candidate(payload)
            if hit is None:
                tally["uebersprungen"] += 1
                continue
            path, when = hit
            work.append((path, when, payload.get("date_source") or "abgeleitet"))

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
    parser.add_argument("--apply", action="store_true",
                        help="Tatsaechlich schreiben. Ohne das nur berichten.")
    parser.add_argument("--preview", action="store_true",
                        help="Nur zaehlen (Index), Dateien nicht oeffnen")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    q = QdrantClient(url=args.qdrant_url)
    if args.preview:
        preview(q, collection=args.collection, limit=args.limit)
        return
    run(q, collection=args.collection,
        apply=args.apply, workers=args.workers, limit=args.limit)


if __name__ == "__main__":
    main()
