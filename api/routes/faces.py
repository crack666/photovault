"""Gesichtsausschnitte und die Gesichter eines einzelnen Fotos.

Der Ausschnitt-Endpunkt bedient das Labeling. Der Listen-Endpunkt schließt
eine Lücke: „Gesichter ohne Namen“ arbeitet den Stapel von vorn ab, aber wenn
man ein Foto offen hat und darauf jemanden erkennt, gab es bisher keinen Weg,
das zu sagen. Hier kommen die Gesichter *dieses* Fotos zurück -- mit einem
Namensvorschlag aus dem Gesichtsvektor, damit man in der Regel nur bestätigen
muss statt zu tippen.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from qdrant_client.models import (
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
    PayloadField,
)

from api.qdrant_util import FACES, PHOTOS, client
from api.thumbs import get_thumb
from ingest.face_matcher import MATCH_THRESHOLD

logger = logging.getLogger(__name__)
router = APIRouter()

#: Mehr als das kann niemand sinnvoll gegeneinander abwaegen; die Liste ist
#: nach Aehnlichkeit sortiert, der erste Treffer ist fast immer der richtige.
MAX_SUGGESTIONS = 4

#: Sonderwerte aus dem Labeling: uebersprungen bzw. bewusst ignoriert. Beides
#: ist kein Name, aber ein Unterschied -- und beides muss hier wieder aufgehen.
STATE_LABELS = {"_skipped": "übersprungen", "_ignored": "ignoriert"}


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


def face_state(person_id: str | None) -> str:
    """`open`, `skipped`, `ignored` oder `named`.

    Übersprungen und ignoriert bleiben getrennt: das erste war ein
    „später“, das zweite ein „nie“. Beides lässt sich hier überschreiben,
    aber nur eines davon ist eine offene Aufgabe.
    """
    if not person_id:
        return "open"
    if person_id in STATE_LABELS:
        return person_id.lstrip("_")
    return "named"


def sort_key(box: list) -> tuple:
    """Gesichter in Leserichtung: zeilenweise von oben, darin links nach rechts.

    Ohne das ist die Reihenfolge die von Qdrant, also zufaellig -- und der
    Streifen unter dem Foto passt nicht zu dem, was man im Foto sieht. Die
    Zeilenbildung ist grob (200 px), damit ein leicht tieferer Kopf nicht in
    die naechste Zeile rutscht.
    """
    if len(box) != 4:
        return (1, 0, 0.0)
    return (0, int((box[1] + box[3]) / 2) // 200, float(box[0]))


def suggest_names(q, vector, *, limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """Benannte Personen mit aehnlichem Gesicht, bester Treffer je Person.

    Fehler werden hier *nicht* geschluckt: ein kaputter Index sieht sonst aus
    wie „niemand aehnelt dieser Person“, und man tippt Namen von Hand, die
    eigentlich vorgeschlagen worden waeren.
    """
    if not vector:
        return []
    hits = q.query_points(
        collection_name=FACES,
        query=vector,
        query_filter=Filter(
            must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
        ),
        limit=limit * 8,
        score_threshold=MATCH_THRESHOLD,
        with_payload=True,
    ).points
    best: dict[str, dict] = {}
    for hit in hits:
        payload = hit.payload or {}
        pid = payload.get("person_id")
        if not pid or pid.startswith("_"):
            continue
        score = round(float(hit.score), 4)
        if pid not in best or score > best[pid]["score"]:
            best[pid] = {
                "id": pid,
                "name": payload.get("person_name") or pid,
                "score": score,
                "example_face_id": str(hit.id),
            }
    return sorted(best.values(), key=lambda r: -r["score"])[:limit]


def resolve_photo_id(q, photo: str) -> str:
    """Punkt-ID der Fotosammlung -> `photo_id` aus dem Payload.

    Die Oberflaeche kennt ueberall nur die Punkt-ID (eine uuid5), der
    Gesichts-Payload verweist aber auf den sha256 der Datei. Wer den Hash
    schon hat, darf ihn direkt schicken -- die Form entscheidet, denn ein
    Hash ist fuer Qdrant keine gueltige Punkt-ID und ein Nachschlagen damit
    waere kein leeres Ergebnis, sondern ein Fehler.
    """
    try:
        uuid.UUID(photo)
    except (ValueError, AttributeError, TypeError):
        return photo
    points = q.retrieve(collection_name=PHOTOS, ids=[photo], with_payload=True)
    if not points:
        raise HTTPException(404, "Foto nicht im Index")
    found = (points[0].payload or {}).get("photo_id")
    if not found:
        raise HTTPException(500, "Foto ohne photo_id im Payload")
    return str(found)


@router.get("")
def faces_of_photo(photo: str, suggest: bool = True) -> dict:
    """Alle Gesichter eines Fotos, in Leserichtung, mit Vorschlaegen."""
    q = client()
    try:
        photo_id = resolve_photo_id(q, photo)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Foto nicht auflösbar: {e}") from e

    try:
        points, _ = q.scroll(
            collection_name=FACES,
            scroll_filter=Filter(
                must=[FieldCondition(key="photo_id", match=MatchValue(value=photo_id))]
            ),
            limit=64,
            with_payload=True,
            with_vectors=suggest,
        )
    except Exception as e:
        raise HTTPException(502, f"Gesichter nicht abrufbar: {e}") from e

    rows = []
    for point in points:
        payload = point.payload or {}
        box = payload.get("box") or []
        pid = payload.get("person_id")
        named = face_state(pid) == "named"
        px = min(box[2] - box[0], box[3] - box[1]) if len(box) == 4 else 0
        rows.append(
            {
                "face_id": str(point.id),
                "person_id": pid,
                "person_name": payload.get("person_name") if named else None,
                "state": face_state(pid),
                "state_label": STATE_LABELS.get(pid or "", ""),
                "box": box,
                "size_px": px,
                "score": round(float(payload.get("score") or 0), 3),
                "suggestions": [],
                "vector": point.vector if suggest else None,
            }
        )
    rows.sort(key=lambda r: sort_key(r["box"]))

    note = ""
    if suggest:
        # Nur fuer die ohne Namen -- bei den anderen waere der beste Vorschlag
        # die Person selbst.
        for row in [r for r in rows if r["state"] != "named"]:
            vec = row["vector"]
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            try:
                row["suggestions"] = suggest_names(q, vec)
            except Exception as e:
                logger.warning("Vorschlaege fuer %s fehlgeschlagen: %s", row["face_id"], e)
                note = f"Namensvorschläge nicht verfügbar: {e}"
                break
    for row in rows:
        row.pop("vector", None)

    return {
        "photo_id": photo_id,
        "faces": rows,
        "named": sum(1 for r in rows if r["state"] == "named"),
        "open": sum(1 for r in rows if r["state"] != "named"),
        "note": note,
    }


@router.get("/{face_id}/lookalikes")
def lookalikes(face_id: str, threshold: float = 0.0, limit: int = 24) -> dict:
    """Unbenannte Gesichter, die dieselbe Person zeigen wie dieses.

    Der eigentliche Hebel beim Benennen: gemessen an einer Stichprobe hat ein
    brauchbar großes Gesicht im Median zwei unbenannte Doppelgänger, manche
    fünf. Ein Name deckt damit typisch drei Gesichter statt einem -- aber nur,
    wenn man danach gefragt wird. Die Ausschnitte gehen immer mit zurück: bei
    Geschwistern und Kindern liegt die Ähnlichkeit hoch, das muss ein Mensch
    sehen und nicht eine Schwelle entscheiden.
    """
    from api.known_faces import DEFAULT_THRESHOLD

    try:
        uuid.UUID(face_id)
    except (ValueError, AttributeError, TypeError):
        # Sonst antwortet Qdrant mit einem 400 voller JSON-Rohtext, und das
        # steht dann so in der Oberflaeche.
        raise HTTPException(400, "Keine gültige Gesichts-ID") from None

    q = client()
    th = threshold or DEFAULT_THRESHOLD
    try:
        points = q.retrieve(collection_name=FACES, ids=[face_id], with_vectors=True)
    except Exception as e:
        raise HTTPException(502, f"Gesicht nicht abrufbar: {e}") from e
    if not points:
        raise HTTPException(404, "Gesicht nicht gefunden")
    vec = points[0].vector
    if isinstance(vec, dict):
        vec = next(iter(vec.values()), None)
    if not vec:
        raise HTTPException(409, "Gesicht ohne Vektor — Neu-Indizierung nötig")

    # Ein paar mehr holen als gezeigt werden: nur so ist die genannte Zahl die
    # echte und nicht die Fensterbreite.
    want = min(200, max(limit * 3, 30))
    try:
        hits = q.query_points(
            collection_name=FACES,
            query=vec,
            query_filter=Filter(
                must=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
            ),
            limit=want,
            score_threshold=th,
            with_payload=True,
        ).points
    except Exception as e:
        raise HTTPException(502, f"Suche fehlgeschlagen: {e}") from e

    rows = []
    for hit in hits:
        if str(hit.id) == str(face_id):
            continue
        payload = hit.payload or {}
        box = payload.get("box") or []
        rows.append(
            {
                "face_id": str(hit.id),
                "photo_id": payload.get("photo_id"),
                "score": round(float(hit.score), 4),
                "size_px": min(box[2] - box[0], box[3] - box[1]) if len(box) == 4 else 0,
            }
        )
    return {
        "threshold": th,
        "count": len(rows),
        "capped": len(hits) >= want,
        "faces": rows[:limit],
    }
