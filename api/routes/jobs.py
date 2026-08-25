"""Laufende und vergangene Jobs — ansehen, aufräumen, anstoßen.

Die langen Läufe (Vision-Captions, Text-Vektoren, Karte) dauern Stunden und
liefen bisher nur im Terminal. Wer sie von der Fortschrittsseite aus starten
kann, braucht dafür keine Shell auf dem Rechner mit den Fotos.

**Was gestartet werden darf, steht in `RUNNABLE` und nirgends sonst.** Die
Oberfläche hat keine Anmeldung und ist über Tailscale auch von außerhalb
erreichbar; diese Liste ist die einzige Schranke. Aufgerufen wird ohne Shell
(`Popen` mit Argumentliste), und jedes Argument entsteht hier aus einem
getippten Feld — nie aus einer Zeichenkette des Aufrufers.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.qdrant_util import client
from ingest.jobs import COLLECTION, list_jobs

logger = logging.getLogger(__name__)
router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = Path(os.environ.get("PHOTOVAULT_JOB_LOGS", ROOT / "logs"))


class Runnable(BaseModel):
    """Ein Lauf, der von der Oberfläche aus gestartet werden darf."""

    module: str
    label: str
    note: str
    #: Kind-Kennung, unter der der Lauf in der Liste erscheint. Läuft schon
    #: einer davon, wird kein zweiter gestartet.
    kind: str
    #: Braucht Ollama und damit die Grafikkarte.
    gpu: bool = False
    #: Welche Schalter das Werkzeug wirklich kennt. `atlas_build` hat keinen
    #: Trockenlauf -- ihm einen mitzugeben ließe den Start unerklärlich
    #: scheitern, mit dem Fehler im Protokoll statt in der Antwort.
    flags: tuple[str, ...] = ()


RUNNABLE: dict[str, Runnable] = {
    "caption": Runnable(
        module="ingest.caption_pass", kind="caption", gpu=True,
        flags=("dry_run", "limit"),
        label="Bildbeschreibungen erzeugen",
        note="Vision-Modell über alle Fotos ohne Beschreibung. Stunden, nicht Minuten.",
    ),
    "reembed": Runnable(
        module="tools.reembed_all", kind="reembed", gpu=True,
        flags=("dry_run", "limit"),
        label="Text-Vektoren neu bauen",
        note="Nach neuen Captions, Namen, Notizen — oder wenn sich die Regel geändert hat.",
    ),
    "atlas": Runnable(
        module="tools.atlas_build", kind="atlas",
        flags=("limit",),
        label="Karte neu rechnen",
        note="UMAP über die CLIP-Vektoren. Kein Trockenlauf. "
             "Braucht umap-learn (pip install 'photovault[atlas]').",
    ),
}

#: Arten, die sich die Grafikkarte teilen müssten.
GPU_KINDS = frozenset(r.kind for r in RUNNABLE.values() if r.gpu)


@router.get("")
def all_jobs(limit: int = 20, offset: int = 0, kind: Optional[str] = None) -> dict:
    """Jobs, neueste zuerst, seitenweise.

    Sortiert wird über *alle* Einträge und erst danach geschnitten. Vorher
    holte die Liste `limit` beliebige Punkte aus Qdrant und sortierte nur die
    — „neueste zuerst" galt damit nur, solange es weniger Jobs gab als das
    Limit.
    """
    jobs = list_jobs(client(), limit=10_000)
    if kind:
        jobs = [j for j in jobs if j.get("kind") == kind]
    running = [j for j in jobs if j.get("status") == "running"]
    page = jobs[offset : offset + max(1, limit)]
    return {
        "jobs": page,
        "running": len(running),
        "total": len(jobs),
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "kinds": sorted({j.get("kind") or "?" for j in jobs}),
        "runnable": [
            {"id": key, "label": r.label, "note": r.note, "gpu": r.gpu,
             "dry_run": "dry_run" in r.flags,
             "busy": any(j.get("kind") == r.kind for j in running)}
            for key, r in RUNNABLE.items()
        ],
    }


@router.get("/{job_id}")
def one_job(job_id: str) -> dict:
    for job in list_jobs(client(), limit=10_000):
        if job.get("job_id") == job_id:
            return job
    raise HTTPException(404, "Job nicht gefunden")


@router.delete("/{job_id}")
def forget_job(job_id: str) -> dict:
    """Einen Eintrag aus der Liste nehmen.

    Nur den Eintrag — der Lauf selbst ist längst vorbei. Ein *laufender* Job
    wird nicht gelöscht: sonst verschwindet die Anzeige, während der Prozess
    weiterarbeitet, und niemand sieht mehr, was passiert.
    """
    q = client()
    for job in list_jobs(q, limit=10_000):
        if job.get("job_id") != job_id:
            continue
        if job.get("status") == "running":
            raise HTTPException(409, "Der Lauf läuft noch — erst beenden, dann aufräumen.")
        try:
            from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

            q.delete(
                collection_name=COLLECTION,
                points_selector=FilterSelector(filter=Filter(must=[
                    FieldCondition(key="job_id", match=MatchValue(value=job_id))
                ])),
                wait=True,
            )
        except Exception as e:
            logger.exception("Job delete failed")
            raise HTTPException(500, f"Löschen fehlgeschlagen: {e}") from e
        return {"deleted": job_id}
    raise HTTPException(404, "Job nicht gefunden")


class PruneRequest(BaseModel):
    """Nach Kategorie, nicht nach Zustandsnamen.

    Im Bestand stehen Zustände aus mehreren Programmgenerationen -- `done`,
    `succeeded`, `partial`, `done-with-errors`. Eine Namensliste hier haette
    41 von 47 Eintraegen nicht angefasst, ohne das zu sagen. Es gibt genau eine
    verlaessliche Trennung: laeuft noch, oder laeuft nicht mehr.
    """

    #: `aborted` = nur Abgebrochenes (kein Lebenszeichen mehr).
    #: `finished` = alles, was nicht mehr laeuft.
    what: Literal["aborted", "finished"] = "aborted"
    #: Nur Einträge, die älter sind als so viele Stunden. 0 = alle.
    older_than_hours: float = 0.0
    #: Die jüngsten N je Art immer behalten -- man will sehen, was zuletzt lief.
    keep_per_kind: int = Field(3, ge=0)


def doomed_jobs(
    jobs: list[dict], what: str, older_than_hours: float, keep_per_kind: int,
    now: float | None = None,
) -> list[str]:
    """Welche Einträge dürfen weg? Ohne Qdrant, damit die Regel prüfbar ist.

    `jobs` kommt neueste-zuerst herein -- daran haengt, welche drei je Art
    stehen bleiben.
    """
    cutoff = (now or time.time()) - older_than_hours * 3600 if older_than_hours else None
    kept_per_kind: dict[str, int] = {}
    doomed: list[str] = []
    for job in jobs:
        kind = job.get("kind") or "?"
        seen = kept_per_kind.get(kind, 0)
        status = job.get("status")
        removable = status != "running" and (what == "finished" or status == "stale")
        too_young = cutoff is not None and (job.get("updated_at") or 0) > cutoff
        if seen < keep_per_kind or not removable or too_young:
            kept_per_kind[kind] = seen + 1
            continue
        doomed.append(job.get("job_id"))
    return doomed


@router.post("/prune")
def prune_jobs(req: PruneRequest) -> dict:
    """Abgeschlossene und abgebrochene Einträge wegräumen.

    Die Liste wächst mit jedem Lauf und jedem Abbruch; nach ein paar Wochen
    stehen dort fünfzig Zeilen, von denen zwei interessieren.
    """
    q = client()
    jobs = list_jobs(q, limit=10_000)
    doomed = doomed_jobs(jobs, req.what, req.older_than_hours, req.keep_per_kind)

    if not doomed:
        return {"deleted": 0, "kept": len(jobs)}
    try:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchAny

        q.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(filter=Filter(must=[
                FieldCondition(key="job_id", match=MatchAny(any=doomed))
            ])),
            wait=True,
        )
    except Exception as e:
        logger.exception("Job prune failed")
        raise HTTPException(500, f"Aufräumen fehlgeschlagen: {e}") from e
    return {"deleted": len(doomed), "kept": len(jobs) - len(doomed)}


class RunRequest(BaseModel):
    job: Literal["caption", "reembed", "atlas"]
    #: Trockenlauf, wo das Werkzeug einen anbietet -- zählt und schätzt, schreibt nichts.
    dry_run: bool = False
    #: Obergrenze, um in Häppchen zu arbeiten.
    limit: Optional[int] = Field(None, ge=1, le=1_000_000)


def build_argv(spec: Runnable, *, dry_run: bool, limit: Optional[int]) -> list[str]:
    """Die Kommandozeile. Alles entsteht hier, nichts kommt vom Aufrufer durch.

    Kein `shell=True`, keine Zeichenkette -- eine Liste, deren Inhalt aus
    `RUNNABLE` und aus getippten Feldern stammt. Ein Schalter, den das Werkzeug
    nicht kennt, wird weggelassen statt weitergereicht: sonst scheitert der
    Start unerklaerlich, mit dem Fehler im Protokoll statt in der Antwort.
    """
    argv = [sys.executable, "-m", spec.module]
    if dry_run and "dry_run" in spec.flags:
        argv.append("--dry-run")
    if limit and "limit" in spec.flags:
        argv += ["--limit", str(int(limit))]
    return argv


@router.post("/run")
def run_job(req: RunRequest) -> dict:
    """Einen Lauf starten. Losgelöst, damit er die Anfrage überlebt."""
    spec = RUNNABLE[req.job]
    if req.dry_run and "dry_run" not in spec.flags:
        raise HTTPException(400, f"{spec.label}: kennt keinen Trockenlauf.")

    running = [j for j in list_jobs(client(), limit=10_000) if j.get("status") == "running"]
    busy = {j.get("kind") for j in running}
    if spec.kind in busy:
        raise HTTPException(409, f"{spec.label}: läuft bereits.")
    if spec.gpu and busy & GPU_KINDS:
        raise HTTPException(
            409,
            "Ein anderer Lauf belegt die Grafikkarte. Zwei gleichzeitig machen "
            "beide langsamer, nicht schneller.",
        )

    argv = build_argv(spec, dry_run=req.dry_run, limit=req.limit)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{req.job}-{int(time.time())}.log"
    try:
        with log_path.open("ab") as log:
            proc = subprocess.Popen(  # noqa: S603 -- feste Liste, keine Shell
                argv, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
    except Exception as e:
        logger.exception("Job start failed")
        raise HTTPException(500, f"Start fehlgeschlagen: {e}") from e

    logger.info("Job %s gestartet (pid %s): %s", req.job, proc.pid, " ".join(argv))
    return {
        "started": req.job,
        "pid": proc.pid,
        "log": str(log_path),
        "note": "Der Fortschritt erscheint in dieser Liste, sobald der Lauf sich meldet."
                if not req.dry_run else
                "Trockenlauf — das Ergebnis steht nur im Protokoll, nicht in der Liste.",
    }
