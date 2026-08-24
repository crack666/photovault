"""Zusatzangaben je Person -- vor allem Spitznamen.

Wer „Annika Wolf“ als Namen vergibt, sucht später trotzdem nach „Karo“.
Der echte Name bleibt der eine, saubere Eintrag; Spitznamen kommen als
zusätzliche Sucheinstiege dazu, statt dass man sich für einen entscheiden muss.

Eigene Collection statt eines Feldes an jedem Gesicht: sonst müssten für eine
Namensänderung hunderte Gesichtspunkte angefasst werden.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = os.environ.get("PHOTOVAULT_PERSON_META", "person_meta")
DUMMY_VECTOR = [0.0]
_ready: dict[str, bool] = {}


def _point_id(person_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"person-meta:{person_id}"))


def ensure_collection(client) -> bool:
    if _ready.get("ok"):
        return True
    try:
        client.get_collection(COLLECTION)
        _ready["ok"] = True
        return True
    except Exception:
        pass
    try:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=1, distance=Distance.COSINE),
        )
        for field in ("person_id", "aliases"):
            try:
                client.create_payload_index(
                    COLLECTION, field_name=field, field_schema=PayloadSchemaType.KEYWORD
                )
            except Exception:
                pass
        _ready["ok"] = True
        return True
    except Exception as e:
        logger.warning("Person metadata unavailable: %s", e)
        return False


def load_all(client) -> dict[str, dict[str, Any]]:
    """person_id -> {aliases, note}."""
    if not ensure_collection(client):
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        offset = None
        while True:
            batch, offset = client.scroll(
                collection_name=COLLECTION, limit=256, offset=offset,
                with_payload=True, with_vectors=False,
            )
            for point in batch:
                payload = point.payload or {}
                pid = payload.get("person_id")
                if pid:
                    out[pid] = {
                        "aliases": payload.get("aliases") or [],
                        "note": payload.get("note"),
                        "pin": payload.get("pin"),
                    }
            if offset is None:
                break
    except Exception as e:
        logger.debug("Reading person metadata failed: %s", e)
    return out


_UNSET = object()
PINS = {None, "favorite", "muted"}


def _clean_aliases(aliases: list[str] | None) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        a = str(alias).strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            clean.append(a)
    return clean


def save(
    client,
    person_id: str,
    aliases: list[str] | None = None,
    note: str | None = None,
    pin=_UNSET,
) -> dict:
    """Schreibe Metadaten. `pin` weglassen = bisherigen Pin behalten."""
    if not ensure_collection(client):
        raise RuntimeError("person_meta collection unavailable")
    existing = load_all(client).get(person_id) or {}
    if aliases is None:
        aliases = existing.get("aliases") or []
    if note is None:
        note = existing.get("note")
    if pin is _UNSET:
        pin = existing.get("pin")
    if pin not in PINS:
        pin = None
    clean = _clean_aliases(aliases)
    from qdrant_client.models import PointStruct

    payload = {"person_id": person_id, "aliases": clean, "note": note, "pin": pin}
    client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=_point_id(person_id),
                vector=DUMMY_VECTOR,
                payload=payload,
            )
        ],
        wait=True,
    )
    return payload


def rename(client, old_id: str, new_id: str) -> None:
    """Metadaten mitziehen, wenn eine Person umbenannt wird."""
    meta = load_all(client).get(old_id)
    if not meta:
        return
    save(
        client,
        new_id,
        meta.get("aliases") or [],
        meta.get("note"),
        pin=meta.get("pin"),
    )
    try:
        client.delete(collection_name=COLLECTION, points_selector=[_point_id(old_id)], wait=True)
    except Exception as e:
        logger.debug("Dropping old person metadata failed: %s", e)


def drop(client, person_id: str) -> None:
    try:
        client.delete(collection_name=COLLECTION, points_selector=[_point_id(person_id)], wait=True)
    except Exception as e:
        logger.debug("Dropping person metadata failed: %s", e)
