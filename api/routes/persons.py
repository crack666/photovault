"""Person labeling: Google-Fotos-Stil 'Wer ist das?'."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

from api.qdrant_util import FACES, PHOTOS, client
from ingest.face_cluster import cluster_faces, person_id_from_name
from ingest.face_matcher import FaceMatcher
from api.people_index import invalidate as invalidate_people
from ingest.reembed import rebuild_text_vectors

logger = logging.getLogger(__name__)
router = APIRouter()
SKIP_ID = "_skipped"
IGNORED_ID = "_ignored"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


class PersonCreate(BaseModel):
    name: str
    face_ids: list[str] = []
    cluster_id: Optional[str] = None


class AssignRequest(BaseModel):
    face_ids: list[str]


class SkipRequest(BaseModel):
    face_ids: list[str]


class RenameRequest(BaseModel):
    name: str


class MoveFacesRequest(BaseModel):
    face_ids: list[str]
    name: str


def _scroll_faces(q, filt: Filter | None, limit: int = 2000) -> list:
    points = []
    offset = None
    while len(points) < limit:
        batch, offset = q.scroll(
            collection_name=FACES,
            scroll_filter=filt,
            limit=min(256, limit - len(points)),
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        points.extend(batch)
        if offset is None:
            break
    return points


def _unlabeled_filter() -> Filter:
    return Filter(must=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))])


@router.get("")
def list_persons() -> list[dict]:
    q = client()
    try:
        labeled = Filter(must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))])
        points = _scroll_faces(q, labeled, limit=5000)
    except Exception:
        return []
    people: dict[str, dict] = {}
    for p in points:
        payload = p.payload or {}
        pid = payload.get("person_id")
        if not pid or pid.startswith("_"):
            continue
        rec = people.setdefault(
            pid,
            {
                "id": pid,
                "name": payload.get("person_name") or pid,
                "face_count": 0,
                "cover_face_id": str(p.id),
            },
        )
        rec["face_count"] += 1
    from api import person_meta

    meta = person_meta.load_all(q)
    for pid, rec in people.items():
        info = meta.get(pid) or {}
        rec["aliases"] = info.get("aliases") or []
        rec["pin"] = info.get("pin")
    rank = {"favorite": 0, None: 1, "muted": 2}

    def _sort_key(p):
        return (rank.get(p.get("pin"), 1), (p["name"] or "").lower())

    return sorted(people.values(), key=_sort_key)


#: Clustering ueber tausende Gesichter dauert Sekunden -- das darf nicht bei
#: jedem Klick neu laufen. Der Cache verfaellt, sobald sich die Zahl der
#: unbenannten Gesichter aendert, also nach jeder Zuordnung.
_cluster_cache: dict[str, object] = {"signature": None, "clusters": None}


def _count_faces(q, filt=None) -> int:
    try:
        if filt is None:
            return q.count(FACES, exact=True).count
        return q.count(FACES, count_filter=filt, exact=True).count
    except Exception:
        return 0


def _face_stats(q) -> dict:
    """Zahlen trennen: mit Namen, übersprungen, ignoriert, noch offen.

    `faces_labeled` war vorher total minus unbenannt — da lagen Übersprungene
    und Ignorierte in „benannt", obwohl sie keinen Menschen-Namen haben.
    """
    total = _count_faces(q)
    unlabeled = _count_faces(q, _unlabeled_filter())
    skipped = _count_faces(q, Filter(
        must=[FieldCondition(key="person_id", match=MatchValue(value=SKIP_ID))],
    ))
    ignored = _count_faces(q, Filter(
        must=[FieldCondition(key="person_id", match=MatchValue(value=IGNORED_ID))],
    ))
    named = max(0, total - unlabeled - skipped - ignored)
    return {
        "faces_total": total,
        "faces_named": named,
        "faces_labeled": named,
        "faces_skipped": skipped,
        "faces_ignored": ignored,
        "faces_unlabeled": unlabeled,
    }


def invalidate_clusters() -> None:
    _cluster_cache["signature"] = None
    _cluster_cache["clusters"] = None


@router.get("/unlabeled")
def unlabeled_clusters(limit: int = 40, max_faces: int = 30000,
                       min_size: int = 10) -> dict:
    """Queue für „Wer ist das?": Nachzügler bekannter Personen zuerst.

    Kleingruppen unter `min_size` (Passanten, 3–9 Bilder) bleiben unbenannt
    und tauchen in der Einzelansicht auf, nicht als eigene Karte.
    """
    from api.known_faces import queue_cards

    q = client()
    stats = _face_stats(q)
    signature = f"{stats['faces_unlabeled']}:{stats['faces_labeled']}:{max_faces}:{min_size}"

    cards = _cluster_cache["clusters"] if _cluster_cache["signature"] == signature else None
    if cards is None:
        labeled_filter = Filter(
            must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
        )
        try:
            unlabeled_pts = _scroll_faces(q, _unlabeled_filter(), limit=max_faces)
            labeled_pts = _scroll_faces(q, labeled_filter, limit=20000)
        except Exception:
            logger.exception("Loading unlabeled faces failed")
            return {"clusters": [], "remaining": 0, "stats": stats}

        def to_items(points):
            out = []
            for p in points:
                vec = p.vector
                if isinstance(vec, dict):
                    vec = next(iter(vec.values()), None)
                if vec is None:
                    continue
                out.append({"id": str(p.id), "vector": vec, "payload": p.payload or {}})
            return out

        cards = queue_cards(to_items(unlabeled_pts), to_items(labeled_pts), min_new_size=min_size)
        _cluster_cache["signature"] = signature
        _cluster_cache["clusters"] = cards
        known_n = sum(1 for c in cards if c.get("kind") == "known")
        logger.info(
            "Label queue: %d known-person batches, %d new groups (>=%d)",
            known_n, len(cards) - known_n, min_size,
        )

    matcher = FaceMatcher(q)
    page = cards[:limit]
    out = []
    for i, card in enumerate(page):
        rec = {k: v for k, v in card.items() if k != "centroid"}
        rec["id"] = f"c{i}-{(rec.get('cover_face_id') or 'x')[:8]}"
        if rec.get("kind") != "known":
            centroid = card.get("centroid")
            rec["suggestions"] = (
                matcher.suggest(centroid.tolist()) if centroid is not None else []
            )
            rec["kind"] = rec.get("kind") or "new"
        out.append(rec)
    queued = sum(int(c.get("size") or 0) for c in cards)
    stats = {**stats, "faces_in_queue": queued,
             "faces_small": max(0, (stats.get("faces_unlabeled") or 0) - queued)}
    return {
        "clusters": out,
        "remaining": max(0, len(cards) - limit),
        "groups_total": len(cards),
        "groups_usable": len(cards),
        "known_batches": sum(1 for c in cards if c.get("kind") == "known"),
        "min_size": min_size,
        "stats": stats,
    }


@router.get("/unknown")
def unknown_faces(
    limit: int = 200,
    offset: int = 0,
    sort: str = "quality",
    min_frontality: float = 0.0,
) -> dict:
    """Alle noch unbenannten Gesichter als flache Liste.

    Je mehr Personen benannt sind, desto mehr Beifang bleibt übrig: Fremde im
    Hintergrund, Ohren, Unschärfen. Die Cluster-Ansicht arbeitet sich von vorn
    durch; hier kann man gezielt suchen und aussortieren.

    `sort=quality` stellt die brauchbarsten Gesichter nach vorn (frontal, groß,
    sicher erkannt), `sort=worst` genau umgekehrt — praktisch, um Ohren und
    Profile in einem Rutsch wegzuräumen.
    """
    q = client()
    try:
        raw = _scroll_faces(q, _unlabeled_filter(), limit=20000)
    except Exception:
        logger.exception("Loading unknown faces failed")
        return {"faces": [], "total": 0, "returned": 0, "stats": _face_stats(q)}

    rows = []
    for point in raw:
        payload = point.payload or {}
        box = payload.get("box") or []
        px = min(box[2] - box[0], box[3] - box[1]) if len(box) == 4 else 0
        front = payload.get("frontality")
        rows.append(
            {
                "face_id": str(point.id),
                "photo_id": payload.get("photo_id"),
                "file_path": payload.get("file_path"),
                "folder_name": (payload.get("file_path") or "").split("/")[-2:-1] or [None],
                "score": payload.get("score"),
                "size_px": px,
                "frontality": front,
                # Ohne Landmark-Daten (vor dem Re-Ingest) faellt die Bewertung
                # auf Score und Groesse zurueck.
                "quality": round(
                    (front if front is not None else 0.5)
                    * float(payload.get("score") or 0)
                    * min(1.0, px / 80.0),
                    4,
                ),
            }
        )
    for row in rows:
        row["folder_name"] = row["folder_name"][0] if row["folder_name"] else None

    if min_frontality > 0:
        rows = [r for r in rows if (r["frontality"] or 0) >= min_frontality]
    rows.sort(key=lambda r: r["quality"], reverse=(sort != "worst"))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "faces": page,
        "total": total,
        "returned": len(page),
        "offset": offset,
        "has_landmarks": any(r["frontality"] is not None for r in rows[:200]),
        "stats": _face_stats(q),
    }


@router.post("/ignore")
def ignore_faces(req: SkipRequest) -> dict:
    """Gesichter dauerhaft aus der Queue nehmen, ohne sie zu benennen.

    Für Fremde, Ohren und alles, was nie einen Namen bekommen soll. Anders als
    „Überspringen“ ist das als bewusste Entscheidung gedacht und taucht nicht
    wieder auf.
    """
    n = _assign(IGNORED_ID, "Ignoriert", req.face_ids)
    return {"ignored": n}


@router.post("")
def create_person(req: PersonCreate) -> dict:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    pid = person_id_from_name(name)
    assigned = 0
    if req.face_ids:
        assigned = _assign(pid, name, req.face_ids)
    return {"id": pid, "name": name, "assigned": assigned}


@router.post("/{person_id}/assign")
def assign_to_person(person_id: str, req: AssignRequest) -> dict:
    name = person_id
    q = client()
    try:
        labeled = Filter(
            must=[FieldCondition(key="person_id", match=MatchValue(value=person_id))]
        )
        existing = _scroll_faces(q, labeled, limit=1)
        if existing:
            name = (existing[0].payload or {}).get("person_name") or person_id
    except Exception:
        pass
    n = _assign(person_id, name, req.face_ids)
    return {"assigned": n, "person": person_id, "name": name}


@router.post("/skip")
def skip_faces(req: SkipRequest) -> dict:
    n = _assign(SKIP_ID, "Übersprungen", req.face_ids)
    return {"skipped": n}


def _faces_of_person(q, person_id: str) -> list:
    filt = Filter(must=[FieldCondition(key="person_id", match=MatchValue(value=person_id))])
    return _scroll_faces(q, filt, limit=20000)


def _photos_of_person(q, person_id: str) -> list:
    filt = Filter(must=[FieldCondition(key="person_ids", match=MatchValue(value=person_id))])
    out, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, scroll_filter=filt, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        out.extend(batch)
        if offset is None:
            break
    return out


class AliasRequest(BaseModel):
    aliases: list[str] = []
    note: Optional[str] = None


class PinRequest(BaseModel):
    pin: Optional[str] = None


class GalleryRequest(BaseModel):
    face_ids: list[str]


def _photo_point_id(photo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, photo_id))


#: Ab so vielen Fotos wird ein Jahr in Monate gegliedert. Darunter ist der
#: Jahresblock noch ueberschaubar, und zusaetzliche Ueberschriften kosten mehr
#: Aufmerksamkeit als sie einbringen.
MONTHS_FROM = 60


def _timeline_from_points(photos: list, *, person_id: str = "", name: str = "") -> dict:
    """Fotos zu Jahr → Ereignis gliedern.

    Ereignisse kommen aus `ingest.events`: Serien, die zeitlich dicht
    beieinanderliegen, getrennt nach Herkunftskanal. Frueher wurde hier nach
    `(Ordner, Datum)` gruppiert -- das trennte einen durchgehenden Abend an
    Mitternacht und warf Screenshots mit Partyfotos zusammen, wenn beide im
    selben Ordner lagen.
    """
    from ingest.events import cluster
    from ingest.provenance import CAMERA, channel

    rows: dict[str, dict] = {}
    for photo in photos:
        payload = photo.payload or {}
        pid = str(photo.id)
        rows[pid] = {
            "id": pid,
            "date": payload.get("date") or "",
            "taken_at": payload.get("taken_at"),
            "channel": payload.get("channel") or channel(payload.get("file_path") or ""),
            "caption_display": payload.get("caption_display"),
            "caption_de": payload.get("caption_de"),
            "folder_name": payload.get("folder_name"),
            "person_names": payload.get("person_names") or [],
            "annotations": payload.get("annotations") or [],
            "sequence_in_folder": payload.get("sequence_in_folder"),
        }

    # `taken_at` fehlt bei aelteren Datensaetzen. Das Tagesdatum reicht dann:
    # `cluster` erkennt es als tagesgenau und bildet ein Ereignis je Tag,
    # statt das Foto aus dem Zeitstrahl fallen zu lassen.
    events = cluster([(pid, r["taken_at"] or r["date"] or None, r["channel"])
                      for pid, r in rows.items()])
    placed = {pid for e in events for pid in e.photo_ids}

    buckets: list[dict] = []
    for event in events:
        photos_in = [rows[pid] for pid in event.photo_ids]
        photos_in.sort(key=lambda r: (r["taken_at"] or "", r["folder_name"] or "",
                                      r["sequence_in_folder"] if r["sequence_in_folder"]
                                      is not None else 1 << 30, r["id"]))
        folders = []
        for r in photos_in:
            if r["folder_name"] and r["folder_name"] not in folders:
                folders.append(r["folder_name"])
        people = []
        for r in photos_in:
            for n in r["person_names"]:
                if n not in people:
                    people.append(n)
        buckets.append({
            "key": event.key(),
            "channel": event.channel,
            "date": (event.start.strftime("%Y-%m-%d") if event.start else ""),
            "start": event.start.isoformat() if event.start else None,
            "end": event.end.isoformat() if event.end else None,
            # Bei tagesgenauen Stempeln ist die Spanne bedeutungslos -- dann
            # keine Uhrzeit anzeigen, statt 00:00 vorzutaeuschen.
            "span_minutes": None if event.day_level else round(event.span.total_seconds() / 60),
            "day_level": event.day_level,
            "folder_name": folders[0] if folders else None,
            "folders": folders,
            "person_names": people,
            "photos": photos_in,
        })

    # Fotos ohne verwertbares Datum bekommen kein Ereignis -- sie sollen
    # trotzdem sichtbar bleiben.
    orphans = [r for pid, r in rows.items() if pid not in placed]
    if orphans:
        orphans.sort(key=lambda r: (r["folder_name"] or "", r["id"]))
        buckets.append({
            "key": "ohne-datum", "channel": CAMERA, "date": "", "start": None,
            "end": None, "span_minutes": None, "day_level": True,
            "folder_name": None, "folders": [], "person_names": [],
            "photos": orphans,
        })

    buckets.sort(key=lambda b: (b["date"] or "9999", b["key"]))
    years: list[dict] = []
    for bucket in buckets:
        year = (bucket["date"] or "")[:4] or "ohne Datum"
        if not years or years[-1]["year"] != year:
            years.append({"year": year, "count": 0, "channels": {},
                          "months": [], "events": []})
        bucket_year = years[-1]
        bucket_year["count"] += len(bucket["photos"])
        chans = bucket_year["channels"]
        chans[bucket["channel"]] = chans.get(bucket["channel"], 0) + len(bucket["photos"])
        bucket_year["events"].append(bucket)

        month = (bucket["date"] or "")[:7]
        if not bucket_year["months"] or bucket_year["months"][-1]["month"] != month:
            bucket_year["months"].append({"month": month, "count": 0, "events": []})
        bucket_year["months"][-1]["count"] += len(bucket["photos"])
        bucket_year["months"][-1]["events"].append(bucket)

    # Die Monatsebene soll gliedern, nicht Struktur behaupten, wo keine ist:
    # Ein Jahr mit drei Fotos braucht keine Monatsbaender.
    for bucket_year in years:
        if bucket_year["count"] < MONTHS_FROM or len(bucket_year["months"]) < 2:
            bucket_year["months"] = []

    dated = [r["date"] for r in rows.values() if r["date"]]
    return {
        "id": person_id,
        "name": name,
        "total": len(rows),
        "span": {"from": min(dated), "to": max(dated)} if dated else None,
        "years": years,
    }


def _retrieve_points(q, collection: str, ids: list[str], *, with_payload=True, with_vectors=False):
    out = []
    for i in range(0, len(ids), 256):
        chunk = ids[i : i + 256]
        try:
            found = q.retrieve(
                collection_name=collection,
                ids=chunk,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
        except Exception:
            logger.exception("retrieve %s (%d ids) failed", collection, len(chunk))
            continue
        out.extend(p for p in found if p is not None)
    return out


@router.post("/gallery")
def faces_gallery(req: GalleryRequest) -> dict:
    """Fotos + Gesichter zu einer Face-Menge — Detailansicht Unbekannte."""
    if not req.face_ids:
        raise HTTPException(400, "face_ids ist leer")
    q = client()
    faces = _retrieve_points(q, FACES, req.face_ids, with_payload=True, with_vectors=False)
    face_items = []
    photo_hashes: list[str] = []
    seen_hash: set[str] = set()
    for face in faces:
        payload = face.payload or {}
        face_items.append(
            {
                "face_id": str(face.id),
                "photo_id": payload.get("photo_id"),
                "file_path": payload.get("file_path"),
                "score": payload.get("score"),
                "frontality": payload.get("frontality"),
            }
        )
        hid = payload.get("photo_id")
        if hid and hid not in seen_hash:
            seen_hash.add(hid)
            photo_hashes.append(hid)
    face_items.sort(key=lambda f: f["score"] if f["score"] is not None else 1.0)
    photos = (
        _retrieve_points(
            q, PHOTOS, [_photo_point_id(h) for h in photo_hashes], with_payload=True
        )
        if photo_hashes
        else []
    )
    cover_vec = None
    try:
        with_vec = _retrieve_points(
            q, FACES, req.face_ids[:1], with_payload=False, with_vectors=True
        )
        if with_vec:
            cover_vec = with_vec[0].vector
            if isinstance(cover_vec, dict):
                cover_vec = next(iter(cover_vec.values()), None)
            if cover_vec is not None and hasattr(cover_vec, "tolist"):
                cover_vec = cover_vec.tolist()
    except Exception:
        cover_vec = None
    try:
        suggestions = FaceMatcher(q).suggest(cover_vec) if cover_vec is not None else []
    except Exception:
        logger.exception("Face suggestions for unknown gallery failed")
        suggestions = []
    out = _timeline_from_points(photos, name="Unbekannt")
    out["faces"] = face_items
    out["face_count"] = len(face_items)
    out["suggestions"] = suggestions
    out["cover_face_id"] = face_items[0]["face_id"] if face_items else None
    return out


@router.post("/{person_id}/pin")
def set_pin(person_id: str, req: PinRequest) -> dict:
    """Favorit oder ausgeblendet — steuert nur die Personenliste, nicht die Suche."""
    from api import person_meta

    pin = req.pin or None
    if pin not in (None, "favorite", "muted"):
        raise HTTPException(400, "pin must be favorite, muted, or empty")
    q = client()
    if not _faces_of_person(q, person_id):
        raise HTTPException(404, f"Person '{person_id}' nicht gefunden")
    try:
        result = person_meta.save(q, person_id, pin=pin)
    except Exception as e:
        raise HTTPException(500, f"Pin speichern fehlgeschlagen: {e}") from e
    invalidate_people()
    return result


@router.post("/{person_id}/aliases")
def set_aliases(person_id: str, req: AliasRequest) -> dict:
    """Spitznamen pflegen — „Karo“ soll „Annika Wolf“ finden."""
    from api import person_meta

    q = client()
    if not _faces_of_person(q, person_id):
        raise HTTPException(404, f"Person '{person_id}' nicht gefunden")
    try:
        result = person_meta.save(q, person_id, req.aliases, req.note)
    except Exception as e:
        raise HTTPException(500, f"Spitznamen speichern fehlgeschlagen: {e}") from e
    invalidate_people()
    return result


@router.get("/{person_id}/photos")
def person_photos(person_id: str, limit: int = 3000) -> dict:
    """Alle Fotos einer Person, chronologisch nach Jahr und Ereignis gruppiert.

    Ein Album ist meist ein Ereignis an einem Datum -- deshalb Jahr als grobe
    Achse und darin Ordner+Datum als Ereignis. Das entspricht der Ordnung, in
    der die Fotos entstanden sind.
    """
    q = client()
    photos = _photos_of_person(q, person_id)[:limit]
    if not photos:
        people = {p["id"]: p["name"] for p in list_persons()}
        if person_id not in people:
            raise HTTPException(404, f"Person '{person_id}' nicht gefunden")
        return {"id": person_id, "name": people[person_id], "total": 0, "years": []}

    name = person_id
    for photo in photos:
        payload = photo.payload or {}
        for pid, pname in zip(
            payload.get("person_ids") or [], payload.get("person_names") or []
        ):
            if pid == person_id:
                name = pname
                break
    out = _timeline_from_points(photos, person_id=person_id, name=name)
    return out


@router.get("/{person_id}/faces")
def person_faces(person_id: str, limit: int = 500) -> dict:
    """Alle Gesichter einer Person — Grundlage, um einzelne zu korrigieren."""
    q = client()
    faces = _faces_of_person(q, person_id)
    if not faces:
        raise HTTPException(404, f"Person '{person_id}' nicht gefunden")
    name = (faces[0].payload or {}).get("person_name") or person_id
    items = []
    for face in faces[:limit]:
        payload = face.payload or {}
        items.append(
            {
                "face_id": str(face.id),
                "photo_id": payload.get("photo_id"),
                "file_path": payload.get("file_path"),
                "score": payload.get("score"),
            }
        )
    # Schwächste Detektionen zuerst: dort stecken die Fehlzuordnungen.
    items.sort(key=lambda f: f["score"] if f["score"] is not None else 1.0)
    return {"id": person_id, "name": name, "total": len(faces), "faces": items}


@router.post("/faces/unassign")
def unassign_faces(req: AssignRequest) -> dict:
    """Einzelne Gesichter aus ihrer Person lösen — zurück in die Queue.

    Für Clustering-Fehler: ein paar fremde Gesichter in einer sonst richtigen
    Gruppe, ohne dass die ganze Person aufgelöst werden muss.
    """
    if not req.face_ids:
        raise HTTPException(400, "face_ids ist leer")
    q = client()
    try:
        faces = q.retrieve(collection_name=FACES, ids=req.face_ids, with_payload=True)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    if not faces:
        raise HTTPException(404, "keine dieser Gesichter gefunden")
    photo_ids = [(f.payload or {}).get("photo_id") for f in faces]
    q.delete_payload(
        collection_name=FACES,
        keys=["person_id", "person_name"],
        points=[f.id for f in faces],
        wait=True,
    )
    touched = sync_photo_persons(q, [p for p in photo_ids if p])
    invalidate_clusters()
    invalidate_people()
    stats = (
        rebuild_text_vectors(q, touched, collection=PHOTOS, ollama_url=OLLAMA_URL)
        if touched
        else {}
    )
    return {
        "freed": len(faces),
        "photos_updated": len(touched),
        "reembedded": stats.get("updated", 0),
    }


@router.post("/faces/move")
def move_faces(req: MoveFacesRequest) -> dict:
    """Gesichter einer anderen Person zuordnen — falsch einsortiert statt falsch erkannt."""
    if not req.face_ids:
        raise HTTPException(400, "face_ids ist leer")
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    q = client()
    target = person_id_from_name(name)
    try:
        faces = q.retrieve(collection_name=FACES, ids=req.face_ids, with_payload=True)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    if not faces:
        raise HTTPException(404, "keine dieser Gesichter gefunden")
    photo_ids = [(f.payload or {}).get("photo_id") for f in faces]
    q.set_payload(
        collection_name=FACES,
        payload={"person_id": target, "person_name": name},
        points=[f.id for f in faces],
        wait=True,
    )
    touched = sync_photo_persons(q, [p for p in photo_ids if p])
    invalidate_clusters()
    invalidate_people()
    stats = (
        rebuild_text_vectors(q, touched, collection=PHOTOS, ollama_url=OLLAMA_URL)
        if touched
        else {}
    )
    return {
        "moved": len(faces),
        "to": {"id": target, "name": name},
        "photos_updated": len(touched),
        "reembedded": stats.get("updated", 0),
    }


@router.post("/{person_id}/rename")
def rename_person(person_id: str, req: RenameRequest) -> dict:
    """Person umbenennen -- inklusive der Namen an den Fotos und ihrer Text-Vektoren."""
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    q = client()
    faces = _faces_of_person(q, person_id)
    if not faces:
        raise HTTPException(404, f"Person '{person_id}' hat keine Gesichter")
    old_name = (faces[0].payload or {}).get("person_name") or person_id
    new_id = person_id_from_name(name)

    q.set_payload(
        collection_name=FACES,
        payload={"person_id": new_id, "person_name": name},
        points=[p.id for p in faces],
        wait=True,
    )
    touched = []
    for photo in _photos_of_person(q, person_id):
        payload = photo.payload or {}
        ids = [new_id if x == person_id else x for x in (payload.get("person_ids") or [])]
        names = [name if x == old_name else x for x in (payload.get("person_names") or [])]
        if name not in names:
            names.append(name)
        q.set_payload(
            collection_name=PHOTOS,
            payload={"person_ids": list(dict.fromkeys(ids)),
                     "person_names": list(dict.fromkeys(names))},
            points=[photo.id], wait=True,
        )
        touched.append(str(photo.id))
    from api import person_meta

    if new_id != person_id:
        person_meta.rename(q, person_id, new_id)
    invalidate_clusters()
    invalidate_people()
    stats = rebuild_text_vectors(q, touched, collection=PHOTOS, ollama_url=OLLAMA_URL) if touched else {}
    return {
        "id": new_id, "name": name, "previous": {"id": person_id, "name": old_name},
        "faces": len(faces), "photos": len(touched), "reembedded": stats.get("updated", 0),
    }


@router.delete("/{person_id}")
def unassign_person(person_id: str) -> dict:
    """Zuordnung auflösen: Gesichter wandern zurück in die 'Wer ist das?'-Queue.

    Für versehentliche Zuordnungen -- ohne das bleibt eine Fehlbenennung
    dauerhaft im Index stehen.
    """
    q = client()
    faces = _faces_of_person(q, person_id)
    if not faces:
        raise HTTPException(404, f"Person '{person_id}' nicht gefunden")
    name = (faces[0].payload or {}).get("person_name") or person_id

    photo_ids = [
        (p.payload or {}).get("photo_id") for p in faces if (p.payload or {}).get("photo_id")
    ]
    q.delete_payload(
        collection_name=FACES, keys=["person_id", "person_name"],
        points=[p.id for p in faces], wait=True,
    )
    touched = sync_photo_persons(q, photo_ids)
    from api import person_meta

    person_meta.drop(q, person_id)
    invalidate_clusters()
    invalidate_people()
    stats = rebuild_text_vectors(q, touched, collection=PHOTOS, ollama_url=OLLAMA_URL) if touched else {}
    return {
        "removed": person_id, "name": name, "faces_freed": len(faces),
        "photos": len(touched), "reembedded": stats.get("updated", 0),
    }


def _assign(person_id: str, person_name: str, face_ids: list[str]) -> int:
    if not face_ids:
        return 0
    q = client()
    try:
        faces = q.retrieve(collection_name=FACES, ids=face_ids, with_payload=True)
    except Exception as e:
        raise HTTPException(404, str(e)) from e
    if not faces:
        return 0
    q.set_payload(
        collection_name=FACES,
        payload={"person_id": person_id, "person_name": person_name},
        points=face_ids,
    )
    invalidate_clusters()
    invalidate_people()
    photo_ids = [
        (face.payload or {}).get("photo_id")
        for face in faces
        if (face.payload or {}).get("photo_id")
    ]
    touched = sync_photo_persons(q, photo_ids) if person_id != SKIP_ID else []
    # Aus "fuenf Maenner" werden jetzt Namen -- der Text-Vektor muss das
    # mitbekommen. Kostet ~130 ms pro Foto, kein neuer Vision-Durchlauf.
    if touched:
        try:
            stats = rebuild_text_vectors(q, touched, collection=PHOTOS, ollama_url=OLLAMA_URL)
            logger.info("Re-embedded %d photos after labeling %s", stats["updated"], person_id)
        except Exception:
            logger.exception("Re-embed after labeling failed for %s", person_id)
    return len(faces)


def sync_photo_persons(q, photo_ids: list[str]) -> list[str]:
    """`person_ids`/`person_names` eines Fotos aus seinen Gesichtern neu ableiten.

    Zuverlässiger als inkrementelles Hinzufügen und Entfernen: Nimmt man ein
    Gesicht aus einer Person heraus, darf der Name nur dann vom Foto
    verschwinden, wenn dort kein weiteres Gesicht derselben Person mehr ist.
    Gibt die Punkt-IDs zurück, deren Payload sich geändert hat.
    """
    changed: list[str] = []
    for photo_id in dict.fromkeys(photo_ids):
        try:
            faces = _scroll_faces(
                q,
                Filter(must=[FieldCondition(key="photo_id", match=MatchValue(value=photo_id))]),
                limit=200,
            )
        except Exception:
            logger.exception("Reading faces of photo %s failed", photo_id)
            continue
        pairs: dict[str, str] = {}
        for face in faces:
            payload = face.payload or {}
            pid = payload.get("person_id")
            if pid and not pid.startswith("_"):
                pairs.setdefault(pid, payload.get("person_name") or pid)

        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, photo_id))
        try:
            points = q.retrieve(collection_name=PHOTOS, ids=[point_id], with_payload=True)
        except Exception:
            continue
        if not points:
            continue
        current = points[0].payload or {}
        ids, names = sorted(pairs), [pairs[k] for k in sorted(pairs)]
        if list(current.get("person_ids") or []) == ids and list(
            current.get("person_names") or []
        ) == names:
            continue
        q.set_payload(
            collection_name=PHOTOS,
            payload={"person_ids": ids, "person_names": names},
            points=[point_id],
            wait=True,
        )
        changed.append(point_id)
    return changed


@router.get("/candidates")
def known_candidates(threshold: float = 0.0, limit: int = 40, faces_per: int = 24) -> dict:
    """Unbenannte Gesichter, die zu bereits benannten Personen gehoeren.

    Knapp ein Drittel des Unbekannt-Stapels sind Leute, die laengst benannt
    sind -- in der Cluster-Ansicht erscheinen sie als dutzende Kleingruppen
    derselben Person. Hier wird daraus eine Rueckfrage je Person.
    """
    from api.known_faces import DEFAULT_THRESHOLD, candidates

    q = client()
    th = threshold or DEFAULT_THRESHOLD
    labeled_filter = Filter(
        must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
    )
    try:
        labeled_pts = _scroll_faces(q, labeled_filter, limit=20000)
        unlabeled_pts = _scroll_faces(q, _unlabeled_filter(), limit=30000)
    except Exception:
        logger.exception("Kandidaten konnten nicht geladen werden")
        return {"batches": [], "total_faces": 0}

    def to_items(points):
        out = []
        for p in points:
            vec = p.vector
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            out.append({"id": str(p.id), "vector": vec, "payload": p.payload or {}})
        return out

    batches = candidates(to_items(unlabeled_pts), to_items(labeled_pts), threshold=th)
    total = sum(b["count"] for b in batches)
    return {
        "threshold": th,
        "people": len(batches),
        "total_faces": total,
        "batches": [
            {**b, "faces": b["faces"][:faces_per], "shown": min(faces_per, b["count"])}
            for b in batches[:limit]
        ],
    }


class ConfirmRequest(BaseModel):
    person_id: str
    name: str = ""
    threshold: float = 0.0


@router.post("/candidates/confirm")
def confirm_candidates(req: ConfirmRequest) -> dict:
    """Alle Kandidaten einer Person auf einmal bestaetigen.

    Die Kandidaten werden hier neu berechnet statt vom Client uebernommen:
    zwischen Anzeige und Klick kann sich der Bestand geaendert haben, und eine
    Liste von Gesichts-IDs aus dem Browser waere eine Zuordnung, die niemand
    mehr gegen die Daten prueft.
    """
    from api.known_faces import DEFAULT_THRESHOLD, candidates

    q = client()
    th = req.threshold or DEFAULT_THRESHOLD
    labeled_filter = Filter(
        must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
    )
    try:
        labeled_pts = _scroll_faces(q, labeled_filter, limit=20000)
        unlabeled_pts = _scroll_faces(q, _unlabeled_filter(), limit=30000)
    except Exception as e:
        logger.exception("Kandidaten nicht ladbar")
        raise HTTPException(status_code=500, detail=str(e)) from e

    def to_items(points):
        out = []
        for p in points:
            vec = p.vector
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            out.append({"id": str(p.id), "vector": vec, "payload": p.payload or {}})
        return out

    batches = candidates(to_items(unlabeled_pts), to_items(labeled_pts), threshold=th)
    hit = next((b for b in batches if b["person_id"] == req.person_id), None)
    if not hit:
        return {"assigned": 0, "reason": "keine Kandidaten mehr"}

    face_ids = [f["face_id"] for f in hit["faces"]]
    n = _assign(hit["person_id"], req.name or hit["name"], face_ids)
    invalidate_clusters()
    return {"assigned": n, "person": hit["name"]}
