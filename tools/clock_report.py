"""Bericht über Kameras mit falsch gestellter Uhr.

Stellt nur fest, schreibt nichts. Grundlage für die Entscheidung, welche
Gruppe korrigiert werden soll.

    python -m tools.clock_report
    python -m tools.clock_report --album "18. Geburtstag (2006)"
"""
from __future__ import annotations

import argparse
import logging
import os

from ingest.clockcheck import by_camera, find

logger = logging.getLogger(__name__)


def load(client, collection: str, album: str | None = None) -> list[dict]:
    photos, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=512, offset=offset,
            with_payload=["folder_name", "taken_at", "exif", "file_path", "date_source"],
            with_vectors=False,
        )
        for p in batch:
            payload = dict(p.payload or {})
            payload["photo_id"] = str(p.id)
            if album and payload.get("folder_name") != album:
                continue
            photos.append(payload)
        if offset is None:
            break
    return photos


def report(photos: list[dict]) -> None:
    suspicions = find(photos)
    if not suspicions:
        print("Keine Verdachtsfaelle.")
        return

    total = sum(s.count for s in suspicions)
    print(f"{total} Fotos in {len(suspicions)} Gruppen fallen aus ihrem Album heraus.\n")

    for camera, group in by_camera(suspicions).items():
        n = sum(s.count for s in group)
        alben = len(group)
        mehrfach = " *** dieselbe Kamera in mehreren Alben" if alben > 1 else ""
        print(f"── {camera[:48]:48s} {n:4d} Fotos, {alben} Album/Alben{mehrfach}")
        for s in group:
            spanne = f"{min(s.observed):%Y-%m-%d} … {max(s.observed):%Y-%m-%d}"
            print(f"     {s.album[:30]:30s} {s.count:4d} Fotos  "
                  f"steht auf {spanne}  Album liegt bei {s.reference:%Y-%m-%d}")
            print(f"        {s.kind:16s} → {s.proposal()}")
        print()

    versatz = [s for s in suspicions if s.offset is not None]
    print(f"{sum(s.count for s in versatz)} Fotos mit konstantem Versatz "
          f"(exakt korrigierbar), "
          f"{total - sum(s.count for s in versatz)} zurueckgefallen "
          f"(nur Datum aus dem Album).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--album", help="Nur dieses Album pruefen")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    from qdrant_client import QdrantClient

    report(load(QdrantClient(url=args.qdrant_url), args.collection, args.album))


if __name__ == "__main__":
    main()
