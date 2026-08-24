"""Ingest Routes: Progress, Start, Status."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from api.qdrant_util import PHOTOS, client
from ingest.jobs import list_jobs

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
