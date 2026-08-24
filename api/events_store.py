"""Ereignisse und ihre Namen aufbewahren.

Ereignisse selbst werden bei jedem Aufruf neu berechnet -- sie folgen aus
Zeitstempeln und Kanal, das ist billig und immer aktuell. Was **nicht** neu
berechnet werden kann, ist der Name, den ein Mensch vergeben hat.

Der Name hängt deshalb an einem **Zeitraum**, nicht am generierten Schlüssel:
kommen später Fotos derselben Gelegenheit dazu, wächst das Ereignis, und sein
Schlüssel (Beginn plus Anzahl) ändert sich. Ein Name, der daran hinge, wäre
verloren. An den Zeitraum gebunden findet ein neues Foto vom 31.12.2012 20:00
von selbst zu „Silvester 2012/13".

Überlappen zwei benannte Zeiträume mit einem Ereignis, gewinnt der mit der
größeren Überdeckung -- und nur, wenn sie deutlich ist. Sonst bliebe die
Zuordnung Zufall.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

COLLECTION = "event_meta"
#: Qdrant verlangt einen Vektor. Inhaltlich bedeutungslos, so klein wie moeglich.
_DUMMY = [0.0]

#: So viel eines Ereignisses muss in einem benannten Zeitraum liegen, damit der
#: Name gilt. Darunter ist es Beifang, kein Treffer.
MIN_OVERLAP = 0.5


def _point_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def ensure(client) -> bool:
    try:
        existing = {c.name for c in client.get_collections().collections}
        if COLLECTION not in existing:
            from qdrant_client.models import Distance, VectorParams

            client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=len(_DUMMY), distance=Distance.COSINE),
            )
            logger.info("Collection %s angelegt", COLLECTION)
        return True
    except Exception as e:
        logger.warning("Ereignis-Collection nicht verfuegbar: %s", e)
        return False


def name_event(client, *, name: str, channel: str, start: str, end: str,
               photo_count: int = 0) -> dict:
    """Einen Zeitraum benennen.

    `start` und `end` sind ISO-Zeitstempel. Ein erneutes Benennen desselben
    Zeitraums ueberschreibt den Namen, statt einen zweiten Eintrag anzulegen.
    """
    from qdrant_client.models import PointStruct

    if not ensure(client):
        raise RuntimeError("Ereignis-Collection nicht verfuegbar")
    name = (name or "").strip()
    if not name:
        raise ValueError("Name ist leer")

    key = f"{channel}|{start}|{end}"
    payload = {
        "name": name,
        "channel": channel,
        "start": start,
        "end": end,
        "photo_count": photo_count,
        "updated_at": time.time(),
    }
    client.upsert(collection_name=COLLECTION, wait=True,
                  points=[PointStruct(id=_point_id(key), vector=_DUMMY, payload=payload)])
    return payload


def forget(client, *, channel: str, start: str, end: str) -> bool:
    if not ensure(client):
        return False
    key = f"{channel}|{start}|{end}"
    try:
        client.delete(collection_name=COLLECTION, points_selector=[_point_id(key)], wait=True)
        return True
    except Exception as e:
        logger.warning("Ereignisname nicht entfernbar: %s", e)
        return False


def all_names(client) -> list[dict]:
    if not ensure(client):
        return []
    out, offset = [], None
    while True:
        try:
            batch, offset = client.scroll(collection_name=COLLECTION, limit=256,
                                          offset=offset, with_payload=True, with_vectors=False)
        except Exception as e:
            logger.warning("Ereignisnamen nicht lesbar: %s", e)
            return out
        out.extend(p.payload or {} for p in batch)
        if offset is None:
            return out


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def match(event_start, event_end, channel: str, names: list[dict]) -> str | None:
    """Namen für ein Ereignis suchen.

    Verglichen wird die Überdeckung der Zeiträume, nicht die Gleichheit: ein
    gewachsenes Ereignis soll seinen Namen behalten. Bei mehreren Kandidaten
    gewinnt die größere Überdeckung.
    """
    start, end = _parse(event_start), _parse(event_end)
    if start is None:
        return None
    if end is None or end < start:
        end = start
    length = max((end - start).total_seconds(), 1.0)

    best, best_share = None, 0.0
    for entry in names:
        if entry.get("channel") != channel:
            continue
        n_start, n_end = _parse(entry.get("start")), _parse(entry.get("end"))
        if n_start is None:
            continue
        if n_end is None or n_end < n_start:
            n_end = n_start
        overlap = (min(end, n_end) - max(start, n_start)).total_seconds()
        # Punktereignisse (Beginn == Ende) haben keine Dauer -- dort zaehlt,
        # ob der Zeitpunkt im benannten Zeitraum liegt.
        if length <= 1.0:
            share = 1.0 if n_start <= start <= n_end else 0.0
        else:
            share = max(0.0, overlap) / length
        if share > best_share:
            best, best_share = entry.get("name"), share
    return best if best_share >= MIN_OVERLAP else None
