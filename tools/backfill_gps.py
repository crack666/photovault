"""GPS aus den Originaldateien in den Index nachziehen.

Der Extraktor hat Ref und Koordinaten vertauscht — jedes Smartphone-Foto
mit EXIF-Ort landete ohne `gps` im Payload. Die Lightbox-Karte blieb leer,
obwohl die JPEGs die Position tragen.

Liest nur EXIF, schreibt nur `gps` (und `location_source`, falls noch leer).
Kein Re-Ingest, keine Vektoren.

    python -m tools.backfill_gps --dry-run
    python -m tools.backfill_gps
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from ingest.exif_extractor import ExifExtractor
from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256
ext = ExifExtractor()


def gps_of(path: str) -> list[float] | None:
    got = ext.extract(path)
    gps = got.get("gps")
    if not gps or len(gps) < 2:
        return None
    return [float(gps[0]), float(gps[1])]


def run(client, collection: str = "photos", apply: bool = False,
        workers: int = 8, limit: int | None = None) -> Counter:
    tally: Counter = Counter()
    offset = None
    seen = 0
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "gps", "location_source"],
            with_vectors=False,
        )
        if not batch:
            break
        todo = []
        for point in batch:
            seen += 1
            payload = point.payload or {}
            if payload.get("gps"):
                tally["hatte schon gps"] += 1
                continue
            path = payload.get("file_path")
            if not path:
                tally["ohne pfad"] += 1
                continue
            todo.append((str(point.id), path, payload))
        if todo:
            def one(item):
                pid, path, payload = item
                try:
                    gps = retry_io(lambda: gps_of(path), what=path)
                except FileNotFoundError:
                    return "datei weg"
                except Exception as e:
                    logger.debug("gps %s: %s", path, e)
                    return "nicht lesbar"
                if not gps:
                    return "kein gps in der datei"
                if apply:
                    extra = {"gps": gps}
                    if not payload.get("location_source"):
                        extra["location_source"] = "exif_gps"
                    try:
                        client.set_payload(
                            collection_name=collection, payload=extra,
                            points=[pid], wait=False,
                        )
                    except Exception as e:
                        logger.warning("payload %s: %s", pid, e)
                        return "schreiben fehlgeschlagen"
                    return "geschrieben"
                return "waere geschrieben"

            with ThreadPoolExecutor(max_workers=workers) as pool:
                for outcome in pool.map(one, todo):
                    tally[outcome] += 1
        if offset is None or (limit and seen >= limit):
            break
    tally["gesehen"] = seen
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    apply = bool(args.apply) and not args.dry_run
    tally = run(QdrantClient(url=args.qdrant_url), args.collection, apply=apply,
                workers=args.workers, limit=args.limit)
    print(dict(tally))


if __name__ == "__main__":
    main()
