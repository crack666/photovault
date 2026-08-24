"""Face crops for the labeling UI."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.qdrant_util import FACES, client
from api.thumbs import get_thumb

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{face_id}/crop")
def face_crop(face_id: str, pad: float = 0.35, size: int = 320):
    q = client()
    try:
        points = q.retrieve(collection_name=FACES, ids=[face_id], with_payload=True)
    except Exception as e:
        raise HTTPException(404, f"face not found: {e}") from e
    if not points:
        raise HTTPException(404, "face not found")
    payload = points[0].payload or {}
    path = payload.get("file_path")
    box = payload.get("box") or []
    if not path or len(box) != 4:
        raise HTTPException(404, "face has no crop")
    try:
        # Ueber den Thumbnail-Cache: beim Labeling wird derselbe Ausschnitt
        # immer wieder angefordert, und die Originale liegen auf dem NAS.
        data = get_thumb(path, size=size, box=box, pad=pad)
    except FileNotFoundError:
        raise HTTPException(404, "image missing") from None
    except Exception as e:
        logger.warning("Face crop failed for %s: %s", path, e)
        raise HTTPException(500, f"crop failed: {e}") from e
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
