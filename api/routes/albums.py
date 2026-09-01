"""Alben umbenennen, ohne neu zu ingestieren.

Der Ordnername ist oft eine Abkürzung (GC 07). In der UI wird er zum
sprechenden Namen, und der Index zieht die Pfad-IDs mit. Explorer-Rename
allein würde jede Photo-ID ungültig machen.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import PHOTOS, client, visible
from ingest.folder_parser import album_dir
from ingest.relocate import album_name_ok, plan_album_rename, rename_album
from ingest.spaces import photo_root

logger = logging.getLogger(__name__)
router = APIRouter()


class RenameRequest(BaseModel):
    path: str
    new_name: str
    dry_run: bool = True


@router.get("")
def list_albums(limit: int = 400) -> dict:
    """Eintrag je Albumordner, kürzeste/generische Namen zuerst."""
    from ingest.events import is_generic_album

    q = client()
    # Ohne Wurzel steigt album_dir ueber Sammelordner hinaus: "Fotos" gilt als
    # Sammelordner (folder_parser.RE_CAMERA_DIR), also landet eine lose Datei
    # in /mnt/photo/Fotos beim "Album" /mnt/photo -- der Freigabe selbst. Ein
    # Umbenennen dieses Eintrags verschoebe die ganze Sammlung. Der Ingest
    # reicht die Wurzel laengst durch (folder_parser.py:41-52), die Liste
    # bisher nicht.
    wurzel = photo_root()
    wurzel = Path(wurzel) if wurzel else None
    groups: dict[str, dict] = {}
    offset = None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, scroll_filter=visible(), limit=256, offset=offset,
            with_payload=["file_path", "folder_name", "event_name"],
            with_vectors=False,
        )
        for point in batch:
            payload = point.payload or {}
            path = payload.get("file_path") or ""
            if not path:
                continue
            album = album_dir(Path(path), root=wurzel)
            key = str(album)
            rec = groups.setdefault(
                key,
                {
                    "path": key,
                    "folder_name": payload.get("folder_name") or album.name,
                    "photo_count": 0,
                    "named_count": 0,
                    "cover": str(point.id),
                    "event_names": [],
                },
            )
            rec["photo_count"] += 1
            ev = payload.get("event_name")
            if ev:
                rec["named_count"] += 1
                if ev not in rec["event_names"]:
                    rec["event_names"].append(ev)
        if offset is None:
            break

    albums = list(groups.values())
    for a in albums:
        a["generic"] = is_generic_album(a["folder_name"])
        # Ganzen Dump umbenennen nur, wenn fast alle Fotos dieselbe Serie sind.
        a["rename_whole"] = (not a["generic"]) or (
            a["photo_count"] > 0 and a["named_count"] / a["photo_count"] >= 0.9
            and len(a["event_names"]) == 1
        )
    albums.sort(key=lambda a: (
        0 if a["generic"] else 1,
        len(a["folder_name"] or ""),
        (a["folder_name"] or "").lower(),
    ))
    return {"total": len(albums), "albums": albums[:limit]}


@router.post("/rename")
def rename(req: RenameRequest) -> dict:
    try:
        album_name_ok(req.new_name)
        src = Path(req.path)
        if req.dry_run:
            plan = plan_album_rename(src, req.new_name)
            from ingest.relocate import _photos_under

            indexed = _photos_under(client(), plan["from"], PHOTOS)
            plan["photos"] = len(indexed)
            plan["dry_run"] = True
            plan["ok"] = True
            return plan
        return rename_album(client(), src, req.new_name, dry_run=False)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except FileExistsError as e:
        raise HTTPException(409, f"Ziel existiert schon: {e}") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        logger.exception("Album-Rename fehlgeschlagen")
        raise HTTPException(500, str(e)) from e
