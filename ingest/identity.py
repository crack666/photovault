"""Photo-IDs folgen dem Dateipfad. Nach einem Move muss der Index mitziehen.

`photo_id` ist sha256(Pfad), der Qdrant-Punkt uuid5 davon. Ein Explorer-Rename
erzeugt sonst neue IDs und lässt Labels an den alten hängen. Operationen, die
PhotoVault selbst ausführt, migrieren beides im selben Schritt.
"""
from __future__ import annotations

import hashlib
import uuid


def photo_id_for(file_path: str) -> str:
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


def point_id_for(photo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, photo_id))


def point_id_for_path(file_path: str) -> str:
    return point_id_for(photo_id_for(file_path))
