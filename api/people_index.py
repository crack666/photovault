"""Freitext-Namen auf person_ids abbilden.

In der Suche tippt man „Jonas, Max“ und nicht „lennart-behr, max-friedel“.
Gespeichert ist aber die ID. Diese Auflösung schlägt die Brücke: Teiltreffer
auf Vor- oder Nachnamen, und bei Mehrdeutigkeit („Sven“ passt auf zwei
Personen) werden beide berücksichtigt, statt still eine davon zu raten.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

FACES = "faces"
_CACHE: dict[str, Any] = {"at": 0.0, "people": []}
CACHE_SECONDS = 30.0


def known_persons(client, collection: str = FACES, force: bool = False) -> list[dict]:
    """[{id, name, aliases}] aller benannten Personen."""
    now = time.time()
    if not force and _CACHE["people"] and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["people"]
    try:
        from qdrant_client.models import Filter, IsEmptyCondition, PayloadField

        labeled = Filter(must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))])
        seen: dict[str, str] = {}
        offset = None
        while True:
            batch, offset = client.scroll(
                collection_name=collection, scroll_filter=labeled, limit=256,
                offset=offset, with_payload=["person_id", "person_name"], with_vectors=False,
            )
            for point in batch:
                payload = point.payload or {}
                pid = payload.get("person_id")
                if pid and not pid.startswith("_"):
                    seen.setdefault(pid, payload.get("person_name") or pid)
            if offset is None:
                break
        from api.person_meta import load_all

        meta = load_all(client)
        people = [
            {"id": k, "name": v, "aliases": (meta.get(k) or {}).get("aliases") or []}
            for k, v in sorted(seen.items())
        ]
    except Exception as e:
        logger.warning("Person index unavailable: %s", e)
        return _CACHE["people"]
    _CACHE["at"] = now
    _CACHE["people"] = people
    return people


def invalidate() -> None:
    _CACHE["at"] = 0.0


def _aliases(person: dict) -> list[str]:
    return [str(a).lower() for a in (person.get("aliases") or []) if a]


def resolve(token: str, people: list[dict]) -> list[str]:
    """Ein Sucheingabe-Token zu passenden person_ids.

    Reihenfolge: exakte ID, exakter Name, exakter Spitzname, dann Wortanfang
    (so trifft „Jonas“ den Vornamen, aber „ennart“ nicht mitten im Wort).
    Spitznamen zählen wie Namen -- „Karo“ findet „Annika Wolf“.
    """
    t = token.strip().lower()
    if not t:
        return []
    exact = [
        p["id"] for p in people
        if p["id"] == t or p["name"].lower() == t or t in _aliases(p)
    ]
    if exact:
        return sorted(set(exact))
    slug = t.replace(" ", "-")
    prefix = [
        p["id"]
        for p in people
        if any(part.startswith(t) for part in p["name"].lower().split())
        or any(a.startswith(t) for a in _aliases(p))
        or any(part.startswith(slug) for part in p["id"].split("-"))
        or p["id"].startswith(slug)
    ]
    if prefix:
        return sorted(set(prefix))
    return [
        p["id"] for p in people
        if t in p["name"].lower() or slug in p["id"] or any(t in a for a in _aliases(p))
    ]
