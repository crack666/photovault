"""Ereignisse durchsehen und benennen.

Derselbe Ablauf wie bei „Wer ist das?", nur auf der Zeitachse: das System
bildet Gruppen, der Mensch erkennt sie und gibt ihnen einen Namen. Ein Name
deckt dabei 50 bis 150 Fotos auf einmal ab.

Die Serien selbst werden bei jedem Aufruf neu berechnet -- das ist billig und
immer aktuell. Nur die Namen sind gespeichert, gebunden an den Zeitraum
(siehe `api.events_store`).
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import events_store
from api.qdrant_util import client
from ingest.events import cluster
from ingest.provenance import CAMERA, channel as channel_of

logger = logging.getLogger(__name__)
router = APIRouter()

_RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")
#: Ordner, die keine Gelegenheit benennen.
_RE_GENERIC = re.compile(
    r"^(fotos?|photos?|bilder|images?|pictures?|handyfotos?|handypics|dcim|"
    r"camera|kamera|whatsapp( images)?|sent|screenshots?|download|neuer ordner|"
    r"unsortiert|sonstiges|div(erse)?|misc)$",
    re.IGNORECASE,
)

COLLECTION = "photos"
#: Serien unter dieser Groesse lohnen den Blick nicht -- ein Name fuer zwei
#: Fotos kostet mehr Aufmerksamkeit, als er einbringt.
MIN_SIZE = 5


class NameRequest(BaseModel):
    name: str
    channel: str = CAMERA
    start: str
    end: str
    photo_count: int = 0


def _load(client, only_channel: str | None):
    rows: dict[str, dict] = {}
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION, limit=512, offset=offset,
            with_payload=["taken_at", "date", "channel", "file_path", "folder_name",
                          "person_names", "caption_display"],
            with_vectors=False,
        )
        for point in batch:
            payload = point.payload or {}
            chan = payload.get("channel") or channel_of(payload.get("file_path") or "")
            if only_channel and chan != only_channel:
                continue
            rows[str(point.id)] = {**payload, "channel": chan}
        if offset is None:
            return rows


def suggest_name(folders: list[str], date: str) -> str:
    """Vorschlag aus dem Ordnernamen.

    Die Ordner sind ueber Jahre von Hand angelegt worden und benennen die
    Gelegenheit meist schon richtig -- "18. Geburtstag", "Silvester
    2012-2013". Ein Vorschlag macht aus dem Benennen ein Bestaetigen.

    Generische Namen taugen nicht: "Fotos" oder "Handyfotos" sagen nichts
    ueber die Gelegenheit. Dort bleibt das Feld leer, damit niemand
    versehentlich zwanzig Serien "Fotos" nennt.
    """
    if not folders:
        return ""
    name = folders[0].strip()
    if not name or _RE_GENERIC.match(name):
        return ""
    # Ein Jahr im Namen ist Kontext; fehlt es, hilft das Datum beim Auseinander-
    # halten gleichnamiger Anlaesse ("Weihnachtsfeier" gibt es jedes Jahr).
    if date and not _RE_YEAR.search(name):
        name = f"{name} {date[:4]}"
    return name


def _summarise(event, rows, names) -> dict:
    photos = [rows[pid] for pid in event.photo_ids if pid in rows]
    folders, people = [], []
    for p in photos:
        f = p.get("folder_name")
        if f and f not in folders:
            folders.append(f)
        for n in p.get("person_names") or []:
            if n not in people:
                people.append(n)
    start = event.start.isoformat() if event.start else None
    end = event.end.isoformat() if event.end else None
    return {
        "key": event.key(),
        "channel": event.channel,
        "start": start,
        "end": end,
        "date": event.start.strftime("%Y-%m-%d") if event.start else "",
        "span_minutes": None if event.day_level else round(event.span.total_seconds() / 60),
        "day_level": event.day_level,
        "size": event.size,
        "folders": folders,
        "person_names": people,
        "name": events_store.match(start, end, event.channel, names),
        "suggested_name": suggest_name(folders, event.start.strftime("%Y-%m-%d")
                                       if event.start else ""),
        "cover": [pid for pid in event.photo_ids[:8]],
    }


@router.get("/unnamed")
def unnamed(limit: int = 300, min_size: int = MIN_SIZE, channel: str = CAMERA) -> dict:
    """Die größten Serien ohne Namen, absteigend.

    Absteigend nach Größe, weil dort der Ertrag je Entscheidung am höchsten
    ist: eine Serie mit 150 Fotos zu benennen ordnet mehr als dreißig
    Zweiergrüppchen.
    """
    q = client()
    rows = _load(q, channel or None)
    names = events_store.all_names(q)
    events = cluster([(pid, r.get("taken_at") or r.get("date") or None, r["channel"])
                      for pid, r in rows.items()], gap=timedelta(hours=3))

    summaries = [_summarise(e, rows, names) for e in events if e.size >= min_size]
    named = [s for s in summaries if s["name"]]
    open_ones = [s for s in summaries if not s["name"]]
    open_ones.sort(key=lambda s: -s["size"])
    return {
        "total_events": len(summaries),
        "named": len(named),
        "unnamed": len(open_ones),
        "photos_unnamed": sum(s["size"] for s in open_ones),
        "events": open_ones[:limit],
    }


@router.get("/named")
def named(limit: int = 200) -> dict:
    entries = sorted(events_store.all_names(client()), key=lambda e: e.get("start") or "")
    return {"total": len(entries), "events": entries[:limit]}


@router.post("/name")
def set_name(req: NameRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name ist leer")
    try:
        saved = events_store.name_event(
            client(), name=req.name, channel=req.channel,
            start=req.start, end=req.end, photo_count=req.photo_count,
        )
    except Exception as e:
        logger.exception("Ereignisname nicht speicherbar")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "event": saved}


@router.post("/forget")
def forget(req: NameRequest) -> dict:
    ok = events_store.forget(client(), channel=req.channel,
                             start=req.start, end=req.end)
    return {"ok": ok}
