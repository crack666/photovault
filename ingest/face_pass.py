"""Gesichter auf Video-Frames, ohne CLIP und ohne Ollama.

Warum getrennt von der Pipeline:

Die Pipeline laesst Videos ohne Gesichter durch, weil insightface ein
Standbild braucht und der Caption-Schritt dieselbe Karte fuer Ollama will.
Drei Frames (Anfang, Mitte, Ende) reichen, um jemanden zu sehen, der nicht
im Poster steht -- dieselben Offsets wie bei den Captions.

Dieser Lauf laedt nur insightface. Captions und CLIP bleiben aus, damit die
5090 nicht geteilt wird. ffmpeg holt die Frames parallel in den Speicher
(Queue), die CUDA-Session bleibt ein Thread -- onnxruntime vertraegt keine
zweite. Namen schreibt er nicht: wie bei Fotos ist der Match ein Vorschlag. Erst wenn jemand im Streifen bestaetigt, fuellt
`sync_photo_persons` die `person_ids` / `person_names`, und ein Caption-
Nachlauf kann den Namen in den Satz setzen.
"""
from __future__ import annotations

import argparse
import logging
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from ingest.caption_pass import Photo, select_photos
from ingest.identity import uid_of
from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

#: Gleicher Wert wie der hervorgehobene Vorschlag im Streifen. Darunter
#: sind zwei Köpfe hinreichend verschieden, darüber dieselbe Person auf
#: einem zweiten Frame.
DEDUP_THRESHOLD = 0.55

#: Stempel im Foto-Payload: schon gescannt, auch wenn kein Gesicht da war.
#: Ohne ihn wuerde `--missing-only` Clips ohne Treffer jedes Mal neu fahren.
FACE_SOURCE = "video-frames"

#: Sentinel: Leser fertig, GPU darf auslaufen.
_DONE = object()

#: Dekodierte Clips vor der GPU. Drei Frames je Clip, also knapp halten.
FRAME_QUEUE = 8


def cosine(a, b) -> float:
    import numpy as np

    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(va @ vb / (na * nb))


def _better(a: dict, b: dict) -> bool:
    """Frontal vor Profil, dann sicherer Detektor, dann groesser."""
    fa = a.get("frontality")
    fb = b.get("frontality")
    if fa is None:
        fa = -1.0
    if fb is None:
        fb = -1.0
    if fa != fb:
        return fa > fb
    sa, sb = float(a.get("score") or 0), float(b.get("score") or 0)
    if sa != sb:
        return sa > sb
    return float(a.get("area") or 0) > float(b.get("area") or 0)


def collapse_same_person(
    faces: list[dict],
    threshold: float = DEDUP_THRESHOLD,
) -> list[dict]:
    """Eine Person, drei Frames: ein Eintrag, der frontalste.

    Ohne das zeigt der Streifen dreimal denselben Kopf, und man muesste
    denselben Namen dreimal bestaetigen.
    """
    kept: list[dict] = []
    for face in faces:
        vec = face.get("embedding")
        if not vec:
            continue
        twin = None
        best = -1.0
        for i, other in enumerate(kept):
            score = cosine(vec, other["embedding"])
            if score >= threshold and score > best:
                twin = i
                best = score
        if twin is None:
            kept.append(face)
            continue
        if _better(face, kept[twin]):
            kept[twin] = face
    return kept


def load_video_frames(
    file_path: str,
    duration: float | None = None,
    *,
    ffmpeg_pool=None,
) -> list[tuple[float, object]]:
    """Einzelbilder in den Speicher — nie neben das Original.

    Ohne Pool nacheinander, mit Pool die drei Offsets eines Clips gleichzeitig.
    Die GPU sieht davon nichts: sie bekommt fertige Bilder aus der Queue.
    """
    from ingest.video import frame_image, probe, sample_images, sample_offsets

    if ffmpeg_pool is None:
        return retry_io(lambda: sample_images(file_path, duration), what=file_path)
    if duration is None:
        duration = retry_io(lambda: probe(file_path).get("duration"), what=file_path)
    offsets = sample_offsets(duration)
    futs = [
        ffmpeg_pool.submit(
            retry_io,
            lambda ss=ss: (ss, frame_image(file_path, ss)),
            file_path,
        )
        for ss in offsets
    ]
    return [fut.result() for fut in futs]


def detect_on_frames(embedder, file_path: str, frames) -> list[dict]:
    """insightface auf schon geholten Frames — ein Aufrufer, ein Thread."""
    found: list[dict] = []
    for ss, image in frames:
        result = embedder.process(file_path, image=image)
        for face in result.get("faces") or []:
            row = dict(face)
            row["frame_ss"] = ss
            found.append(row)
    return collapse_same_person(found)


def faces_on_video(file_path: str, embedder, duration: float | None = None) -> list[dict]:
    """Ein Clip, sequentiell — Tests und der Einzelfall. Der Lauf nutzt die Queue."""
    frames = load_video_frames(file_path, duration)
    return detect_on_frames(embedder, file_path, frames)


def suggestion_ids(faces: list[dict], matcher) -> list[str]:
    """Bekannte Personen, die zu den gefundenen Koepfen passen -- nur IDs.

    Wie die Pipeline: der Payload traegt Vorschlaege, keine Zuordnung.
    Mehrere Gesichter duerfen mehrere IDs liefern; dieselbe Person nur einmal.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for face in faces:
        for hit in matcher.suggest(face.get("embedding"), limit=1):
            pid = hit.get("id")
            if not pid or pid.startswith("_") or pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
    return ids


def write_faces(client, writer, photo: Photo, faces: list[dict], suggestions: list[str],
                collection: str = "photos") -> None:
    """Gesichter in die Faces-Collection, Zaehler auf den Foto-Punkt.

    `set_payload` statt `upsert` auf dem Foto -- sonst waeren Caption und
    Textvektor weg. `person_ids` bleiben unangetastet: das ist die
    Bestaetigung im Streifen, nicht dieser Lauf.
    """
    photo_id = uid_of(photo.payload)
    record = SimpleNamespace(
        photo_id=photo_id,
        file_path=photo.file_path,
        content_sha256=photo.payload.get("content_sha256"),
        faces=faces,
    )
    writer.upsert_faces(record)
    client.set_payload(
        collection_name=collection,
        payload={
            "face_count": len(faces),
            "face_boxes": [f.get("box") or [] for f in faces],
            "person_suggestions": suggestions,
            "face_source": FACE_SOURCE,
        },
        points=[photo.point_id],
        wait=True,
    )


def run(
    client,
    collection: str = "photos",
    model_dir: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    track: bool = True,
    missing_only: bool = True,
    kind: str = "video",
    embedder=None,
    writer=None,
    matcher=None,
    io_workers: int = 6,
    ffmpeg_workers: int = 8,
    **filter_args,
) -> dict[str, int]:
    stats = {
        "selected": 0, "processed": 0, "faces": 0, "skipped": 0,
        "unreadable": 0, "failed": 0,
    }
    photos = select_photos(
        client, collection, limit=limit,
        missing_only=False, skip_locked=False, kind=kind, **filter_args,
    )
    if missing_only:
        photos = [p for p in photos if (p.payload or {}).get("face_source") != FACE_SOURCE]
    stats["selected"] = len(photos)
    if not photos:
        logger.info("Nothing to scan for faces.")
        return stats
    logger.info("%d Clips ausgewaehlt", len(photos))
    if dry_run:
        for photo in photos[:20]:
            logger.info("  %s", photo.file_path)
        if len(photos) > 20:
            logger.info("  ... und %d weitere", len(photos) - 20)
        return stats

    if embedder is None:
        from ingest.face_embedder import FaceEmbedder

        embedder = FaceEmbedder(model_dir or os.environ.get(
            "MODEL_DIR", str(Path.home() / ".cache" / "photovault-models"),
        ))
    if writer is None:
        from ingest.qdrant_writer import QdrantWriter

        writer = QdrantWriter.__new__(QdrantWriter)
        writer.client = client
        writer.collection = collection
        writer.faces_collection = os.environ.get("PHOTOVAULT_FACES", "faces")
        writer.space_root = None
    if matcher is None:
        from ingest.face_matcher import FaceMatcher

        matcher = FaceMatcher(client)

    job = _start_job(client, len(photos), collection) if track else None
    started = time.time()
    io_workers = max(1, int(io_workers))
    ffmpeg_workers = max(1, int(ffmpeg_workers))
    logger.info(
        "Fließband: %d Leser, ffmpeg-Pool %d — GPU ein Thread",
        io_workers, ffmpeg_workers,
    )
    _run_piped(
        photos, embedder=embedder, writer=writer, matcher=matcher,
        client=client, collection=collection, stats=stats, job=job,
        io_workers=io_workers, ffmpeg_workers=ffmpeg_workers, started=started,
    )
    if job:
        job.finish(
            "done" if not (stats["failed"] or stats["unreadable"]) else "done-with-errors",
            phase="done", processed=stats["processed"],
            errors=stats["failed"] + stats["unreadable"],
        )
    logger.info(
        "Gesichter: %d Clips, %d Gesichter, %d unlesbar, %d fehlgeschlagen",
        stats["processed"], stats["faces"], stats["unreadable"], stats["failed"],
    )
    return stats


def _run_piped(
    photos, *, embedder, writer, matcher, client, collection, stats, job,
    io_workers: int, ffmpeg_workers: int, started: float,
) -> None:
    """ffmpeg parallel in den Speicher, insightface nur auf einem Thread.

    onnxruntime darf die CUDA-Session nicht von mehreren Threads gleichzeitig
    sehen — deshalb GPU ein Thread, wie im Foto-Fließband. Die Queue ist klein,
    weil drei dekodierte Frames schon ein paar Dutzend MB sind.
    """
    lock = threading.Lock()
    to_gpu: queue.Queue = queue.Queue(maxsize=FRAME_QUEUE)
    to_write: queue.Queue = queue.Queue(maxsize=32)
    pending = queue.Queue()
    for photo in photos:
        pending.put(photo)

    def bump(key: str, n: int = 1) -> None:
        with lock:
            stats[key] += n

    def seen_now() -> int:
        with lock:
            return stats["unreadable"] + stats["processed"] + stats["failed"]

    def progress(photo) -> None:
        seen = seen_now()
        if job:
            with lock:
                err = stats["failed"] + stats["unreadable"]
                done = stats["processed"]
            job.update(phase="faces", processed=done, errors=err)
        if seen == 1 or seen % 10 == 0 or seen == len(photos):
            elapsed = time.time() - started
            with lock:
                nfaces = stats["faces"]
                processed = stats["processed"]
            rate = processed / elapsed if elapsed else 0
            logger.info(
                "%d/%d  %d Gesichter  %s  %.2f/s",
                seen, len(photos), nfaces, photo.file_path, rate,
            )

    def reader() -> None:
        while True:
            try:
                photo = pending.get_nowait()
            except queue.Empty:
                break
            try:
                frames = load_video_frames(
                    photo.file_path,
                    photo.payload.get("duration_s"),
                    ffmpeg_pool=ffmpeg_pool,
                )
                to_gpu.put({"photo": photo, "frames": frames})
            except Exception as e:
                logger.warning("Unreadable, skipping: %s (%s)", photo.file_path, e)
                bump("unreadable")
                photo.failed = True
                progress(photo)

    def gpu() -> None:
        ensure = getattr(embedder, "_ensure_loaded", None)
        if callable(ensure):
            ensure()
        finished = 0
        while finished < io_workers:
            item = to_gpu.get()
            if item is _DONE:
                finished += 1
                continue
            photo = item["photo"]
            try:
                faces = detect_on_frames(embedder, photo.file_path, item["frames"])
            except Exception as e:
                logger.warning("Face processing failed for %s: %s", photo.file_path, e)
                bump("failed")
                photo.failed = True
                progress(photo)
                continue
            finally:
                item["frames"] = None
            to_write.put({"photo": photo, "faces": faces})

    def writer_loop() -> None:
        while True:
            item = to_write.get()
            if item is _DONE:
                break
            photo, faces = item["photo"], item["faces"]
            try:
                suggestions = suggestion_ids(faces, matcher)
                # Absicht: keine person_ids. Der Test und der Matcher-Kommentar
                # verbieten stilles Auto-Label.
                write_faces(client, writer, photo, faces, suggestions, collection)
            except Exception as e:
                logger.warning("Face write failed for %s: %s", photo.file_path, e)
                bump("failed")
                continue
            bump("processed")
            bump("faces", len(faces))
            progress(photo)

    with ThreadPoolExecutor(max_workers=ffmpeg_workers, thread_name_prefix="ff") as ffmpeg_pool:
        readers = [threading.Thread(target=reader, name=f"read-{i}", daemon=True)
                   for i in range(io_workers)]
        gpu_thread = threading.Thread(target=gpu, name="gpu", daemon=True)
        write_thread = threading.Thread(target=writer_loop, name="write", daemon=True)
        for t in (*readers, gpu_thread, write_thread):
            t.start()
        for t in readers:
            t.join()
        for _ in readers:
            to_gpu.put(_DONE)
        gpu_thread.join()
        to_write.put(_DONE)
        write_thread.join()


def _start_job(client, total: int, collection: str):
    try:
        from ingest.jobs import JobTracker

        job = JobTracker(client, kind="faces", source=collection)
        job.update(phase="faces", total=total, force=True)
        return job
    except Exception as e:
        logger.debug("Job tracking unavailable: %s", e)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gesichter in Videos finden, ohne CLIP und ohne Ollama.",
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--model-dir", default=os.environ.get(
        "MODEL_DIR", str(Path.home() / ".cache" / "photovault-models"),
    ))
    parser.add_argument("--all", action="store_true",
                        help="Auch Clips neu scannen, die schon einen Face-Lauf hatten")
    parser.add_argument("--kind", choices=("video", "photo"), default="video",
                        help="Standard: nur Videos -- Fotos haben Gesichter schon vom Ingest")
    parser.add_argument("--person", help="Nur Medien mit dieser bestaetigten Person")
    parser.add_argument("--album", help="Nur Alben, deren Name diesen Text enthaelt")
    parser.add_argument("--path", dest="path_contains", help="Nur Pfade, die diesen Text enthalten")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--io-workers", type=int, default=6,
                        help="Clips gleichzeitig von der Platte holen")
    parser.add_argument("--ffmpeg-workers", type=int, default=8,
                        help="Gleichzeitige ffmpeg-Seeks (Frames eines Clips teilen den Pool)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-track", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    from qdrant_client import QdrantClient

    os.environ.setdefault("QDRANT_URL", args.qdrant_url)
    client = QdrantClient(url=args.qdrant_url)
    run(
        client,
        collection=args.collection,
        model_dir=args.model_dir,
        limit=args.limit,
        dry_run=args.dry_run,
        track=not args.no_track,
        missing_only=not args.all,
        kind=args.kind,
        person=args.person,
        album=args.album,
        path_contains=args.path_contains,
        io_workers=args.io_workers,
        ffmpeg_workers=args.ffmpeg_workers,
    )


if __name__ == "__main__":
    main()
