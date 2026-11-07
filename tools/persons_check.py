"""Belegt jedes Gesicht die Personen eines Fotos? Nur lesend.

Die Regel: Jede Person in `person_ids` eines Fotos braucht auf diesem Foto
ein Gesicht mit derselben `person_id`. Gesichter von Video-Bildern
(`frame_ss`) zaehlen mit, Pseudo-IDs (`_skipped` usw.) nicht. Fehlt der
Beleg, findet die Personensuche das Foto unter einem Namen, den kein Gesicht
traegt (photovault#57: 12 Fotos, gemessen 03.11.2026; am 07.11.2026
bereinigt, danach 0).

Ausgegeben werden nur Punkt-IDs und Zaehler, keine Namen -- die Liste darf
in ein Issue. Je Foto steht dabei, ob der Abgleich die fehlende Person
ohnehin vorschlaegt (`person_suggestions`): dann genuegt meist, das Gesicht
im Streifen zu bestaetigen. Sonst ist offen, ob die Person zu sehen ist.

    python -m tools.persons_check            # Exit 1, wenn etwas gefunden wird
    python -m tools.persons_check --ids      # nur die Punkt-IDs, eine je Zeile

Bereinigt wird hier nichts. Ob ein Name auf ein Gesicht gehoert oder vom
Foto weg (`sync_photo_persons`), entscheidet der Mensch je Foto.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

BATCH = 2048


def _real(pid: Any) -> bool:
    return bool(pid) and not str(pid).startswith("_")


@dataclass
class Unbelegt:
    point_id: Any
    face_source: str | None
    face_count: int
    #: Personen in `person_ids` ohne Gesicht -- nur gezaehlt, nicht genannt.
    missing: list[str] = field(default_factory=list)
    #: Davon schlaegt der Abgleich so viele fuer ein Gesicht vor.
    suggested: int = 0


def faces_by_photo(faces: Iterable[dict]) -> dict[str, set[str]]:
    """`photo_id` -> Personen, die ein Gesicht dieses Fotos traegt."""
    out: dict[str, set[str]] = defaultdict(set)
    for face in faces:
        pid = face.get("person_id")
        if _real(pid):
            out[face.get("photo_id")].add(pid)
    return out


def find_unbelegt(photos: Iterable[tuple[Any, dict]],
                  persons_on: dict[str, set[str]]) -> list[Unbelegt]:
    """Fotos, deren `person_ids` ein Gesicht fehlt.

    Ein Foto kann ueber `photo_uid` oder (aelter) `photo_id` an seinen
    Gesichtern haengen; beide zaehlen.
    """
    found: list[Unbelegt] = []
    for point_id, payload in photos:
        ids = [p for p in (payload.get("person_ids") or []) if _real(p)]
        if not ids:
            continue
        have: set[str] = set()
        for key in {payload.get("photo_uid"), payload.get("photo_id")} - {None}:
            have |= persons_on.get(key, set())
        missing = [p for p in ids if p not in have]
        if not missing:
            continue
        sugg = set(payload.get("person_suggestions") or [])
        found.append(Unbelegt(
            point_id=point_id,
            face_source=payload.get("face_source"),
            face_count=int(payload.get("face_count") or 0),
            missing=missing,
            suggested=sum(1 for p in missing if p in sugg),
        ))
    return found


def _scroll(client, collection: str, fields: list[str]):
    offset = None
    while True:
        batch, offset = client.scroll(collection_name=collection, limit=BATCH, offset=offset,
                                      with_payload=fields, with_vectors=False)
        for point in batch:
            yield point.id, (point.payload or {})
        if offset is None:
            return


def check(client, collection: str = "photos", faces_collection: str = "faces") -> list[Unbelegt]:
    persons_on = faces_by_photo(
        payload for _, payload in _scroll(client, faces_collection, ["photo_id", "person_id"])
    )
    return find_unbelegt(
        _scroll(client, collection, ["photo_uid", "photo_id", "person_ids",
                                     "person_suggestions", "face_source", "face_count"]),
        persons_on,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--faces-collection", default=os.environ.get("PHOTOVAULT_FACES", "faces"))
    parser.add_argument("--ids", action="store_true", help="Nur Punkt-IDs ausgeben")
    args = parser.parse_args(argv)

    from qdrant_client import QdrantClient

    found = check(QdrantClient(url=args.qdrant_url, timeout=120),
                  args.collection, args.faces_collection)
    if args.ids:
        for row in found:
            print(row.point_id)
        return 1 if found else 0
    print(f"Fotos mit Person ohne passendes Gesicht: {len(found)}")
    for row in found:
        print(f"  {row.point_id}  face_source={row.face_source}  Gesichter={row.face_count}  "
              f"ohne Beleg={len(row.missing)}  davon vorgeschlagen={row.suggested}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
