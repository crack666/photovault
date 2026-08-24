"""`taken_at` für bereits indizierte Fotos nachziehen.

Bis 2026-08-24 verwarf der Ingest die Uhrzeit: der EXIF-Extraktor schnitt sie
mit `strftime("%Y-%m-%d")` ab, der Normalizer baute den Zeitstempel aus dem
Tagesdatum neu. Jedes Foto stand damit auf Mitternacht -- und ohne Uhrzeit
lässt sich keine Serie von der nächsten trennen.

Dieses Werkzeug liest die Aufnahmezeit nach, ohne den gesamten Ingest zu
wiederholen: nur EXIF, keine Gesichtserkennung, kein CLIP, keine Vektoren.
Über den cifs-Mount kostet das rund 11 ms je Datei.

    python -m tools.backfill_taken_at --dry-run
    python -m tools.backfill_taken_at
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256


def exif_datetime(file_path: str) -> str | None:
    """Aufnahmezeit aus EXIF -- nur das, kein weiteres Feld."""
    from PIL import Image

    with Image.open(file_path) as img:
        exif = img.getexif()
        if not exif:
            return None
        raw = exif.get(306)
        if not raw:
            try:
                raw = exif.get_ifd(0x8769).get(36867)
            except Exception:
                raw = None
        if not raw:
            return None
    from datetime import datetime

    try:
        dt = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve(payload: dict) -> str | None:
    """Neuer Zeitstempel für ein Foto -- dieselbe Regel wie im Normalizer.

    EXIF zuerst, dann die Dateizeit, aber nur wenn ihr Tag zum ermittelten
    Datum passt. Sonst ist sie der Kopierzeitpunkt und täuscht eine Präzision
    vor, die es nicht gibt.
    """
    date = payload.get("date")
    if not date or len(date) != 10:
        return None
    path = payload.get("file_path")
    if path:
        try:
            got = retry_io(lambda: exif_datetime(path), what=path)
            if got and got[:10] == date:
                return got
        except FileNotFoundError:
            logger.debug("weg: %s", path)
        except Exception as e:
            logger.debug("EXIF nicht lesbar (%s): %s", path, e)
    mtime = payload.get("file_mtime")
    if mtime and str(mtime)[:10] == date and len(str(mtime)) >= 19:
        return f"{str(mtime)[:19]}Z"
    return None


def run(client, collection: str = "photos", workers: int = 8,
        dry_run: bool = False, limit: int | None = None) -> dict:
    stats = {"seen": 0, "from_exif": 0, "from_mtime": 0, "unchanged": 0, "updated": 0}
    started = time.time()
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["date", "file_path", "file_mtime", "taken_at"],
            with_vectors=False,
        )
        if not batch:
            break
        pairs = [(p.id, p.payload or {}) for p in batch]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fresh = list(pool.map(lambda item: resolve(item[1]), pairs))

        for (pid, payload), new in zip(pairs, fresh):
            stats["seen"] += 1
            if new is None or new == payload.get("taken_at"):
                stats["unchanged"] += 1
                continue
            # Woher der Wert kam, nur fuer die Statistik.
            mtime = str(payload.get("file_mtime") or "")
            stats["from_mtime" if new[:19] == mtime[:19] else "from_exif"] += 1
            if not dry_run:
                client.set_payload(collection_name=collection,
                                   payload={"taken_at": new}, points=[pid], wait=False)
            stats["updated"] += 1

        logger.info("  %d gesehen, %d neu", stats["seen"], stats["updated"])
        if offset is None or (limit and stats["seen"] >= limit):
            break

    elapsed = time.time() - started
    logger.info(
        "%s: %d Fotos, %d Zeitstempel neu (%d aus EXIF, %d aus Dateizeit), "
        "%d unveraendert -- %.0fs",
        "Trockenlauf" if dry_run else "Fertig", stats["seen"], stats["updated"],
        stats["from_exif"], stats["from_mtime"], stats["unchanged"], elapsed,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    run(QdrantClient(url=args.qdrant_url), collection=args.collection,
        workers=args.workers, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
