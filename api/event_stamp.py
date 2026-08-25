"""Serienname auf die betroffenen Fotos schreiben und Text-Vektoren nachziehen."""
from __future__ import annotations

import logging
import os

from api.qdrant_util import PHOTOS
from ingest.reembed import rebuild_text_vectors

logger = logging.getLogger(__name__)
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def apply_event_name(q, point_ids: list[str], name: str | None) -> dict:
    """`event_name` setzen oder entfernen, dann Kopfzeile + Text-Vektor.

    Ohne Name: Stempel runter und `event_excluded`, damit die Zeitscheibe
    das Foto nicht still wieder in die Serie zieht.
    """
    ids = [i for i in dict.fromkeys(point_ids) if i]
    if not ids:
        return {"photos": 0, "reembedded": 0}
    for chunk in _chunks(ids, 128):
        if name:
            q.set_payload(
                collection_name=PHOTOS,
                payload={"event_name": name, "event_excluded": False},
                points=chunk,
                wait=True,
            )
        else:
            q.delete_payload(
                collection_name=PHOTOS,
                keys=["event_name"],
                points=chunk,
                wait=True,
            )
            q.set_payload(
                collection_name=PHOTOS,
                payload={"event_excluded": True},
                points=chunk,
                wait=True,
            )
    stats = {}
    try:
        stats = rebuild_text_vectors(q, ids, collection=PHOTOS, ollama_url=OLLAMA_URL)
    except Exception:
        logger.exception("Re-embed after event_name failed")
    return {"photos": len(ids), "reembedded": stats.get("updated", 0)}


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
