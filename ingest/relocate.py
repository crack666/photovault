"""Dateien verschieben und den Index mitziehen.

Physisch immer `rename` auf demselben Volume. Der Qdrant-Punkt wandert auf die
neue Pfad-ID, Gesichter behalten ihre ID (nur `photo_id`/`file_path` ändern
sich) — sonst zerbrechen Crop-URLs und Personen-Zuordnungen.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ingest.filetimes import rename_same_volume
from ingest.identity import photo_id_for, point_id_for

logger = logging.getLogger(__name__)

#: Zeichen, die unter Windows und im Explorer Ordnernamen zerlegen.
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PHOTOS = "photos"
_FACES = "faces"


def album_name_ok(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Name ist leer")
    if _BAD_NAME.search(cleaned) or cleaned.endswith(".") or cleaned.endswith(" "):
        raise ValueError(f"Ungültiger Ordnername: {name!r}")
    return cleaned


def replace_prefix(path: str, old_dir: str, new_dir: str) -> str:
    """Pfad umschreiben, sobald er unter `old_dir` liegt."""
    src = path.replace("\\", "/").rstrip("/")
    old = old_dir.replace("\\", "/").rstrip("/")
    new = new_dir.replace("\\", "/").rstrip("/")
    if src == old:
        return new
    if src.startswith(old + "/"):
        return new + src[len(old) :]
    return path


def migrate_photo(client, *, old_path: str, new_path: str, folder_name: str | None,
                  photos: str = _PHOTOS, faces: str = _FACES) -> dict:
    """Einen Foto-Punkt auf den neuen Pfad-Hash legen, Faces umhängen."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

    old_hash = photo_id_for(old_path)
    new_hash = photo_id_for(new_path)
    old_id = point_id_for(old_hash)
    new_id = point_id_for(new_hash)
    found = client.retrieve(
        collection_name=photos, ids=[old_id], with_payload=True, with_vectors=True
    )
    if not found:
        raise KeyError(f"Foto nicht im Index: {old_path}")
    payload = dict(found[0].payload or {})
    payload["file_path"] = new_path
    payload["photo_id"] = new_hash
    if folder_name:
        payload["folder_name"] = folder_name
    vectors = found[0].vector
    if not vectors:
        vectors = {}
    client.upsert(
        collection_name=photos, wait=True,
        points=[PointStruct(id=new_id, vector=vectors, payload=payload)],
    )
    face_hits = []
    offset = None
    filt = Filter(must=[FieldCondition(key="photo_id", match=MatchValue(value=old_hash))])
    while True:
        batch, offset = client.scroll(
            collection_name=faces, scroll_filter=filt, limit=64,
            offset=offset, with_payload=True, with_vectors=False,
        )
        face_hits.extend(batch)
        if offset is None:
            break
    if face_hits:
        client.set_payload(
            collection_name=faces,
            payload={"photo_id": new_hash, "file_path": new_path},
            points=[f.id for f in face_hits],
            wait=True,
        )
    if old_id != new_id:
        client.delete(collection_name=photos, points_selector=[old_id], wait=True)
    return {
        "old_id": old_id,
        "new_id": new_id,
        "faces": len(face_hits),
    }


def plan_album_rename(src: Path, new_name: str) -> dict:
    """Prüfen, ohne etwas zu verschieben."""
    src = Path(src)
    new_name = album_name_ok(new_name)
    dest = src.parent / new_name
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dest.exists() and dest.resolve() != src.resolve():
        raise FileExistsError(dest)
    if src.name == new_name:
        raise ValueError("Name ist unverändert")
    if src.stat().st_dev != dest.parent.stat().st_dev:
        raise OSError("nicht dasselbe Volume")
    return {
        "from": str(src),
        "to": str(dest),
        "from_name": src.name,
        "to_name": new_name,
    }


def rename_album(
    client,
    src: Path,
    new_name: str,
    *,
    dry_run: bool = True,
    photos: str = _PHOTOS,
    faces: str = _FACES,
) -> dict:
    """Albumordner umbenennen (Move) und Index-IDs mitziehen."""
    plan = plan_album_rename(src, new_name)
    old_dir, new_dir = plan["from"], plan["to"]
    indexed = _photos_under(client, old_dir, photos)
    plan["photos"] = len(indexed)
    plan["dry_run"] = dry_run
    if dry_run:
        plan["ok"] = True
        return plan

    rename_same_volume(Path(old_dir), Path(new_dir))
    folder_name = Path(new_dir).name
    migrated, failed = [], []
    for point in indexed:
        old_path = (point.payload or {}).get("file_path") or ""
        new_path = replace_prefix(old_path, old_dir, new_dir)
        try:
            migrated.append(migrate_photo(
                client, old_path=old_path, new_path=new_path,
                folder_name=folder_name, photos=photos, faces=faces,
            ))
        except Exception as e:
            logger.exception("Index-Migration fehlgeschlagen für %s", old_path)
            failed.append({"path": old_path, "error": str(e)})
    plan["migrated"] = len(migrated)
    plan["faces"] = sum(m["faces"] for m in migrated)
    plan["failed"] = failed
    plan["ok"] = not failed
    # Caption-Kopfzeile enthält den Ordnernamen — ohne Vision neu setzen.
    try:
        from ingest.reembed import rebuild_text_vectors

        rebuild_text_vectors(client, [m["new_id"] for m in migrated], collection=photos)
    except Exception:
        logger.exception("Re-embed after album rename failed")
    return plan


LIBRARY_DIR_NAMES = {"fotos", "alben", "photos"}


def unique_dest(dest_dir: Path, filename: str) -> Path:
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem, suf = Path(filename).stem, Path(filename).suffix
    n = 2
    while True:
        cand = dest_dir / f"{stem}-{n}{suf}"
        if not cand.exists():
            return cand
        n += 1


def library_root_for(file_paths: list[str]) -> Path:
    """Wohin eine aus einem Dump gezogene Serie gelegt wird.

    Unter `/mnt/photo/Handys/...` liegt meist ein Geschwister `Fotos` oder
    `Alben`. Das ist die Bibliothek, nicht der WhatsApp-Ordner selbst.
    """
    import os

    env = os.environ.get("PHOTOVAULT_LIBRARY")
    if env:
        return Path(env)
    for raw in file_paths:
        path = Path(raw)
        for parent in (path, *path.parents):
            if parent.name.lower() in LIBRARY_DIR_NAMES:
                return parent
            for cand in ("Fotos", "Alben", "photos"):
                sib = parent / cand
                if sib.is_dir():
                    return sib
    raise ValueError(
        "Kein Bibliotheksordner gefunden (Fotos/Alben). "
        "PHOTOVAULT_LIBRARY setzen oder dest_parent angeben."
    )


def dest_for_series(file_paths: list[str], name: str = "") -> dict:
    """Bibliotheksziel: Parent und optional Parent/Name.

    Ohne Treffer (kein Fotos/Alben, kein PHOTOVAULT_LIBRARY) bleiben beide
    Felder leer — die UI zeigt dann die Quellen trotzdem.
    """
    if not file_paths:
        return {"dest_parent": None, "dest": None}
    try:
        parent = library_root_for(file_paths)
    except ValueError:
        return {"dest_parent": None, "dest": None}
    cleaned = (name or "").strip()
    return {
        "dest_parent": str(parent),
        "dest": str(parent / cleaned) if cleaned else None,
    }


def needs_shelve(folders: list[str], series_name: str) -> bool:
    """Serie liegt noch im Dump oder verteilt — nicht den Dump umbenennen."""
    from ingest.events import is_generic_album

    if not folders:
        return True
    if any(is_generic_album(f) for f in folders):
        return True
    if len(folders) > 1:
        return True
    return False


def move_photos(
    client,
    point_ids: list[str],
    dest: Path,
    *,
    folder_name: str,
    dry_run: bool = True,
    reembed: bool = True,
    photos: str = _PHOTOS,
    faces: str = _FACES,
) -> dict:
    """Einzelne Dateien in einen neuen Ordner legen (Move, kein Copy).

    `reembed=False` laesst die Textvektoren, wie sie sind. Der Ordnername
    steckt im Vektor, nach einem Umzug ist er also veraltet -- aber jeder
    Vektor kostet einen Ollama-Aufruf, und wer tausend Screenshots aus der
    Bibliothek schiebt, will dafuer nicht die GPU belegen, die gerade
    Bildbeschreibungen rechnet. Nachziehen geht per POST /api/photos/reembed.
    """
    dest = Path(dest)
    folder_name = album_name_ok(folder_name)
    ids = [i for i in dict.fromkeys(point_ids) if i]
    if not ids:
        raise ValueError("keine Fotos")
    found = []
    for i in range(0, len(ids), 128):
        found.extend(
            client.retrieve(
                collection_name=photos, ids=ids[i : i + 128],
                with_payload=True, with_vectors=False,
            )
        )
    if not found:
        raise KeyError("keine dieser Fotos im Index")

    files = []
    skipped = []
    for point in found:
        payload = point.payload or {}
        old = payload.get("file_path") or ""
        if not old:
            skipped.append({"id": str(point.id), "reason": "kein Pfad"})
            continue
        src = Path(old)
        if dest.exists() and src.parent.resolve() == dest.resolve():
            skipped.append({"id": str(point.id), "reason": "liegt schon dort", "path": old})
            continue
        if not dry_run and not src.is_file():
            skipped.append({"id": str(point.id), "reason": "Datei fehlt", "path": old})
            continue
        target = unique_dest(dest, src.name)
        files.append({"id": str(point.id), "from": str(src), "to": str(target)})

    plan = {
        "dest": str(dest),
        "folder_name": folder_name,
        "photos": len(files),
        "skipped": skipped,
        "dry_run": dry_run,
        "files": files[:30],
    }
    if dry_run:
        plan["ok"] = True
        return plan

    if files:
        sample = Path(files[0]["from"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.is_file():
            raise FileExistsError(dest)
        dest.mkdir(parents=True, exist_ok=True)
        if sample.exists() and sample.stat().st_dev != dest.stat().st_dev:
            raise OSError("nicht dasselbe Volume — kein Copy, Abbruch")

    migrated, failed = [], []
    for item in files:
        try:
            target = unique_dest(dest, Path(item["from"]).name)
            rename_same_volume(Path(item["from"]), target)
            migrated.append(
                migrate_photo(
                    client,
                    old_path=item["from"],
                    new_path=str(target),
                    folder_name=folder_name,
                    photos=photos,
                    faces=faces,
                )
            )
        except Exception as e:
            logger.exception("Move fehlgeschlagen für %s", item["from"])
            failed.append({"path": item["from"], "error": str(e)})
    plan["migrated"] = len(migrated)
    plan["new_ids"] = [m["new_id"] for m in migrated]
    plan["faces"] = sum(m.get("faces", 0) for m in migrated)
    plan["failed"] = failed
    plan["ok"] = not failed
    plan["reembedded"] = 0
    if reembed and migrated:
        try:
            from ingest.reembed import rebuild_text_vectors

            stats = rebuild_text_vectors(client, [m["new_id"] for m in migrated],
                                         collection=photos)
            plan["reembedded"] = stats.get("updated", 0)
        except Exception:
            logger.exception("Re-embed after shelve failed")
    return plan


def _photos_under(client, directory: str, collection: str) -> list:
    """Fotos, deren file_path unter diesem Ordner liegt."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    prefix = directory.replace("\\", "/").rstrip("/") + "/"
    exact = directory.replace("\\", "/").rstrip("/")
    name = Path(directory).name
    filt = Filter(must=[FieldCondition(key="folder_name", match=MatchValue(value=name))])

    def under(point) -> bool:
        path = ((point.payload or {}).get("file_path") or "").replace("\\", "/")
        return path == exact or path.startswith(prefix)

    hits = [p for p in _scroll(client, collection, filt) if under(p)]
    if hits:
        return hits
    return [p for p in _scroll(client, collection, None) if under(p)]


def _scroll(client, collection: str, filt) -> list:
    out, offset = [], None
    while True:
        try:
            batch, offset = client.scroll(
                collection_name=collection, scroll_filter=filt, limit=256,
                offset=offset, with_payload=["file_path", "folder_name", "photo_id"],
                with_vectors=False,
            )
        except Exception:
            if filt is not None:
                return _scroll(client, collection, None)
            raise
        out.extend(batch)
        if offset is None:
            break
    return out
