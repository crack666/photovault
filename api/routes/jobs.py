"""Laufende und vergangene Jobs — für die Fortschrittsseite."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from api.qdrant_util import client
from ingest.jobs import list_jobs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
def all_jobs(limit: int = 50) -> dict:
    jobs = list_jobs(client(), limit=limit)
    running = [j for j in jobs if j.get("status") == "running"]
    return {
        "jobs": jobs,
        "running": len(running),
        "total": len(jobs),
    }


@router.get("/{job_id}")
def one_job(job_id: str) -> dict:
    for job in list_jobs(client(), limit=200):
        if job.get("job_id") == job_id:
            return job
    raise HTTPException(404, "Job nicht gefunden")
