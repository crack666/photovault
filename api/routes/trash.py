"""Papierkorb: erst vormerken, dann wirklich loeschen.

Eigener Router unter `/api/trash`, nicht unter `/api/photos/trash`: dort
faengt `GET /api/photos/{point_id}` die Anfrage ab und haelt „trash" fuer eine
Foto-Kennung. Die POSTs kamen durch, das GET nicht -- ein Fehler, der nur an
einer von drei Routen sichtbar war.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import FACES, PHOTOS, TRASH_KEY, client
from api.thumbs import drop_cached

logger = logging.getLogger(__name__)
router = APIRouter()

# --------------------------------------------------------------------------
# Papierkorb
# --------------------------------------------------------------------------
#
# Zweistufig, und die erste Stufe faesst absichtlich keine Datei an. Ein
# Vermerk im Payload ist sofort, folgenlos und vollstaendig umkehrbar -- wer
# tausend Screenshots markiert und dann zweifelt, hat nichts verloren. Erst
# das Leeren loescht, und dann richtig: Datei, Punkt, Gesichter, Vorschaubild.
#
# Bewusst kein Verschieben in einen Papierkorb-Ordner als erste Stufe: das
# waere zweimal Datei-I/O fuer denselben Zweck, und ein abgebrochener Umzug
# hinterlaesst einen Zustand, den niemand mehr versteht.

TRASH_LOG = Path(__file__).resolve().parent.parent.parent / "logs"

# Die Wurzel und die Schranke liegen in ingest/spaces.py -- dort steht schon
# die Rechnung, was zur Sammlung gehoert, und die Albumliste braucht sie
# ebenfalls. Zwei Fassungen davon waeren zwei Wahrheiten.
from ingest.spaces import photo_root, under_root  # noqa: E402


class TrashRequest(BaseModel):
    photo_ids: list[str]
    #: `False` holt sie wieder heraus.
    trashed: bool = True


@router.post("")
def set_trash(req: TrashRequest) -> dict:
    """In den Papierkorb legen oder daraus retten."""
    if not req.photo_ids:
        raise HTTPException(400, "photo_ids ist leer")
    q = client()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        if req.trashed:
            q.set_payload(
                collection_name=PHOTOS,
                payload={"trashed_at": stamp},
                points=req.photo_ids,
                wait=True,
            )
        else:
            q.clear_payload_keys(
                collection_name=PHOTOS, keys=["trashed_at"],
                points=req.photo_ids, wait=True,
            )
    except AttributeError:
        # Aeltere Clients kennen `clear_payload_keys` nicht.
        q.set_payload(collection_name=PHOTOS, payload={"trashed_at": None},
                      points=req.photo_ids, wait=True)
    except Exception as e:
        logger.exception("Trash failed")
        raise HTTPException(500, f"Papierkorb fehlgeschlagen: {e}") from e
    return {"trashed" if req.trashed else "restored": len(req.photo_ids)}


@router.get("")
def list_trash(limit: int = 200, offset: int = 0) -> dict:
    """Was zur Loeschung vorgemerkt ist, neueste Markierung zuerst."""
    from qdrant_client.models import Filter, IsEmptyCondition, PayloadField

    q = client()
    marked: list[dict] = []
    offset_id = None
    while True:
        batch, offset_id = q.scroll(
            collection_name=PHOTOS, limit=512, offset=offset_id,
            with_payload=True, with_vectors=False,
            scroll_filter=Filter(
                must_not=[IsEmptyCondition(is_empty=PayloadField(key="trashed_at"))]
            ),
        )
        for point in batch:
            payload = point.payload or {}
            marked.append({
                "id": str(point.id),
                "file_path": payload.get("file_path"),
                "trashed_at": payload.get("trashed_at"),
                "caption_display": payload.get("caption_display"),
                "caption_de": payload.get("caption_de"),
                "date": payload.get("date"),
                "scene_tags": (payload.get("scene_tags") or [])[:6],
                "person_names": payload.get("person_names") or [],
                "file_size": payload.get("file_size"),
            })
        if offset_id is None:
            break
    marked.sort(key=lambda m: m["trashed_at"] or "", reverse=True)
    page = marked[offset : offset + max(1, limit)]
    return {
        "total": len(marked),
        "bytes": sum(m["file_size"] or 0 for m in marked),
        "photos": page,
        "offset": offset,
        "returned": len(page),
        # Was beim Loeschen unwiederbringlich verloren geht -- das darf man
        # nicht erst hinterher erfahren.
        "with_caption": sum(1 for m in marked if m["caption_de"]),
        "with_person": sum(1 for m in marked if m["person_names"]),
    }


class EmptyTrashRequest(BaseModel):
    #: Leer = alles, was im Papierkorb liegt.
    photo_ids: list[str] = []
    #: Ohne das passiert nichts. Es gibt kein Zurueck.
    confirm: bool = False


@router.post("/empty")
def empty_trash(req: EmptyTrashRequest) -> dict:
    """Endgueltig loeschen: Datei, Indexpunkt, Gesichter, Vorschaubild.

    Es gibt kein Zurueck. Deshalb vier Vorkehrungen:

    * `confirm` muss gesetzt sein.
    * Geloescht wird nur, was wirklich im Papierkorb liegt. Kennungen in
      `photo_ids` waehlen aus dem Papierkorb aus, sie umgehen ihn nicht.
    * Geloescht wird nur innerhalb der Bibliothek (siehe `photo_root`).
    * Jeder geloeschte Pfad landet vorher in einem Protokoll unter `logs/`.
      Rueckgaengig macht das nichts, aber man kann hinterher sehen, was weg
      ist -- und das ist der Unterschied zwischen einem Fehler und einem
      Raetsel.

    Was die beiden mittleren Schranken aussortiert haben, steht als
    `skipped` und `outside` in der Antwort: stillschweigend weniger zu
    loeschen als verlangt waere genauso schlecht wie zu viel.
    """
    from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

    q = client()
    ids = req.photo_ids
    if not ids:
        ids = [p["id"] for p in list_trash(limit=1_000_000)["photos"]]
    if not ids:
        return {"deleted": 0, "note": "Der Papierkorb ist leer."}

    found = []
    for i in range(0, len(ids), 128):
        found.extend(q.retrieve(collection_name=PHOTOS, ids=ids[i : i + 128],
                                with_payload=True, with_vectors=False))

    # Zwei Schranken, beide vor der Bestätigungsfrage — damit die Vorschau
    # dieselbe Zahl nennt, die das Löschen dann trifft.
    #
    # Erstens: nur, was wirklich vorgemerkt ist. `req.photo_ids` wurde bisher
    # ungeprüft übernommen; die Papierkorb-Bedingung galt allein im Zweig
    # ohne Kennungen. Ein Aufruf mit beliebigen Kennungen und `confirm` hat
    # damit Fotos gelöscht, die nie im Papierkorb lagen — die erste, bewusst
    # umkehrbare Stufe ließ sich vollständig überspringen.
    #
    # Zweitens: nur innerhalb der Bibliothek. Der Pfad kommt aus dem Payload.
    root = photo_root()
    if not root:
        raise HTTPException(
            500,
            "Bibliothekswurzel unbekannt — es wird nichts gelöscht. sources.txt "
            "prüfen oder PHOTOVAULT_PHOTO_ROOT setzen.",
        )

    erlaubt, nicht_vorgemerkt, ausserhalb = [], [], []
    for point in found:
        payload = point.payload or {}
        if not payload.get(TRASH_KEY):
            nicht_vorgemerkt.append(str(point.id))
            continue
        path = payload.get("file_path") or ""
        if path and not under_root(path, root):
            ausserhalb.append(path)
            continue
        erlaubt.append(point)
    found = erlaubt

    # Immer dieselben Schluessel, egal welcher Weg genommen wird. Eine
    # Antwort, deren Gestalt vom Ausgang abhaengt, zwingt jeden Aufrufer zu
    # einer Fallunterscheidung -- und der Aufrufer vergisst sie.
    leer = {"deleted": 0, "files": 0, "thumbs": 0, "failed": [],
            "skipped": len(nicht_vorgemerkt), "outside": len(ausserhalb)}
    if not found:
        note = ("Nichts davon liegt im Papierkorb." if nicht_vorgemerkt or ausserhalb
                else "Keine dieser Kennungen steht im Index.")
        return {**leer, "note": note}
    if not req.confirm:
        return {**leer, "would_delete": len(found),
                "note": "Ohne confirm wird nichts gelöscht."}

    TRASH_LOG.mkdir(parents=True, exist_ok=True)
    record = TRASH_LOG / f"deleted-{int(datetime.now(timezone.utc).timestamp())}.log"
    deleted_files = 0
    failed: list[dict] = []
    with record.open("w", encoding="utf-8") as log:
        for point in found:
            payload = point.payload or {}
            path = payload.get("file_path") or ""
            log.write(f"{point.id}\t{path}\t{payload.get('caption_de') or ''}\n")
            if not path:
                continue
            try:
                Path(path).unlink()
                deleted_files += 1
            except FileNotFoundError:
                deleted_files += 1  # schon weg zaehlt als erledigt
            except Exception as e:
                failed.append({"path": path, "error": str(e)})

    photo_hashes = [(p.payload or {}).get("photo_id") for p in found]
    try:
        q.delete(collection_name=PHOTOS, points_selector=[str(p.id) for p in found], wait=True)
        for h in [h for h in photo_hashes if h]:
            q.delete(
                collection_name=FACES,
                points_selector=FilterSelector(filter=Filter(must=[
                    FieldCondition(key="photo_id", match=MatchValue(value=h))
                ])),
                wait=True,
            )
    except Exception as e:
        logger.exception("Trash delete failed")
        raise HTTPException(500, f"Index aufräumen fehlgeschlagen: {e}") from e

    thumbs = sum(drop_cached((p.payload or {}).get("file_path") or "") for p in found)
    return {
        "deleted": len(found),
        "files": deleted_files,
        "thumbs": thumbs,
        "failed": failed,
        "skipped": len(nicht_vorgemerkt),
        "outside": len(ausserhalb),
        "log": str(record),
    }
