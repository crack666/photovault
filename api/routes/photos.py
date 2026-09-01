"""Fotos nachtraeglich anreichern: eigene Notizen, dann neu einbetten.

Das ist Wissen, das kein Modell aus Pixeln holen kann -- "das war im Stripclub",
"das ist Omas Garten". Es macht Teilmengen innerhalb eines Events unterscheidbar
und verknuepft gleichartige Abschnitte ueber Events hinweg.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.qdrant_util import FACES, PHOTOS, client, visible
from api.thumbs import drop_cached, jpeg_truncation_hint, make_thumb
from ingest.reembed import apply_annotations, rebuild_text_vectors

logger = logging.getLogger(__name__)
router = APIRouter()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


class AnnotateRequest(BaseModel):
    photo_ids: list[str] = Field(..., description="Qdrant-Punkt-IDs der markierten Fotos")
    annotations: list[str] = Field(default_factory=list)
    mode: Literal["add", "remove", "replace"] = "add"
    reembed: bool = Field(True, description="Text-Vektoren direkt neu rechnen")


class AnnotateResponse(BaseModel):
    changed: int
    missing: int
    reembedded: int
    failed: int


class ReembedRequest(BaseModel):
    photo_ids: Optional[list[str]] = None
    folder_name: Optional[str] = Field(None, description="Statt IDs: ganzes Album")
    limit: int = 5000


#: Was die Detailansicht zeigt -- und woher es stammt. Die Herkunft ist hier
#: die eigentliche Information: ein EXIF-Datum wiegt anders als ein geratenes.
@router.get("/{point_id}")
def photo_detail(point_id: str) -> dict:
    q = client()
    try:
        points = q.retrieve(collection_name=PHOTOS, ids=[point_id], with_payload=True)
    except Exception as e:
        raise HTTPException(404, f"Foto nicht gefunden: {e}") from e
    if not points:
        raise HTTPException(404, "Foto nicht gefunden")
    p = points[0].payload or {}
    path = p.get("file_path") or ""
    exif = p.get("exif") or {}
    return {
        "id": point_id,
        "file_path": path,
        "file_name": path.rsplit("/", 1)[-1] if path else None,
        "folder_name": p.get("folder_name"),
        "event_name": p.get("event_name"),
        "sequence_in_folder": p.get("sequence_in_folder"),
        "caption_display": p.get("caption_display"),
        "caption_de": p.get("caption_de"),
        "caption_source": p.get("caption_source") or ("manual" if p.get("caption_locked") else "llm"),
        "caption_locked": bool(p.get("caption_locked")),
        "date": p.get("date"),
        "date_source": p.get("date_source"),
        "date_confidence": p.get("date_confidence"),
        "taken_at": p.get("taken_at"),
        "file_mtime": p.get("file_mtime"),
        "file_ctime": p.get("file_ctime"),
        "file_size": p.get("file_size"),
        "location": p.get("location"),
        "location_source": p.get("location_source"),
        "gps": p.get("gps"),
        "person_names": p.get("person_names") or [],
        "person_ids": p.get("person_ids") or [],
        "person_suggestions": p.get("person_suggestions") or [],
        "face_count": p.get("face_count"),
        "scene_tags": p.get("scene_tags") or [],
        "annotations": p.get("annotations") or [],
        "camera": " ".join(x for x in (exif.get("Make"), exif.get("Model")) if x) or None,
        "exif": exif,
        "ingested_at": p.get("ingested_at"),
        "file_warning": p.get("file_warning"),
        # Damit die Grossansicht weiss, ob ihr Knopf "in den Papierkorb" oder
        # "zurueckholen" heissen muss. Ohne das stand beim Oeffnen eines schon
        # vorgemerkten Fotos das Falsche dran.
        "trashed_at": p.get("trashed_at"),
    }


class CaptionRequest(BaseModel):
    caption_de: str
    #: Von Hand geschriebene Captions überleben spätere Vision-Läufe.
    lock: bool = True


@router.post("/{point_id}/caption")
def set_caption(point_id: str, req: CaptionRequest) -> dict:
    """Caption von Hand setzen und gegen den nächsten Vision-Lauf schützen."""
    text = req.caption_de.strip()
    q = client()
    try:
        points = q.retrieve(collection_name=PHOTOS, ids=[point_id], with_payload=True)
    except Exception as e:
        raise HTTPException(404, f"Foto nicht gefunden: {e}") from e
    if not points:
        raise HTTPException(404, "Foto nicht gefunden")
    q.set_payload(
        collection_name=PHOTOS,
        payload={
            "caption_de": text or None,
            "caption_source": "manual" if text else None,
            "caption_locked": bool(req.lock and text),
        },
        points=[point_id],
        wait=True,
    )
    stats = rebuild_text_vectors(q, [point_id], collection=PHOTOS, ollama_url=OLLAMA_URL)
    return {"id": point_id, "caption_de": text or None,
            "locked": bool(req.lock and text), "reembedded": stats.get("updated", 0)}


#: Geschätzte Herkunft — der Mensch darf sie zur Aufnahmezeit machen.
_ESTIMATED_DATE = {"filename", "folder", "folder_name", "folder_json", "file_time", "album"}


@router.post("/{point_id}/accept-date")
def accept_date(point_id: str) -> dict:
    """Geschätztes Aufnahmedatum festschreiben — in den Index und, wo möglich, ins EXIF.

    Echte Kameradaten (`date_source=exif`) werden nicht überschrieben. Die
    Dateizeit bleibt erhalten.
    """
    q = client()
    try:
        points = q.retrieve(collection_name=PHOTOS, ids=[point_id], with_payload=True)
    except Exception as e:
        raise HTTPException(404, f"Foto nicht gefunden: {e}") from e
    if not points:
        raise HTTPException(404, "Foto nicht gefunden")
    payload = points[0].payload or {}
    source = payload.get("date_source") or ""
    if source == "accepted":
        return {"id": point_id, "already": True, "date": payload.get("date"),
                "taken_at": payload.get("taken_at"), "written": False}
    if source == "exif":
        raise HTTPException(400, "Aufnahmedatum stammt schon aus den Bilddaten")
    if source and source not in _ESTIMATED_DATE:
        raise HTTPException(400, f"Herkunft {source} wird nicht übernommen")
    when = _when_from_payload(payload)
    if when is None:
        raise HTTPException(400, "kein Datum zum Übernehmen")
    path = payload.get("file_path") or ""
    written, reason = False, ""
    if path:
        from ingest.exif_writer import write_capture_time

        try:
            out = write_capture_time(
                path, when, source="accepted", dry_run=False, overwrite=False,
            )
            written = bool(out.get("written"))
            reason = out.get("reason") or ""
        except Exception as e:
            logger.warning("EXIF-Datum nicht schreibbar (%s): %s", path, e)
            reason = str(e)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    q.set_payload(
        collection_name=PHOTOS,
        payload={
            "date": when.strftime("%Y-%m-%d"),
            "taken_at": stamp,
            "date_source": "accepted",
            "date_confidence": 1.0,
        },
        points=[point_id],
        wait=True,
    )
    stats = rebuild_text_vectors(q, [point_id], collection=PHOTOS, ollama_url=OLLAMA_URL)
    return {
        "id": point_id,
        "date": when.strftime("%Y-%m-%d"),
        "taken_at": stamp,
        "written": written,
        "exif_reason": reason,
        "reembedded": stats.get("updated", 0),
    }


class SetDateRequest(BaseModel):
    date: str
    time: str | None = None


@router.post("/{point_id}/date")
def set_date(point_id: str, req: SetDateRequest) -> dict:
    """Aufnahmedatum von Hand setzen — auch wenn der Index „EXIF“ sagt.

    Kopierzeit ohne DateTimeOriginal sieht aus wie Bilddaten. Der Mensch
    sieht Ordner und Nachbarn und korrigiert. Überschreibt den Index und,
    wo möglich, die Datei (Notiz `accepted`, Dateizeit bleibt).
    """
    day = (req.date or "").strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(400, "Datum muss YYYY-MM-DD sein") from e
    clock = (req.time or "").strip()
    if clock:
        try:
            datetime.strptime(clock[:8], "%H:%M:%S" if clock.count(":") == 2 else "%H:%M")
        except ValueError as e:
            raise HTTPException(400, "Uhrzeit unlesbar") from e
        if clock.count(":") == 1:
            clock = clock + ":00"
        when = datetime.strptime(f"{day}T{clock[:8]}", "%Y-%m-%dT%H:%M:%S")
    else:
        when = datetime.strptime(day, "%Y-%m-%d")

    q = client()
    try:
        points = q.retrieve(collection_name=PHOTOS, ids=[point_id], with_payload=True)
    except Exception as e:
        raise HTTPException(404, f"Foto nicht gefunden: {e}") from e
    if not points:
        raise HTTPException(404, "Foto nicht gefunden")
    payload = points[0].payload or {}
    path = payload.get("file_path") or ""
    written, reason = False, ""
    if path:
        from ingest.exif_writer import write_capture_time

        try:
            out = write_capture_time(
                path, when, source="accepted", dry_run=False, overwrite=True,
            )
            written = bool(out.get("written"))
            reason = out.get("reason") or ""
        except Exception as e:
            logger.warning("EXIF-Datum nicht schreibbar (%s): %s", path, e)
            reason = str(e)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    q.set_payload(
        collection_name=PHOTOS,
        payload={
            "date": day,
            "taken_at": stamp,
            "date_source": "accepted",
            "date_confidence": 1.0,
        },
        points=[point_id],
        wait=True,
    )
    stats = rebuild_text_vectors(q, [point_id], collection=PHOTOS, ollama_url=OLLAMA_URL)
    return {
        "id": point_id,
        "date": day,
        "taken_at": stamp,
        "written": written,
        "exif_reason": reason,
        "reembedded": stats.get("updated", 0),
    }


def _when_from_payload(payload: dict) -> datetime | None:
    raw = payload.get("taken_at") or payload.get("date") or ""
    raw = str(raw).replace("Z", "")
    for fmt, n in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:n], fmt)
        except ValueError:
            continue
    return None


class BulkCaptionRequest(BaseModel):
    photo_ids: list[str]
    caption_de: str
    lock: bool = True
    #: Wie bei `annotate`. Der Haken in der Karte versprach Kontrolle ueber
    #: die GPU-Last und hatte hier keine Wirkung -- neu eingebettet wurde
    #: immer. Voreinstellung bleibt True, damit bestehende Aufrufer sich
    #: nicht anders verhalten.
    reembed: bool = Field(True, description="Text-Vektoren direkt neu rechnen")


@router.post("/caption/bulk")
def set_captions(req: BulkCaptionRequest) -> dict:
    """Dieselbe Caption auf viele Fotos -- für ganze Abschnitte eines Events."""
    text = req.caption_de.strip()
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    if not text:
        raise HTTPException(400, "caption_de ist leer")
    q = client()
    q.set_payload(
        collection_name=PHOTOS,
        payload={"caption_de": text, "caption_source": "manual",
                 "caption_locked": bool(req.lock)},
        points=req.photo_ids,
        wait=True,
    )
    stats = (
        rebuild_text_vectors(q, req.photo_ids, collection=PHOTOS, ollama_url=OLLAMA_URL)
        if req.reembed else {}
    )
    return {"updated": len(req.photo_ids), "locked": bool(req.lock),
            "reembedded": stats.get("updated", 0)}


def _stamp_file_warning(q, point_id: str, payload: dict, warning: str) -> None:
    """Einmal setzen — die Datei ändert sich nicht von allein."""
    if payload.get("file_warning") == warning:
        return
    try:
        q.set_payload(
            collection_name=PHOTOS,
            payload={"file_warning": warning},
            points=[point_id],
            wait=True,
        )
        payload["file_warning"] = warning
    except Exception:
        logger.debug("file_warning for %s not stored", point_id)


@router.get("/{point_id}/thumb")
def photo_thumb(point_id: str, size: int = 320):
    """Verkleinertes JPEG des Fotos. Ohne das zeigt die Suche nur Text."""
    q = client()
    try:
        points = q.retrieve(
            collection_name=PHOTOS, ids=[point_id], with_payload=["file_path", "file_warning"]
        )
    except Exception as e:
        raise HTTPException(404, f"Foto nicht gefunden: {e}") from e
    if not points:
        raise HTTPException(404, "Foto nicht gefunden")
    payload = points[0].payload or {}
    path = payload.get("file_path")
    if not path:
        raise HTTPException(404, "Foto hat keinen Pfad")
    try:
        data, warn = make_thumb(path, size=size)
    except FileNotFoundError:
        _stamp_file_warning(q, point_id, payload, "missing")
        raise HTTPException(404, f"Datei fehlt: {path}") from None
    except Exception as e:
        logger.warning("Thumb failed for %s: %s", path, e)
        _stamp_file_warning(q, point_id, payload, "unreadable")
        raise HTTPException(500, f"Thumbnail fehlgeschlagen: {e}") from e
    if not payload.get("file_warning"):
        warn = warn or jpeg_truncation_hint(path)
        if warn:
            _stamp_file_warning(q, point_id, payload, warn)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/annotate", response_model=AnnotateResponse)
def annotate(req: AnnotateRequest) -> AnnotateResponse:
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    if req.mode != "replace" and not req.annotations:
        raise HTTPException(400, "annotations ist leer")
    q = client()
    try:
        result = apply_annotations(
            q, req.photo_ids, req.annotations, mode=req.mode, collection=PHOTOS
        )
    except Exception as e:
        logger.exception("Annotate failed")
        raise HTTPException(500, f"Annotieren fehlgeschlagen: {e}") from e

    reembedded = failed = 0
    if req.reembed and result.get("changed_ids"):
        stats = rebuild_text_vectors(
            q, result["changed_ids"], collection=PHOTOS, ollama_url=OLLAMA_URL
        )
        reembedded, failed = stats["updated"], stats["failed"]
    return AnnotateResponse(
        changed=result["changed"],
        missing=result["missing"],
        reembedded=reembedded,
        failed=failed,
    )


@router.post("/reembed")
def reembed(req: ReembedRequest) -> dict:
    """Text-Vektoren neu bauen -- nach Labeling, Annotationen oder Metadaten-Fixes."""
    q = client()
    ids = req.photo_ids or []
    if not ids and req.folder_name:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        filt = Filter(
            must=[FieldCondition(key="folder_name", match=MatchValue(value=req.folder_name))]
        )
        offset = None
        while len(ids) < req.limit:
            batch, offset = q.scroll(
                collection_name=PHOTOS,
                scroll_filter=filt,
                limit=min(256, req.limit - len(ids)),
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            ids.extend(str(p.id) for p in batch)
            if offset is None:
                break
    if not ids:
        raise HTTPException(400, "Weder photo_ids noch ein Album mit Treffern angegeben")
    try:
        return rebuild_text_vectors(q, ids, collection=PHOTOS, ollama_url=OLLAMA_URL)
    except Exception as e:
        logger.exception("Reembed failed")
        raise HTTPException(500, f"Re-Embed fehlgeschlagen: {e}") from e


#: Mehr als das nimmt der Schwerpunkt nicht auf, ohne zu Brei zu werden --
#: und mehr Beispiele machen den Vorschlag nicht besser, nur langsamer.
MAX_POSITIVES = 64
MAX_SIMILAR = 2000


class SimilarRequest(BaseModel):
    """„Mehr davon" -- die Auswahl wird zur Abfrage."""

    photo_ids: list[str] = Field(..., description="Beispiele, denen die Treffer ähneln sollen")
    negative_ids: list[str] = Field(default_factory=list, description="Gegenbeispiele")
    #: `clip` = wie es aussieht, `text` = worum es geht (Album, Datum, Personen,
    #: Caption). Der Textvektor ist nur so gut wie die Captions, die schon da sind.
    using: Literal["clip", "text"] = "clip"
    #: `average` mittelt die Beispiele -- vorhersagbar, solange die Auswahl
    #: zusammengehört. `best` misst gegen jedes einzeln und trägt weiter,
    #: streut aber auch mehr.
    strategy: Literal["average", "best"] = "average"
    limit: int = 200
    score_threshold: Optional[float] = None


def _sample(ids: list[str], cap: int) -> list[str]:
    """Gleichmäßig ausdünnen statt vorn abschneiden -- sonst beschreibt eine
    Auswahl von 1 200 Fotos nur ihre ersten 64."""
    if len(ids) <= cap:
        return ids
    step = len(ids) / cap
    return [ids[int(i * step)] for i in range(cap)]


@router.post("/similar")
def similar(req: SimilarRequest) -> dict:
    """Ähnliche Fotos zu einer Auswahl.

    Das ist die Abfragesprache eines Vektorraums, und ohne sie ist eine Karte
    nur ein Poster: man sieht etwas, kann ihm aber nicht folgen.

    Gefragt wird über die Punkt-IDs, nicht über Vektoren -- Qdrant holt sie
    selbst. Damit kommt weder Ollama noch die GPU ins Spiel, und die Abfrage
    stört einen laufenden Caption-Lauf nicht.
    """
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    limit = max(1, min(req.limit, MAX_SIMILAR))
    positive = _sample(req.photo_ids, MAX_POSITIVES)
    negative = _sample(req.negative_ids, MAX_POSITIVES)

    from qdrant_client import models

    if len(positive) == 1 and not negative:
        # Ein einzelnes Beispiel: die einfache Nachbarschaftsabfrage genügt.
        query: object = positive[0]
    else:
        query = models.RecommendQuery(
            recommend=models.RecommendInput(
                positive=list(positive),
                negative=list(negative),
                strategy=(
                    models.RecommendStrategy.BEST_SCORE
                    if req.strategy == "best"
                    else models.RecommendStrategy.AVERAGE_VECTOR
                ),
            )
        )

    q = client()
    try:
        found = q.query_points(
            collection_name=PHOTOS,
            query=query,
            using=req.using,
            query_filter=visible(),
            # Die Beispiele selbst kommen zurueck und wuerden Plaetze belegen.
            limit=limit + len(positive),
            with_payload=False,
            with_vectors=False,
            score_threshold=req.score_threshold,
        ).points
    except Exception as e:
        logger.exception("Similar failed")
        raise HTTPException(500, f"Ähnlichkeitssuche fehlgeschlagen: {e}") from e

    seen = set(req.photo_ids)
    results = [
        {"id": str(p.id), "score": round(float(p.score), 4)}
        for p in found
        if str(p.id) not in seen
    ][:limit]
    return {"using": req.using, "strategy": req.strategy, "from": len(positive), "results": results}


class RelocateRequest(BaseModel):
    """Eine Auswahl in einen eigenen Ordner legen.

    Der Anlass ist konkret: Screenshots und Dokumente sind ein eigenes Thema
    und gehoeren nicht in dieselbe Sammlung wie die Fotos von Menschen. Sie zu
    loeschen waere zu viel -- sie in einen eigenen Ordner zu schieben genau
    richtig, und danach fallen sie aus der Bibliothek heraus.
    """

    photo_ids: list[str]
    #: Ordnername unter dem Bibliotheksziel. `dest_parent` ueberschreibt das
    #: automatisch gefundene Elternverzeichnis.
    folder_name: str
    dest_parent: Optional[str] = None
    #: Ohne `confirm` wird nur geplant, nichts bewegt.
    confirm: bool = False
    #: Textvektoren nachziehen. Standard aus: das kostet je Foto einen
    #: Ollama-Aufruf und damit die GPU, die vielleicht Captions rechnet.
    reembed: bool = False


@router.post("/relocate")
def relocate(req: RelocateRequest) -> dict:
    """Dateien verschieben (Move, kein Copy) und den Index nachziehen.

    Standardmaessig ein Trockenlauf: die Antwort zeigt, was passieren wuerde.
    Erst `confirm: true` bewegt etwas. Bei zweieinhalbtausend Dateien ist das
    kein Komfort, sondern die Bremse vor dem Unwiderruflichen.
    """
    from pathlib import Path

    from ingest.relocate import library_root_for, move_photos

    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    if not req.folder_name.strip():
        raise HTTPException(400, "folder_name ist leer")

    q = client()
    if req.dest_parent:
        parent = Path(req.dest_parent)
    else:
        # Das Bibliotheksziel steht neben dem Dump, nicht darin -- unter
        # /mnt/photo/Handys/... liegt meist ein Geschwister `Fotos`.
        sample = q.retrieve(collection_name=PHOTOS, ids=req.photo_ids[:20],
                            with_payload=["file_path"], with_vectors=False)
        paths = [(p.payload or {}).get("file_path") for p in sample]
        try:
            parent = library_root_for([p for p in paths if p])
        except ValueError as e:
            raise HTTPException(
                400,
                f"{e} — dest_parent angeben oder PHOTOVAULT_LIBRARY setzen.",
            ) from e

    try:
        return move_photos(
            q,
            req.photo_ids,
            parent / req.folder_name.strip(),
            folder_name=req.folder_name.strip(),
            dry_run=not req.confirm,
            reembed=req.reembed,
        )
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        # Anderes Volume: `move_photos` bricht bewusst ab, statt zu kopieren.
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        logger.exception("Relocate failed")
        raise HTTPException(500, f"Verschieben fehlgeschlagen: {e}") from e
