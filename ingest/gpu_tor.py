"""Die Ablehnung des GPU-Tors erkennen und einen Lauf pausieren statt scheitern.

LiteLLM fragt vor jeder Anfrage an ein lokales Modell das GPU-Tor
(ai-stack `docs/LITELLM_GPU_TOR.md`, Vault `Patterns/GPU-Tor`). Laeuft ein
Spiel, rendert ComfyUI oder gilt eine fremde Reservierung, antwortet es sofort
mit HTTP 503, einem Text ab „GPU belegt“ und `Retry-After`. Es haelt nichts
fest; das Warten gehoert in den Lauf selbst.

Fuer PhotoVault ist das ein Normalfall: ein Batch wartet, bis die Karte frei
ist, zeigt den Grund im Job-Eintrag und verbucht nichts als Fehler. Eine 503
ohne „GPU belegt“ bleibt ein echter Fehler.

Betroffen sind nur Anfragen an den Pool `local` (Captions, Textfragen).
Embeddings (`embedder`) laesst LiteLLM nie am Tor warten -- Suche und
Re-Embed laufen auch waehrend eines Spiels.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

#: So beginnt der Text jeder Ablehnung durch das Tor.
MARKER = "GPU belegt"
#: Fehlertyp, den der Hook mitschickt.
ERROR_TYPE = "gpu_busy"
#: Falls `Retry-After` fehlt oder Unsinn enthaelt.
DEFAULT_RETRY_AFTER = 30
#: Laenger schlaeft ein pausierter Lauf nicht am Stueck: der Job-Eintrag muss
#: sich melden, sonst haelt ihn die Jobs-Seite nach 120 s fuer tot (`stale`).
HEARTBEAT_SECONDS = 60

T = TypeVar("T")


class GpuBusy(RuntimeError):  # noqa: N818 -- ein Wartegrund, kein Fehler
    """Das GPU-Tor hat die Anfrage abgelehnt -- ein Wartegrund, kein Fehler."""

    def __init__(self, reason: str, retry_after: int = DEFAULT_RETRY_AFTER):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def from_http_error(
    code: int, body: str, headers: Mapping[str, str] | None = None
) -> GpuBusy | None:
    """`GpuBusy`, wenn die Antwort die Ablehnung des Tors ist, sonst `None`."""
    if code != 503:
        return None
    message, kind = _error_fields(body)
    if kind != ERROR_TYPE and MARKER not in message:
        return None
    start = message.find(MARKER)
    reason = (message[start:] if start >= 0 else message).strip() or MARKER
    return GpuBusy(reason, _retry_after(headers))


def _error_fields(body: str) -> tuple[str, str]:
    """Text und Typ aus der Fehlerantwort; LiteLLM schickt `{"error": {...}}`."""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return str(body or ""), ""
    err: Any = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        message = err.get("message") or err.get("detail") or ""
        return str(message), str(err.get("type") or "")
    return str(err or ""), ""


def _retry_after(headers: Mapping[str, str] | None) -> int:
    raw = None
    if headers is not None:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return value if value > 0 else DEFAULT_RETRY_AFTER


class Pause:
    """Wartet nach einer Ablehnung `Retry-After` Sekunden und haelt den Job aktuell.

    Der Job-Eintrag bleibt `running` (Beenden-Knopf, Zaehler und Aufraeumen
    kennen nur diesen Zustand) und traegt waehrend der Pause `paused` mit dem
    Grund und `resume_at`. Danach leert der Lauf beide Felder und macht weiter.
    """

    def __init__(
        self,
        job=None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.job = job
        self._sleep = sleep
        self._clock = clock
        self.count = 0

    def __call__(self, busy: GpuBusy) -> None:
        self.count += 1
        wait = max(1, int(busy.retry_after))
        resume_at = self._clock() + wait
        logger.warning("Lauf pausiert, %s -- neuer Versuch in %d s", busy.reason, wait)
        self._mark(busy.reason, resume_at)
        left = float(wait)
        while left > 0:
            step = min(left, HEARTBEAT_SECONDS)
            self._sleep(step)
            left -= step
            if left > 0:
                self._mark(busy.reason, resume_at)
        self._mark("", 0)

    def _mark(self, reason: str, resume_at: float) -> None:
        if self.job is None:
            return
        try:
            self.job.update(paused=reason, resume_at=round(resume_at, 0), force=True)
        except Exception as e:  # Job-Tracking darf den Lauf nie stoppen.
            logger.debug("Job-Pause nicht geschrieben: %s", e)


def call_with_pause(fn: Callable[[], T], pause: Callable[[GpuBusy], None]) -> T:
    """`fn` aufrufen; lehnt das Tor ab, pausieren und dieselbe Anfrage wiederholen.

    Ein Versuch je `Retry-After` -- kein Wiederholsturm. Es gibt bewusst keine
    Obergrenze: ein Batch, der waehrend eines Spiels startet, soll danach
    weiterlaufen, nicht aufgeben. Beenden geht wie immer ueber die Jobs-Seite.
    """
    while True:
        try:
            return fn()
        except GpuBusy as busy:
            pause(busy)
