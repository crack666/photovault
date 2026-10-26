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
import signal
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Eigener Name, weil PhotoVault sich das Qdrant laut README mit dem ai-stack
#: teilt — und dessen RAG-Ingest schreibt sein Protokoll unter demselben
#: generischen Vorgabenamen `ingest_jobs`. Am 26.10.2026 standen dort 44
#: Punkte, 25 davon fremd: sie tragen `progress.files_total` statt
#: `total`/`processed` und rendern deshalb als `0 / 0 (0.0%)`. Gefaehrlicher
#: als die Anzeige war der Aufraeumen-Knopf, der sie mitgeloescht haette.
COLLECTION = os.environ.get("PHOTOVAULT_JOBS_COLLECTION", "photovault_jobs")

#: Qdrant verlangt einen Vektor pro Punkt; hier reine Metadaten. Die Groesse
#: richtet sich nach der vorhandenen Collection -- passt sie nicht, lehnt
#: Qdrant jeden Upsert ab.
DEFAULT_VECTOR_SIZE = 4
FLUSH_SECONDS = 2.0

#: Wie viele gescheiterte Dateien ein Eintrag namentlich mitfuehrt. Ein Lauf
#: ueber 50.000 Dateien kann tausende Fehler haben, und alle zu speichern
#: bliese den Qdrant-Punkt auf. Die ersten paar reichen, um zu erkennen, *was
#: fuer eine Sorte* Datei es trifft -- wie viele es insgesamt waren, sagt
#: weiterhin der Zaehler.
MAX_FAILURES = 20

#: Welches `-m`-Modul ein Lauf in der Kommandozeile tragen muss. Ohne den
#: Abgleich koennte ein wiederverwendetes PID einen fremden Prozess treffen —
#: im Docker-Container waere PID 1 der API-Server.
KIND_MARKERS = {
    "ingest": ("ingest.pipeline",),
    # Derselbe Lauf, nur mit Beschreibungen: `pipeline` waehlt die Kennung
    # danach, ob `--skip-caption` gesetzt ist. Sie fehlte hier, und als
    # `list_jobs` anfing fremde Arten zu ueberspringen, waere ein Ingest mit
    # Beschreibungen damit von der Seite verschwunden.
    "ingest-caption": ("ingest.pipeline",),
    "faces": ("ingest.face_pass",),
    "caption": ("ingest.caption_pass",),
    "reembed": ("tools.reembed_all",),
    "thumbs": ("tools.thumbs",),
    "atlas": ("tools.atlas_build",),
}

#: Arten, die aus diesem Programm stammen. Alles andere in der Collection ist
#: fremd — `list_jobs` laesst es liegen, statt es als Nullzeile zu zeigen.
#: Die eigene Collection oben macht das im Normalfall unnoetig; diese Pruefung
#: faengt den Fall ab, dass jemand `PHOTOVAULT_JOBS_COLLECTION` wieder auf eine
#: geteilte Collection stellt.
#:
#: Abgeleitet und nicht abgeschrieben: eine zweite, von Hand gepflegte Liste
#: derselben Kennungen driftet, und hier kostet jede Abweichung Sichtbarkeit —
#: eine Art, die oben fehlt, verschwindet unten von der Seite.
OWN_KINDS = frozenset(KIND_MARKERS)


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
        self._install_stop_handler()
        self.update(phase="starting", force=True)

    def _install_stop_handler(self) -> None:
        """SIGTERM als Abbruch schreiben, bevor der Prozess stirbt.

        Nur im Kind, das die Jobs-Seite gestartet hat (`PHOTOVAULT_JOB_CHILD`).
        Tests und der API-Prozess selbst duerfen den Handler nicht erben —
        sonst faengt pytest das naechste SIGTERM.
        """
        if os.environ.get("PHOTOVAULT_JOB_CHILD") != "1":
            return

        def _stop(signum, _frame):
            try:
                self.finish("aborted", phase="aborted")
            except Exception:
                pass
            raise SystemExit(128 + int(signum))

        try:
            signal.signal(signal.SIGTERM, _stop)
        except (ValueError, OSError):
            return

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

    def fail(self, path: Any, reason: Any) -> None:
        """Eine gescheiterte Datei merken, nicht nur zaehlen.

        `1 Fehler` auf der Uebersichtsseite laesst ratlos zurueck: welche
        Datei, und woran? Der Grund stand bisher ausschliesslich im Terminal
        dessen, der den Lauf gestartet hat -- und wer ihn ueber die Oberflaeche
        startet, hat gar keins. Ein Lauf, der `done-with-errors` meldet und
        verschweigt was, ist schlechter als einer, der nichts meldet: er macht
        auf ein Problem aufmerksam, das man anschliessend nicht finden kann.
        """
        liste = self._state.setdefault("failures", [])
        if len(liste) < MAX_FAILURES:
            liste.append({"path": str(path), "reason": str(reason)[:200]})

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
    # Aussortiert wird nur, was sich ausdruecklich als fremd ausweist. Ein
    # Eintrag ganz ohne `kind` stammt aus einer aelteren Fassung dieses
    # Programms und gehoert uns -- ihn wegzuwerfen hiesse, eigene Historie zu
    # verlieren, um fremde loszuwerden.
    fremd = [j for j in jobs if j.get("kind") and j["kind"] not in OWN_KINDS]
    if fremd:
        logger.debug("%d fremde Job-Eintraege in '%s' uebersprungen (Arten: %s)",
                     len(fremd), collection,
                     sorted({str(j.get("kind")) for j in fremd}))
        jobs = [j for j in jobs if not (j.get("kind") and j["kind"] not in OWN_KINDS)]
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


def job_point_id(job_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, job_id))


def proc_cmdline(pid: int) -> str:
    """Kommandozeile aus /proc — leer, wenn es den Prozess hier nicht gibt.

    Nativ und Docker teilen sich denselben Weg: der Lauf ist ein Kind *dieses*
    API-Prozesses, also dieselbe PID-Namespace. Ein `pkill` ueber den Host
    wuerde im Container niemanden finden, und umgekehrt.
    """
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def cmdline_is_job(cmd: str, kind: str) -> bool:
    """Ob diese Kommandozeile der erwartete Lauf ist — und nicht uvicorn."""
    if not cmd or "uvicorn" in cmd:
        return False
    markers = KIND_MARKERS.get(kind) or ()
    return any(m in cmd for m in markers)


def pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Die Prozessgruppe des Laufs, nicht die ganze Maschine.

    `Popen(..., start_new_session=True)` macht den Kindprozess zum Leiter
    seiner Gruppe. ffmpeg-Kinder erben die Gruppe. SIGTERM daran reicht —
    `pkill` waere blind fuer Namen und traefe im Zweifel den API-Server.
    PID 1 ist im Container uvicorn; den fassen wir nie an.
    """
    if pid <= 1:
        raise ValueError("PID 1 ist der Dienst, kein Lauf")
    if hasattr(os, "killpg"):
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            pass
        except PermissionError:
            raise
    os.kill(pid, sig)


def mark_job_status(client, job_id: str, status: str, **fields: Any) -> None:
    """Status setzen, ohne den Tracker des Kindes zu brauchen.

    Nach SIGKILL kommt kein `finish` mehr. Die Seite darf dann nicht auf
    den 120-Sekunden-Stale-Fall warten.
    """
    payload = {
        "status": status,
        "updated_at": time.time(),
        "finished_at": time.time() if status != "running" else None,
        **fields,
    }
    try:
        client.set_payload(
            collection_name=COLLECTION,
            payload=payload,
            points=[job_point_id(job_id)],
            wait=True,
        )
    except Exception as e:
        logger.warning("Could not mark job %s as %s: %s", job_id, status, e)
