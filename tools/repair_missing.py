"""Index-Punkte, deren Datei nicht mehr existiert.

Explorer-Rename ohne PhotoVault: der alte Pfad-Hash bleibt, ein Re-Ingest legt
einen neuen Punkt an. Eine Serie zeigt dann zwei Ordner, von denen einer tot
ist — wie `groemitz` neben `Groemitz 2009`.

Pro Geisterpunkt:

* Datei liegt unter einem Geschwisterordner **und** der neue Pfad ist schon
  indiziert → Duplikat, Geisterpunkt (plus Gesichter) weg.
* Datei liegt unter einem Geschwisterordner, noch nicht indiziert → Pfad
  nachziehen (`migrate_photo`).
* Nichts gefunden → wirklich verschwunden, Punkt weg.

    python -m tools.repair_missing
    python -m tools.repair_missing --apply
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from pathlib import Path

from ingest.identity import point_id_for_path

logger = logging.getLogger(__name__)

BATCH = 256


def counterpart_path(old_path: str) -> Path | None:
    """Eine eindeutige Datei gleichen Namens, wenn der alte Ordner weg ist."""
    src = Path(old_path)
    if src.is_file():
        return None
    parent = src.parent
    if parent.is_dir():
        return None
    grand = parent.parent
    if not grand.is_dir():
        return None
    hits = []
    try:
        siblings = list(grand.iterdir())
    except OSError:
        return None
    for sib in siblings:
        if not sib.is_dir():
            continue
        cand = sib / src.name
        if cand.is_file():
            hits.append(cand)
    if len(hits) == 1:
        return hits[0]
    return None


def classify(client, collection: str, path: str, point_id: str) -> str:
    """duplicate | migrate | gone."""
    if Path(path).is_file():
        return "live"
    dest = counterpart_path(path)
    if dest is None:
        return "gone"
    new_id = point_id_for_path(str(dest))
    found = client.retrieve(collection_name=collection, ids=[new_id], with_payload=False)
    if found:
        return "duplicate"
    return "migrate"


def collect(client, collection: str) -> dict[str, list]:
    buckets = {"duplicate": [], "migrate": [], "gone": [], "live": 0}
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "photo_id", "folder_name"],
            with_vectors=False,
        )
        for point in batch:
            path = (point.payload or {}).get("file_path") or ""
            if not path:
                continue
            if Path(path).is_file():
                buckets["live"] += 1
                continue
            kind = classify(client, collection, path, str(point.id))
            dest = counterpart_path(path)
            buckets[kind].append({
                "id": str(point.id),
                "photo_id": (point.payload or {}).get("photo_id"),
                "path": path,
                "folder": (point.payload or {}).get("folder_name"),
                "dest": str(dest) if dest else None,
            })
        if offset is None:
            break
    return buckets


def run(client, collection: str = "photos", faces: str = "faces",
        apply: bool = False) -> dict:
    from tools.prune import delete_faces

    buckets = collect(client, collection)
    dup = buckets["duplicate"]
    mig = buckets["migrate"]
    gone = buckets["gone"]
    print(f"Index lebendig: {buckets['live']}")
    print(f"Duplikate (alte Pfade, Datei unter neuem Ordner schon indiziert): {len(dup)}")
    for rec in dup[:15]:
        print(f"   {rec['folder'] or '?':20s}  {rec['path']}")
        print(f"      → {rec['dest']}")
    if len(dup) > 15:
        print(f"   … {len(dup) - 15} weitere")
    print(f"Zum Nachziehen (Datei liegt woanders, noch kein neuer Punkt): {len(mig)}")
    for rec in mig[:10]:
        print(f"   {rec['path']}  →  {rec['dest']}")
    print(f"Wirklich weg: {len(gone)}")
    for rec in gone[:10]:
        print(f"   {rec['path']}")

    doomed = dup + gone
    n_faces = delete_faces(
        client, faces, [r["photo_id"] for r in doomed], apply=apply,
    )
    migrated = 0
    if apply:
        if doomed:
            ids = [r["id"] for r in doomed]
            for i in range(0, len(ids), 256):
                client.delete(
                    collection_name=collection, points_selector=ids[i:i + 256], wait=True,
                )
        if mig:
            from ingest.relocate import migrate_photo
            from ingest.folder_parser import album_dir

            for rec in mig:
                dest = rec["dest"]
                folder = Path(dest).parent.name
                migrate_photo(
                    client, old_path=rec["path"], new_path=dest, folder_name=folder,
                )
                migrated += 1
        print(f"Entfernt: {len(doomed)} Fotos, {n_faces} Gesichter. Nachgezogen: {migrated}.")
    else:
        print(f"Trockenlauf — mit --apply: {len(doomed)} löschen, {len(mig)} nachziehen, "
              f"{n_faces} Gesichter.")
    return {
        "duplicate": len(dup),
        "migrate": len(mig),
        "gone": len(gone),
        "faces": n_faces,
        "migrated": migrated if apply else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--faces-collection", default="faces")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    run(QdrantClient(url=args.qdrant_url), collection=args.collection,
        faces=args.faces_collection, apply=args.apply)


if __name__ == "__main__":
    main()
