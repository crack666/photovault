"""Ereignisse durchsehen und benennen.

Derselbe Ablauf wie bei „Wer ist das?", nur auf der Zeitachse: das System
bildet Gruppen, der Mensch erkennt sie und gibt ihnen einen Namen. Ein Name
deckt dabei 50 bis 150 Fotos auf einmal ab.

Unbenannte Serien entstehen aus der Zeitlücke. Ein vergebener Name hängt
am Zeitraum und am Foto (`event_name`). ✕ setzt `event_excluded`, und nach
dem Ablegen in den eigenen Ordner bleiben Dump-Fotos derselben Minute
draußen (siehe `api.event_stamp`).
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import event_stamp, events_store
from api.qdrant_util import client, visible
from ingest.event_suggest import (
    coalesce_same_album,
    neighbor_suggestions,
    rank_suggestions,
    suggestion_photo_count,
    timestamp_suggestions,
    unify_folder_suggestions,
)
from ingest.events import Event, cluster, is_generic_album, parse_stamp
from ingest.folder_parser import album_dir
from ingest.provenance import CAMERA, channel as channel_of
from ingest.relocate import dest_for_series, library_root_for, move_photos, needs_shelve

logger = logging.getLogger(__name__)
router = APIRouter()

_RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")

COLLECTION = "photos"
#: Serien unter dieser Groesse lohnen den Blick nicht -- ein Name fuer zwei
#: Fotos kostet mehr Aufmerksamkeit, als er einbringt. Die UI kann das
#: per min_size auf 2 senken, dann stehen die Kleinstserien hinten.
MIN_SIZE = 5
PAGE = 20
MAX_PAGE = 200


def _page(items: list, offset: int = 0, limit: int = PAGE) -> tuple[list, dict]:
    """Ein Ausschnitt der bereits sortierten Liste, plus Blaetter-Metadaten."""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = PAGE
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_PAGE))
    total = len(items)
    if offset > total:
        offset = total
    page = items[offset : offset + limit]
    return page, {
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "has_more": offset + len(page) < total,
    }


class NameRequest(BaseModel):
    name: str
    channel: str = CAMERA
    start: str
    end: str
    photo_count: int = 0
    photo_ids: list[str] = []


class MergeRequest(BaseModel):
    name: str
    channel: str = CAMERA
    a_start: str
    a_end: str
    b_start: str
    b_end: str
    photo_ids: list[str] = []


class RejectRequest(BaseModel):
    a_channel: str = CAMERA
    a_start: str
    a_end: str
    b_channel: str = CAMERA
    b_start: str
    b_end: str


class ShelveRequest(BaseModel):
    name: str
    photo_ids: list[str]
    dest_parent: str | None = None
    dry_run: bool = True


class MembersRequest(BaseModel):
    photo_ids: list[str]
    name: str | None = None


def _load(client, only_channel: str | None):
    rows: dict[str, dict] = {}
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION, scroll_filter=visible(), limit=512, offset=offset,
            with_payload=["taken_at", "date", "channel", "file_path", "folder_name",
                          "person_names", "caption_display", "event_name",
                          "event_excluded", "photo_id"],
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


def _stamped_name(row: dict) -> str:
    return (row.get("event_name") or "").strip()


def _named_member_ids(photo_ids: list[str], rows: dict, name: str) -> list[str]:
    """Welche Fotos der Zeitscheibe wirklich zur benannten Serie gehören.

    ✕ setzt `event_excluded`. Liegt die Serie schon in ihrem eigenen Ordner
    (nach „Fotos dorthin legen“), bleiben Dump-Fotos ohne Stempel draußen —
    sonst kämen die abgehakten Judo-Bilder mit derselben Minute zurück.
    """
    name = (name or "").strip()
    home: list[str] = []
    other: list[str] = []
    dump: list[str] = []
    for pid in photo_ids:
        row = rows.get(pid)
        if not row or row.get("event_excluded"):
            continue
        stamped = _stamped_name(row)
        if stamped and stamped != name:
            continue
        folder = (row.get("folder_name") or "").strip()
        if stamped == name or folder == name:
            home.append(pid)
        elif is_generic_album(folder):
            dump.append(pid)
        else:
            other.append(pid)
    if home:
        return home
    return other + dump


def _named_groups(rows: dict, names: list) -> tuple[dict[tuple[str, str], list[str]], dict]:
    """Benannte Serien (Zeitraum + Stempel) und der Rest ohne Anspruch."""
    events = cluster(
        [(pid, r.get("taken_at") or r.get("date") or None, r["channel"])
         for pid, r in rows.items()],
        gap=timedelta(hours=3),
    )
    groups: dict[tuple[str, str], list[str]] = {}
    claimed: set[str] = set()
    for e in events:
        start = e.start.isoformat() if e.start else None
        end = e.end.isoformat() if e.end else None
        name = events_store.match(start, end, e.channel, names)
        if not name:
            continue
        bucket = groups.setdefault((e.channel, name), [])
        for pid in _named_member_ids(e.photo_ids, rows, name):
            if pid in claimed:
                continue
            bucket.append(pid)
            claimed.add(pid)
    for pid, r in rows.items():
        if pid in claimed or r.get("event_excluded"):
            continue
        n = _stamped_name(r)
        if not n:
            continue
        groups.setdefault((r.get("channel") or CAMERA, n), []).append(pid)
        claimed.add(pid)
    open_rows = {pid: r for pid, r in rows.items() if pid not in claimed}
    return groups, open_rows


def _event_from_ids(pids: list[str], rows: dict, channel: str) -> Event:
    """Eine benannte Serie aus ihren gestempelten Fotos, ohne neu zu clustern."""
    def ts(pid: str) -> str:
        r = rows.get(pid) or {}
        return r.get("taken_at") or r.get("date") or ""

    ordered = sorted((p for p in pids if p in rows), key=ts)
    start = end = None
    saw_clock = False
    saw_day = False
    for pid in ordered:
        dt, day_only = parse_stamp(rows[pid].get("taken_at") or rows[pid].get("date"))
        if dt is None:
            continue
        if start is None or dt < start:
            start = dt
        if end is None or dt > end:
            end = dt
        if day_only:
            saw_day = True
        else:
            saw_clock = True
    return Event(
        photo_ids=ordered,
        channel=channel,
        start=start,
        end=end,
        day_level=bool(saw_day and not saw_clock),
    )


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
    if is_generic_album(name):
        return ""
    # Ein Jahr im Namen ist Kontext; fehlt es, hilft das Datum beim Auseinander-
    # halten gleichnamiger Anlaesse ("Weihnachtsfeier" gibt es jedes Jahr).
    if date and not _RE_YEAR.search(name):
        name = f"{name} {date[:4]}"
    return name


def _summarise(event, rows, names, forced_name: str | None = None) -> dict:
    folders, people, album_paths, file_paths = [], [], [], []
    sources_map: dict[str, dict] = {}
    for pid in event.photo_ids:
        p = rows.get(pid)
        if not p:
            continue
        f = p.get("folder_name")
        if f and f not in folders:
            folders.append(f)
        for n in p.get("person_names") or []:
            if n not in people:
                people.append(n)
        fp = p.get("file_path")
        ap = str(album_dir(Path(fp))) if fp else (f or "")
        if fp:
            file_paths.append(fp)
            if ap and ap not in album_paths:
                album_paths.append(ap)
        rec = sources_map.get(ap)
        if rec is None:
            rec = {"path": ap, "folder": f or "", "photo_ids": []}
            sources_map[ap] = rec
        rec["photo_ids"].append(pid)
    start = event.start.isoformat() if event.start else None
    end = event.end.isoformat() if event.end else None
    name = (forced_name or "").strip() or events_store.match(start, end, event.channel, names)
    shelve = needs_shelve(folders, name or "")
    dest = None
    if name and shelve and file_paths:
        try:
            dest = str(library_root_for(file_paths) / name)
        except ValueError:
            dest = None
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
        "album_paths": album_paths,
        "sources": [
            {"path": v["path"], "folder": v["folder"],
             "size": len(v["photo_ids"]), "photo_ids": v["photo_ids"]}
            for v in sources_map.values()
        ],
        "person_names": people,
        "name": name,
        "suggested_name": suggest_name(folders, event.start.strftime("%Y-%m-%d")
                                       if event.start else ""),
        "cover": [pid for pid in event.photo_ids[:8]],
        "photo_ids": list(event.photo_ids),
        "needs_shelve": shelve,
        "dest": dest,
    }


@router.get("/unnamed")
def unnamed(
    limit: int = PAGE,
    offset: int = 0,
    min_size: int = MIN_SIZE,
    channel: str = CAMERA,
) -> dict:
    """Die größten Serien ohne Namen, absteigend, seitenweise.

    Absteigend nach Größe, weil dort der Ertrag je Entscheidung am höchsten
    ist: eine Serie mit 150 Fotos zu benennen ordnet mehr als dreißig
    Zweiergrüppchen. Kleinstserien (2–4 Fotos) sind oft eine Salve derselben
    Szene — sie stehen hinten und sind per min_size=2 erreichbar.
    """
    q = client()
    rows = _load(q, channel or None)
    names = events_store.all_names(q)
    groups, open_rows = _named_groups(rows, names)
    events = cluster([(pid, r.get("taken_at") or r.get("date") or None, r["channel"])
                      for pid, r in open_rows.items()], gap=timedelta(hours=3))

    # Keine Zeitraum-Namen: sonst rutschen per ✕ entfernte Fotos wieder
    # in die benannte Serie, nur weil sie in derselben Stunde liegen.
    min_size = max(2, int(min_size or MIN_SIZE))
    summaries = [_summarise(e, open_rows, []) for e in events if e.size >= 2]
    summaries = coalesce_same_album(summaries)
    tiny = [s for s in summaries if s["size"] < min_size]
    summaries = [s for s in summaries if s["size"] >= min_size]
    summaries.sort(key=lambda s: -s["size"])
    page, meta = _page(summaries, offset, limit)
    return {
        "total_events": len(groups) + len(events),
        "named": len(groups),
        "unnamed": len(summaries),
        "unnamed_small": len(tiny),
        "photos_unnamed": sum(s["size"] for s in summaries),
        "photos_small": sum(s["size"] for s in tiny),
        "min_size": min_size,
        "events": page,
        **meta,
    }


@router.get("/named")
def named(limit: int = 200, detail: bool = False, channel: str = "") -> dict:
    """Ohne detail: nur Namen für die Vorschlagsliste. Mit detail: Karten."""
    if not detail:
        entries = sorted(events_store.all_names(client()), key=lambda e: e.get("start") or "")
        return {"total": len(entries), "events": entries[:limit]}
    q = client()
    rows = _load(q, channel or None)
    names = events_store.all_names(q)
    groups, _ = _named_groups(rows, names)
    named_ones = []
    for (ch, name), pids in groups.items():
        ev = _event_from_ids(pids, rows, ch)
        named_ones.append(_summarise(ev, rows, [], forced_name=name))
    named_ones.sort(key=lambda s: s.get("start") or "", reverse=True)
    return {
        "total": len(named_ones),
        "events": named_ones[:limit],
    }


@router.post("/name")
def set_name(req: NameRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name ist leer")
    q = client()
    try:
        saved = events_store.name_event(
            q, name=req.name, channel=req.channel,
            start=req.start, end=req.end, photo_count=req.photo_count,
        )
    except Exception as e:
        logger.exception("Ereignisname nicht speicherbar")
        raise HTTPException(status_code=500, detail=str(e)) from e
    ids = req.photo_ids or _ids_in_span(q, req.channel, req.start, req.end)
    stamped = event_stamp.apply_event_name(q, ids, req.name.strip())
    return {"ok": True, "event": saved, **stamped}


@router.post("/shelve")
def shelve(req: ShelveRequest) -> dict:
    """Nur die Fotos dieser Serie in einen eigenen Ordner legen.

    Den WhatsApp-Dump umbenennen wäre falsch — dort liegen tausende andere
    Bilder. Move, kein Copy; der Index zieht die Pfad-IDs mit.
    """
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name ist leer")
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    q = client()
    try:
        points = []
        for i in range(0, len(req.photo_ids), 128):
            points.extend(
                q.retrieve(
                    collection_name=COLLECTION,
                    ids=req.photo_ids[i : i + 128],
                    with_payload=["file_path"],
                    with_vectors=False,
                )
            )
        paths = [(p.payload or {}).get("file_path") for p in points]
        paths = [p for p in paths if p]
        parent = Path(req.dest_parent) if req.dest_parent else library_root_for(paths)
        dest = parent / name
        result = move_photos(q, req.photo_ids, dest, folder_name=name, dry_run=req.dry_run)
        new_ids = result.get("new_ids") or []
        if new_ids and not req.dry_run:
            event_stamp.apply_event_name(q, new_ids, name)
        return result
    except FileExistsError as e:
        raise HTTPException(409, f"Ziel existiert schon: {e}") from e
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        logger.exception("Serie ablegen fehlgeschlagen")
        raise HTTPException(500, str(e)) from e


@router.post("/forget")
def forget(req: NameRequest) -> dict:
    q = client()
    ok = events_store.forget(q, channel=req.channel,
                             start=req.start, end=req.end)
    ids = req.photo_ids or _ids_in_span(q, req.channel, req.start, req.end)
    stamped = event_stamp.apply_event_name(q, ids, None)
    return {"ok": ok, **stamped}


@router.post("/members")
def set_members(req: MembersRequest) -> dict:
    """Einzelne Fotos in eine Serie legen oder wieder herausnehmen.

    Ohne Name: `event_name` runter. Die benannte Ansicht gruppiert nur noch
    nach diesem Stempel — ein erneutes Laden steckt das Foto nicht zurück
    in die Zeitscheibe.
    """
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    name = (req.name or "").strip() or None
    return event_stamp.apply_event_name(client(), req.photo_ids, name)


@router.get("/suggestions")
def suggestions(channel: str = CAMERA, limit: int = PAGE, offset: int = 0) -> dict:
    """Nachbar-Serien, gemischte Ordner, gleiche Uhrzeit — zum Bestätigen.

    Sortiert nach Fotozahl, nicht nach internem Score: 80 Fotos einer Feier
    vor vier Schnappschüssen vom selben Berg. Blättern über offset/limit,
    sonst bleiben 170 von 212 Karten unsichtbar.
    """
    q = client()
    rows_all = _load(q, None)
    rows = (
        {k: v for k, v in rows_all.items() if v["channel"] == channel}
        if channel else rows_all
    )
    names = events_store.all_names(q)
    rejected = events_store.all_rejects(q)
    groups, open_rows = _named_groups(rows, names)
    events = cluster(
        [(pid, r.get("taken_at") or r.get("date") or None, r["channel"])
         for pid, r in open_rows.items()]
    )
    summaries = [_summarise(e, open_rows, []) for e in events if e.size >= 2]
    for (ch, name), pids in groups.items():
        ev = _event_from_ids(pids, rows, ch)
        if ev.size >= 2:
            summaries.append(_summarise(ev, rows, [], forced_name=name))
    summaries = coalesce_same_album(summaries)
    neighbors = neighbor_suggestions(summaries, rejected=rejected)
    unify = unify_folder_suggestions(summaries)
    photo_rows = [
        {
            "id": pid,
            "taken_at": r.get("taken_at") or r.get("date"),
            "channel": r["channel"],
            "folder_name": r.get("folder_name"),
        }
        for pid, r in rows_all.items()
    ]
    stamps = timestamp_suggestions(photo_rows, rejected=rejected)
    items = rank_suggestions(
        [_with_dest(s) for s in (neighbors + unify + stamps)]
    )
    page, meta = _page(items, offset, limit)
    return {
        "total": len(items),
        "suggestions": page,
        **meta,
    }


def _with_dest(suggestion: dict) -> dict:
    """Zielordner an die Karte hängen, damit die UI Von → Nach zeigen kann."""
    suggestion["photo_count"] = suggestion_photo_count(suggestion)
    kind = suggestion.get("kind")
    if kind == "neighbor":
        paths: list[str] = []
        for side in (suggestion.get("a") or {}, suggestion.get("b") or {}):
            for p in side.get("album_paths") or []:
                if p and p not in paths:
                    paths.append(p)
        suggestion.update(dest_for_series(paths, suggestion.get("suggested_name") or ""))
    elif kind == "unify_folders":
        ev = suggestion.get("event") or {}
        suggestion.update(
            dest_for_series(ev.get("album_paths") or [], suggestion.get("suggested_name") or "")
        )
    return suggestion


@router.post("/merge")
def merge(req: MergeRequest) -> dict:
    """Zwei Zeiträume unter einem Namen — der User hat bestätigt."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Name ist leer")
    q = client()
    start = min(req.a_start, req.b_start)
    end = max(req.a_end, req.b_end)
    try:
        saved = events_store.name_event(
            q, name=req.name, channel=req.channel,
            start=start, end=end, photo_count=len(req.photo_ids),
        )
        events_store.forget(q, channel=req.channel, start=req.a_start, end=req.a_end)
        events_store.forget(q, channel=req.channel, start=req.b_start, end=req.b_end)
    except Exception as e:
        logger.exception("Zusammenlegen nicht speicherbar")
        raise HTTPException(status_code=500, detail=str(e)) from e
    ids = req.photo_ids or (
        _ids_in_span(q, req.channel, req.a_start, req.a_end)
        + _ids_in_span(q, req.channel, req.b_start, req.b_end)
    )
    stamped = event_stamp.apply_event_name(q, ids, req.name.strip())
    return {"ok": True, "event": saved, **stamped}


@router.post("/reject")
def reject(req: RejectRequest) -> dict:
    try:
        saved = events_store.reject_merge(
            client(),
            a=(req.a_channel, req.a_start, req.a_end),
            b=(req.b_channel, req.b_start, req.b_end),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "reject": saved}


def _ids_in_span(q, channel: str, start: str, end: str) -> list[str]:
    rows = _load(q, channel)
    s, _ = parse_stamp(start)
    e, _ = parse_stamp(end)
    if s is None:
        return []
    if e is None or e < s:
        e = s
    ids = []
    for pid, r in rows.items():
        dt, _ = parse_stamp(r.get("taken_at") or r.get("date"))
        if dt is None:
            continue
        if s <= dt <= e:
            ids.append(pid)
    return ids
