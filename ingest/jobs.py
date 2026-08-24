"""Job-Fortschritt in Qdrant, damit lange Läufe von außen sichtbar sind.

Ein Ingest über 50k Fotos läuft Stunden. Ohne Ablage sieht man den Stand nur
im Terminal des Prozesses, der ihn gestartet hat. Der Tracker schreibt
periodisch in die Collection `ingest_jobs`; API und Übersichtsseite lesen von
dort. Bewusst generisch gehalten (`kind`), damit auch Caption-Nachläufe oder
Re-Embeds dieselbe Anzeige benutzen können.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = os.environ.get("PHOTOVAULT_JOBS_COLLECTION", "ingest_jobs")
#: Qdrant verlangt einen Vektor pro Punkt; hier reine Metadaten. Die Groesse
#: richtet sich nach der vorhandenen Collection -- passt sie nicht, lehnt
#: Qdrant jeden Upsert ab.
DEFAULT_VECTOR_SIZE = 4
FLUSH_SECONDS = 2.0


class JobTracker:
    """Schreibt den Stand eines Laufs nach Qdrant. Fehler hier dürfen den Lauf nie stoppen."""

    def __init__(
        self,
        client,
        kind: str,
        source: str = "",
        collection: str = COLLECTION,
        job_id: Optional[str] = None,
        detail: Optional[dict] = None,
    ):
        self.client = client
        self.collection = collection
        self.job_id = job_id or str(uuid.uuid4())
        self.kind = kind
        self.source = source
        self.detail = detail or {}
        self.started_at = time.time()
        self.vector_size = DEFAULT_VECTOR_SIZE
        self._last_flush = 0.0
        self._warned = False
        self._state: dict[str, Any] = {
            "phase": "starting",
            "total": 0,
            "processed": 0,
            "errors": 0,
            "skipped": 0,
        }
        self._ready = self._ensure_collection()
        self.update(phase="starting", force=True)

    def _ensure_collection(self) -> bool:
        try:
            info = self.client.get_collection(self.collection)
            params = getattr(info.config.params, "vectors", None)
            size = getattr(params, "size", None)
            if isinstance(params, dict):
                first = next(iter(params.values()), None)
                size = getattr(first, "size", None)
            self.vector_size = int(size or DEFAULT_VECTOR_SIZE)
            return True
        except Exception:
            pass
        try:
            from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

            self.vector_size = DEFAULT_VECTOR_SIZE
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )
            for field in ("kind", "status", "job_id"):
                try:
                    self.client.create_payload_index(
                        self.collection, field_name=field, field_schema=PayloadSchemaType.KEYWORD
                    )
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.warning("Job tracking disabled (collection unavailable): %s", e)
            return False

    def update(self, force: bool = False, **fields: Any) -> None:
        self._state.update({k: v for k, v in fields.items() if v is not None})
        now = time.time()
        if not force and now - self._last_flush < FLUSH_SECONDS:
            return
        self._last_flush = now
        self._write("running")

    def finish(self, status: str = "done", **fields: Any) -> None:
        self._state.update({k: v for k, v in fields.items() if v is not None})
        self._write(status, finished=True)

    def _write(self, status: str, finished: bool = False) -> None:
        if not self._ready:
            return
        elapsed = time.time() - self.started_at
        processed = int(self._state.get("processed") or 0)
        total = int(self._state.get("total") or 0)
        rate = processed / elapsed if elapsed > 0 else 0.0
        remaining = max(0, total - processed)
        payload = {
            "job_id": self.job_id,
            "kind": self.kind,
            "source": self.source,
            "status": status,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "finished_at": time.time() if finished else None,
            "elapsed_s": round(elapsed, 1),
            "rate_per_s": round(rate, 3),
            "eta_s": round(remaining / rate, 0) if rate > 0 and not finished else 0,
            "percent": round(processed / total * 100, 1) if total else 0.0,
            **self._state,
            **self.detail,
        }
        try:
            from qdrant_client.models import PointStruct

            self.client.upsert(
                collection_name=self.collection,
                points=[
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, self.job_id)),
                        vector=[0.0] * self.vector_size,
                        payload=payload,
                    )
                ],
                wait=False,
            )
        except Exception as e:
            # Einmal warnen, nicht bei jedem Tick -- aber nicht verschlucken,
            # sonst bleibt die Fortschrittsseite unerklaerlich leer.
            if not self._warned:
                self._warned = True
                logger.warning("Job progress write failed, page will stay empty: %s", e)


def as_epoch(value: Any) -> float:
    """Zeitstempel als Unix-Sekunden. Ältere Einträge tragen ISO-Strings."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def list_jobs(client, collection: str = COLLECTION, limit: int = 50) -> list[dict]:
    """Alle bekannten Jobs, neueste zuerst."""
    try:
        points, _ = client.scroll(
            collection_name=collection, limit=limit, with_payload=True, with_vectors=False
        )
    except Exception as e:
        logger.debug("Job listing failed: %s", e)
        return []
    jobs = [p.payload or {} for p in points]
    now = time.time()
    for job in jobs:
        job["started_at"] = as_epoch(job.get("started_at"))
        job["updated_at"] = as_epoch(job.get("updated_at"))
        job["finished_at"] = as_epoch(job.get("finished_at")) or None
        # Ein Lauf, dessen Prozess gestorben ist, bleibt sonst ewig "running".
        if job.get("status") == "running" and now - job["updated_at"] > 120:
            job["status"] = "stale"
    jobs.sort(key=lambda j: j["started_at"], reverse=True)
    return jobs
