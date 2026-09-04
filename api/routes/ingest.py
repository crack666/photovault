"""Ingest Routes: Progress, Start, Status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import PHOTOS, client
from ingest.jobs import list_jobs

logger = logging.getLogger(__name__)

router = APIRouter()


class IngestStartRequest(BaseModel):
    source: str
    batch_size: int = 50


@router.post("/start")
def start_ingest(req: IngestStartRequest) -> dict:
    return {"status": "started", "source": req.source}


@router.get("/progress")
def get_progress() -> dict:
    """Stand des jüngsten Ingest-Laufs, aus der Job-Collection."""
    jobs = [j for j in list_jobs(client(), limit=50) if str(j.get("kind", "")).startswith("ingest")]
    if not jobs:
        return {
            "total": 0, "processed": 0, "skipped": 0, "errors": 0,
            "phase": "idle", "percent": 0.0, "status": "idle",
        }
    job = next((j for j in jobs if j.get("status") == "running"), jobs[0])
    return {
        "total": job.get("total", 0),
        "processed": job.get("processed", 0),
        "skipped": job.get("skipped", 0),
        "errors": job.get("errors", 0),
        "phase": job.get("phase", "idle"),
        "percent": job.get("percent", 0.0),
        "status": job.get("status", "idle"),
        "rate_per_s": job.get("rate_per_s", 0.0),
        "eta_s": job.get("eta_s", 0),
        "job_id": job.get("job_id"),
        "source": job.get("source"),
    }


@router.get("/stats")
def get_stats() -> dict:
    try:
        count = client().count(PHOTOS, exact=True).count
    except Exception:
        count = 0
    return {"total_photos": count}


#: Was fehlt, was das bedeutet, und was es behebt. Reihenfolge = Wichtigkeit.
_GAPS = (
    ("text", "Freitextsuche findet sie nicht",
     "Text-Vektoren neu bauen — braucht Ollama"),
    ("clip", "keine Ähnlichkeitssuche, nicht auf der Karte",
     "Datei prüfen: meist beschädigt oder nicht lesbar"),
)


@router.get("/state")
def index_state() -> dict:
    """Was im Index fehlt — abgefragt, nicht mitgezählt.

    Schlägt das Text-Embedding während eines Ingest fehl (kein Ollama, und ohne
    Grafikkarte hat das mancher gar nicht), schreibt die Pipeline den Punkt
    **ohne** Textvektor weiter und sagt nur eine Warnung pro Stapel. Man merkt
    es erst Wochen später an einer Freitextsuche, die nichts findet.

    Ein mitlaufender Zähler wäre die falsche Antwort: er gilt nur für den einen
    Lauf und läuft mit der Wirklichkeit auseinander. Der Zustand dagegen ist
    jederzeit ableitbar und kann nicht veralten.
    """
    from qdrant_client.models import Filter, HasVectorCondition

    q = client()
    try:
        total = q.count(collection_name=PHOTOS, exact=True).count
    except Exception as e:
        return {"total": 0, "gaps": [], "error": str(e)}

    gaps = []
    for name, means, remedy in _GAPS:
        try:
            have = q.count(
                collection_name=PHOTOS, exact=True,
                count_filter=Filter(must=[HasVectorCondition(has_vector=name)]),
            ).count
        except Exception:
            continue
        if total - have > 0:
            gaps.append({
                "vector": name, "missing": total - have,
                "means": means, "remedy": remedy,
            })
    return {"total": total, "gaps": gaps}


@router.get("/gaps/{vector}")
def gap_files(vector: str, limit: int = 200) -> dict:
    """Welche Fotos diesem Vektor fehlen -- namentlich.

    Die Zustandszeile sagte "59 von 14.593 Fotos ohne clip-Vektor" und daneben
    "Datei pruefen: meist beschaedigt oder nicht lesbar". Das ist ein Hinweis,
    mit dem man nichts anfangen kann: welche 59? Ohne die Liste ist die
    Meldung nicht mehr als "es gibt ein Problem, finde es selbst heraus".

    Also die Pfade, und dazu was der Index sonst ueber sie weiss -- die
    Dateigroesse und ein etwaiger Warnvermerk sagen meist schon, woran es
    liegt: 0 Bytes, abgeschnittenes JPEG, oder eine Datei, die es nicht mehr
    gibt.
    """
    from qdrant_client.models import Filter, HasVectorCondition

    bekannt = {name for name, _m, _r in _GAPS}
    if vector not in bekannt:
        raise HTTPException(404, f"Kein solcher Vektor: {vector}. "
                                 f"Bekannt: {', '.join(sorted(bekannt))}")

    q = client()
    fehlend, offset = [], None
    try:
        while len(fehlend) < limit:
            batch, offset = q.scroll(
                collection_name=PHOTOS, limit=256, offset=offset,
                with_payload=["file_path", "file_size", "file_warning", "date",
                              "folder_name", "content_sha256"],
                with_vectors=False,
                scroll_filter=Filter(must_not=[HasVectorCondition(has_vector=vector)]),
            )
            for p in batch:
                pl = p.payload or {}
                fehlend.append({
                    "id": str(p.id),
                    "file_path": pl.get("file_path"),
                    "file_size": pl.get("file_size"),
                    "warning": pl.get("file_warning"),
                    "date": pl.get("date"),
                    "folder_name": pl.get("folder_name"),
                    # Kein Inhalts-Hash heisst: die Datei war beim Nachtragen
                    # nicht lesbar. Das ist oft dieselbe Ursache.
                    "hashed": bool(pl.get("content_sha256")),
                })
            if offset is None:
                break
    except Exception as e:
        logger.exception("Luecken-Liste fehlgeschlagen")
        raise HTTPException(502, f"Nicht abrufbar: {type(e).__name__}: {e}") from e

    return {
        "vector": vector,
        "returned": len(fehlend[:limit]),
        "photos": fehlend[:limit],
        "truncated": offset is not None,
    }
