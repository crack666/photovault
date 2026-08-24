"""Ingest als Fließband statt als Reihe von Einzelschritten.

Die Stufen belegen ganz verschiedene Ressourcen: Lesen hängt an der
SMB-Latenz, Dekodieren an der CPU, Gesichter und CLIP an der GPU, Einbetten
und Schreiben am Netzwerk. Nacheinander ausgeführt wartet immer fast alles auf
eine Sache -- gemessen 135 ms I/O + 105 ms GPU + 38 ms Netz pro Foto, während
die GPU nur ein Drittel der Zeit etwas zu tun hat.

Als Fließband mit begrenzten Warteschlangen laufen sie gleichzeitig, und statt
der Summe zählt die langsamste Stufe:

    Lesen+Dekodieren (Pool)  ──►  Queue  ──►  GPU (ein Thread)  ──►  Queue  ──►  Einbetten+Schreiben (Pool)

Warum die GPU-Stufe einen einzelnen Thread hat: Die CUDA-Session von
onnxruntime ist nicht dafür gedacht, von mehreren Threads gleichzeitig
bedient zu werden. Sie ist ohnehin der Engpass -- parallel würde sie nicht
schneller, nur unzuverlässiger.

Warum die Warteschlangen begrenzt sind: Ein dekodiertes Bild belegt 5-20 MB.
Ohne Deckel liest der Reader-Pool dem GPU-Worker davon und der Speicher läuft
voll. Die Grenze bremst die schnelleren Stufen automatisch aus.
"""
from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Ein Sentinel je Verbraucher-Thread beendet die Stufe geordnet.
_DONE = object()

#: Dekodierte Bilder sind groß -- diese Queue bleibt klein.
IMAGE_QUEUE = 24
#: Fertige Records tragen nur noch Vektoren, davon passen mehr in den Puffer.
RECORD_QUEUE = 128


@dataclass
class Stats:
    processed: int = 0
    errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ok(self) -> int:
        with self.lock:
            self.processed += 1
            return self.processed

    def fail(self) -> int:
        with self.lock:
            self.errors += 1
            return self.errors

    def seen(self) -> int:
        with self.lock:
            return self.processed + self.errors


class StageClock:
    """Zeit je Stufe, über Threads hinweg addiert.

    Die Summe übersteigt die Laufzeit -- die Stufen laufen ja gleichzeitig.
    Genau das ist die Aussage: Wo viel Zeit anfällt, die nicht die Wanduhr
    kostet, arbeitet das Fließband.
    """

    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            self._totals[name] = self._totals.get(name, 0.0) + seconds

    def totals(self) -> dict[str, float]:
        with self._lock:
            return dict(self._totals)


class _Timed:
    __slots__ = ("clock", "name", "t0")

    def __init__(self, clock: StageClock, name: str):
        self.clock = clock
        self.name = name

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.clock.add(self.name, time.perf_counter() - self.t0)
        return False


def run_parallel(
    files: list[str],
    *,
    io_workers: int,
    write_workers: int,
    gpu_batch: int,
    build_record: Callable[[str, Any, Any], Any],
    gpu_step: Callable[[list], None],
    finish: Callable[[list], None],
    on_progress: Optional[Callable[[int, str], None]] = None,
    clock: Optional[StageClock] = None,
) -> Stats:
    """Fließband über `files`.

    `build_record` läuft im Reader-Pool (Datei lesen, dekodieren, Metadaten),
    `gpu_step` im einzelnen GPU-Thread auf einem Stapel, `finish` im
    Writer-Pool auf einem Stapel.
    """
    clock = clock or StageClock()
    stats = Stats()
    to_gpu: queue.Queue = queue.Queue(maxsize=max(4, IMAGE_QUEUE))
    to_write: queue.Queue = queue.Queue(maxsize=max(8, RECORD_QUEUE))
    paths: queue.Queue = queue.Queue()
    for f in files:
        paths.put(f)

    def reader() -> None:
        while True:
            try:
                fp = paths.get_nowait()
            except queue.Empty:
                break
            try:
                with _Timed(clock, "read+decode"):
                    item = build_record(fp, clock, None)
                if item is not None:
                    to_gpu.put(item)
                else:
                    stats.fail()
            except Exception as e:
                logger.warning("Read stage failed for %s: %s", fp, e)
                stats.fail()

    def gpu() -> None:
        """Ein Thread, aber in Stapeln -- CLIP läuft so deutlich effizienter."""
        pending: list = []
        finished_readers = 0
        while True:
            try:
                item = to_gpu.get(timeout=0.25)
            except queue.Empty:
                if pending:
                    _flush_gpu(pending)
                continue
            if item is _DONE:
                finished_readers += 1
                if finished_readers >= io_workers:
                    _flush_gpu(pending)
                    break
                continue
            pending.append(item)
            if len(pending) >= gpu_batch:
                _flush_gpu(pending)

    def _flush_gpu(pending: list) -> None:
        if not pending:
            return
        batch, pending[:] = list(pending), []
        try:
            with _Timed(clock, "gpu"):
                gpu_step(batch)
        except Exception as e:
            logger.warning("GPU stage failed for %d items: %s", len(batch), e)
            for _ in batch:
                stats.fail()
            return
        for item in batch:
            to_write.put(item)

    def writer() -> None:
        pending: list = []
        while True:
            try:
                item = to_write.get(timeout=0.25)
            except queue.Empty:
                if pending:
                    _flush_write(pending)
                continue
            if item is _DONE:
                _flush_write(pending)
                break
            pending.append(item)
            if len(pending) >= gpu_batch:
                _flush_write(pending)

    def _flush_write(pending: list) -> None:
        if not pending:
            return
        batch, pending[:] = list(pending), []
        try:
            with _Timed(clock, "embed+write"):
                finish(batch)
            for item in batch:
                n = stats.ok()
                if on_progress:
                    on_progress(n, getattr(item, "file_path", "") or "")
        except Exception as e:
            logger.warning("Write stage failed for %d items: %s", len(batch), e)
            for _ in batch:
                stats.fail()

    readers = [threading.Thread(target=reader, name=f"read-{i}", daemon=True)
               for i in range(io_workers)]
    gpu_thread = threading.Thread(target=gpu, name="gpu", daemon=True)
    writers = [threading.Thread(target=writer, name=f"write-{i}", daemon=True)
               for i in range(write_workers)]

    for t in (*readers, gpu_thread, *writers):
        t.start()

    for t in readers:
        t.join()
    # Je Reader ein Sentinel, damit der GPU-Thread das Ende sicher erkennt.
    for _ in readers:
        to_gpu.put(_DONE)
    gpu_thread.join()
    for _ in writers:
        to_write.put(_DONE)
    for t in writers:
        t.join()
    return stats


def make_photo_id(file_path: str) -> str:
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_times(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {}
    return {
        "file_size": st.st_size,
        "file_mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "file_ctime": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
    }
