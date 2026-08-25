"""Fotos nachtraeglich anreichern: eigene Notizen, dann neu einbetten.

Das ist Wissen, das kein Modell aus Pixeln holen kann -- "das war im Stripclub",
"das ist Omas Garten". Es macht Teilmengen innerhalb eines Events unterscheidbar
und verknuepft gleichartige Abschnitte ueber Events hinweg.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.qdrant_util import PHOTOS, client
from api.thumbs import jpeg_truncation_hint, make_thumb
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
    stats = rebuild_text_vectors(q, req.photo_ids, collection=PHOTOS, ollama_url=OLLAMA_URL)
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
