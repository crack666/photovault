"""Aus dem Index entfernen, was nicht mehr in der Quellenliste steht.

Die Auswahl in `sources.txt` ändert sich: ein Ordner kommt dazu, ein anderer
stellt sich als Screenshot-Halde heraus. Der Index folgt dem nicht von selbst
-- ein Ingest fügt nur hinzu. Ohne dieses Werkzeug bliebe alles liegen, was
einmal versehentlich aufgenommen wurde, und würde jede Suche verwässern.

Sicherheiten: Trockenlauf ist der Standard; von Hand vergebene Personen,
Notizen und Captions blockieren das Löschen, solange man nicht ausdrücklich
darauf besteht. Zu jedem Foto verschwinden auch seine Gesichtseinträge --
sonst bleiben verwaiste Vektoren zurück, die weiter als Vorschläge auftauchen.

    python -m tools.prune --sources-file sources.txt
    python -m tools.prune --sources-file sources.txt --apply
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter

logger = logging.getLogger(__name__)

BATCH = 512


def covered(path: str, sources: list[str], exclude: list[str]) -> bool:
    """Wuerde der Scanner dieses Foto heute aufnehmen?

    Nicht nur "liegt unter einer Quelle": es gelten dieselben Regeln wie beim
    Scan. Sonst bleibt liegen, was ein frueherer Lauf mit anderen Regeln
    aufgenommen hat -- etwa die 5119 Dateien aus Androids `.thumbnails`, das
    unter einer aktiven Quelle liegt und trotzdem nichts im Index zu suchen
    hat. Der Index soll genau das enthalten, was der Scanner heute liefert.
    """
    if any(path == e or path.startswith(e.rstrip("/") + "/") for e in exclude):
        return False
    root = next((s.rstrip("/") for s in sources
                 if path == s or path.startswith(s.rstrip("/") + "/")), None)
    if root is None:
        return False
    rest = path[len(root) + 1:].split("/")
    if any(part.startswith(".") for part in rest):
        return False
    from pathlib import Path

    from ingest.scanner import IMAGE_EXTENSIONS, SKIP_NAMES

    name = rest[-1] if rest else ""
    if name.lower() in SKIP_NAMES or Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    return True


def _blocking(payload: dict) -> str | None:
    """Grund, dieses Foto *nicht* zu loeschen -- oder None."""
    if payload.get("person_names") or payload.get("person_ids"):
        return "hat zugeordnete Personen"
    if payload.get("annotations"):
        return "hat Notizen"
    if payload.get("caption_locked"):
        return "hat eine von Hand geschriebene Caption"
    return None


def collect(client, collection: str, sources: list[str], exclude: list[str]):
    """`(zu_loeschen, geschuetzt, verteilung)` ermitteln."""
    doomed: list[tuple] = []
    protected: Counter = Counter()
    tops: Counter = Counter()
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["file_path", "photo_id", "person_names", "person_ids",
                          "annotations", "caption_locked"],
            with_vectors=False,
        )
        for point in batch:
            payload = point.payload or {}
            path = payload.get("file_path") or ""
            if not path or covered(path, sources, exclude):
                continue
            reason = _blocking(payload)
            if reason:
                protected[reason] += 1
                continue
            doomed.append((point.id, payload.get("photo_id"), path))
            parts = path.split("/")
            tops["/".join(parts[:4]) if len(parts) > 4 else path] += 1
        if offset is None:
            break
    return doomed, protected, tops


def delete_faces(client, faces_collection: str, photo_ids: list[str], apply: bool) -> int:
    """Gesichtseintraege der genannten Fotos entfernen.

    Ueber einen Filter statt ueber Punkt-IDs: die Gesichts-ID enthaelt die
    Box-Koordinaten, laesst sich also nicht aus dem Foto allein herleiten.
    """
    from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchAny

    total = 0
    for i in range(0, len(photo_ids), 128):
        chunk = [p for p in photo_ids[i:i + 128] if p]
        if not chunk:
            continue
        flt = Filter(must=[FieldCondition(key="photo_id", match=MatchAny(any=chunk))])
        try:
            total += client.count(faces_collection, count_filter=flt).count
            if apply:
                client.delete(collection_name=faces_collection,
                              points_selector=FilterSelector(filter=flt), wait=True)
        except Exception as e:
            logger.warning("Gesichter nicht entfernbar: %s", e)
    return total


def run(client, sources: list[str], exclude: list[str], collection: str = "photos",
        faces_collection: str = "faces", apply: bool = False, force: bool = False) -> dict:
    doomed, protected, tops = collect(client, collection, sources, exclude)

    print(f"Nicht mehr abgedeckt: {len(doomed)} Fotos")
    for name, n in tops.most_common(15):
        print(f"   {name[:56]:56s} {n:6d}")
    if len(tops) > 15:
        print(f"   ... und {len(tops) - 15} weitere Pfade")

    if protected:
        print("\nGeschuetzt (bleiben stehen):")
        for reason, n in protected.most_common():
            print(f"   {reason[:56]:56s} {n:6d}")
        if force:
            print("   --force ignoriert diesen Schutz nicht: dafuer waere ein")
            print("   eigener Lauf noetig, damit es keine Unfaelle gibt.")

    n_faces = delete_faces(client, faces_collection,
                           [pid for _, pid, _ in doomed], apply=apply)
    print(f"\nDazu {n_faces} Gesichtseintraege.")

    if not doomed:
        return {"photos": 0, "faces": 0}

    if apply:
        ids = [pid for pid, _, _ in doomed]
        for i in range(0, len(ids), 256):
            client.delete(collection_name=collection, points_selector=ids[i:i + 256], wait=True)
        print(f"Entfernt: {len(ids)} Fotos, {n_faces} Gesichter.")
    else:
        print(f"Trockenlauf -- mit --apply werden {len(doomed)} Fotos entfernt.")
    return {"photos": len(doomed), "faces": n_faces}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--faces-collection", default="faces")
    parser.add_argument("--sources-file", default="sources.txt")
    parser.add_argument("--apply", action="store_true", help="Tatsaechlich loeschen.")
    parser.add_argument("--force", action="store_true",
                        help="(reserviert -- schuetzt weiterhin markierte Fotos)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    from ingest.scanner import load_sources

    sources, exclude = load_sources(args.sources_file)
    print(f"Quellen laut {args.sources_file}:")
    for s in sources:
        print(f"   + {s}")
    for e in exclude:
        print(f"   - {e}")
    print()
    run(QdrantClient(url=args.qdrant_url), sources, exclude,
        collection=args.collection, faces_collection=args.faces_collection,
        apply=args.apply, force=args.force)


if __name__ == "__main__":
    main()
