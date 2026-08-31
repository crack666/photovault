"""Captions nachziehen, ohne Gesichts- und Szenenmodelle zu laden.

Warum getrennt von der Pipeline:

Die Pipeline erzeugt Captions im selben Durchgang wie insightface und CLIP.
Beide belegen zusammen rund 2,4 GB auf derselben Karte, auf der Ollama das
27B-Modell haelt. Bei `num_ctx 131072` bleiben davor nur 2,6 GB frei, und der
Caption-Schritt bricht von 3,0 auf 17,5 s pro Foto ein -- nicht weil grosser
Kontext langsam waere, sondern weil der Platz fehlt. Ohne die beiden Modelle
laeuft dieselbe Caption in 2,7 s, unabhaengig von der Kontextgroesse
(docs/performance.md).

Dieser Lauf braucht die GPU-Modelle nicht: Gesichtszahl, bestaetigte Namen und
CLIP-Tags stehen bereits im Qdrant-Payload. Damit muss Photovault Ollama kein
`num_ctx` mehr aufzwingen, es gibt keinen Reload, und der Coding-Agent behaelt
seinen langen Kontext.

Der zweite Gewinn ist fachlich: der Lauf ist auf Teilmengen wiederholbar. Nach
einer Labeling-Runde nur die betroffenen Fotos neu beschriften zu lassen kostet
Sekunden statt eines vollen Ingest -- und genau darauf ist das mehrstufige
Vorgehen angewiesen (erst Personen benennen, dann Captions mit den Namen im
Kontext).
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ingest.captioner import Captioner, jpeg_b64, merge_tags, run_captions
from ingest.grounding import caption_display
from ingest.netfs import retry_io
from ingest.reembed import rebuild_text_vectors
from ingest.text_embedder import TextEmbedder

logger = logging.getLogger(__name__)

#: Fotos je Runde. Gross genug, dass der Pool ausgelastet bleibt, klein genug,
#: dass ein Abbruch wenig Arbeit kostet -- geschrieben wird nach jeder Runde.
BATCH = 24


class Photo:
    """Ein Foto aus Qdrant, so wie der Caption-Schritt es braucht."""

    __slots__ = ("point_id", "payload", "file_path", "image_b64",
                 "caption_de", "scene_tags", "failed")

    def __init__(self, point_id: Any, payload: dict):
        self.point_id = point_id
        self.payload = payload
        self.file_path: str = payload.get("file_path") or ""
        self.image_b64: Optional[str] = None
        self.caption_de: Optional[str] = None
        self.scene_tags: list[str] = list(payload.get("scene_tags") or [])
        self.failed: bool = False


def payload_context(payload: dict) -> dict:
    """Prompt-Kontext aus dem gespeicherten Payload.

    Gegenstueck zu `pipeline.record_context`, aber aus Qdrant statt aus einem
    frisch berechneten Record. Ein Unterschied ist Absicht: hier zaehlen die
    **bestaetigten** Namen (`person_names`). Vorschlaege aus dem Face-Match
    gehoeren nicht in eine Caption -- ein falscher Name waere schlimmer als
    gar keiner, und die Regeln im Prompt verlassen sich darauf.
    """
    file_path = payload.get("file_path") or ""
    return {
        "folder_name": payload.get("folder_name"),
        "event_name": payload.get("event_name"),
        "date": payload.get("date"),
        "date_source": payload.get("date_source"),
        "file_ctime": payload.get("file_ctime"),
        "file_mtime": payload.get("file_mtime"),
        "filename": Path(file_path).name if file_path else None,
        "sequence": payload.get("sequence_in_folder"),
        "location": payload.get("location"),
        "face_count": payload.get("face_count"),
        "people_assigned": list(payload.get("person_names") or []),
        "people_album": list(payload.get("folder_people") or []),
        "clip_tags": list(payload.get("scene_tags") or []),
    }


def build_filter(
    missing_only: bool = True,
    person: Optional[str] = None,
    album: Optional[str] = None,
    path_contains: Optional[str] = None,
    has_caption: bool = False,
):
    """Qdrant-Filter fuer die Auswahl. `caption_locked` bleibt beim LLM-Lauf aussen vor.

    `has_caption` ist der EXIF-Abgleich: Satz steht schon im Index, soll in
    die Datei. Von-Hand-Sätze (`caption_locked`) gehören dort *hinein*.
    """
    from qdrant_client.models import (
        FieldCondition, Filter, IsEmptyCondition, MatchText, MatchValue, PayloadField,
    )

    must: list = []
    must_not: list = []
    if not has_caption:
        must_not.append(FieldCondition(key="caption_locked", match=MatchValue(value=True)))
    if missing_only:
        must_not.append(FieldCondition(key="caption_source", match=MatchValue(value="llm")))
    if has_caption:
        must_not.append(IsEmptyCondition(is_empty=PayloadField(key="caption_de")))
    if person:
        must.append(FieldCondition(key="person_names", match=MatchValue(value=person)))
    if album:
        must.append(FieldCondition(key="folder_name", match=MatchText(text=album)))
    if path_contains:
        must.append(FieldCondition(key="file_path", match=MatchText(text=path_contains)))
    return Filter(must=must or None, must_not=must_not or None)


def select_photos(
    client,
    collection: str = "photos",
    limit: Optional[int] = None,
    **filter_args,
) -> list[Photo]:
    """Kandidaten einsammeln, nach Pfad sortiert.

    Die Sortierung ordnet den Lauf, sie waehlt nicht aus: `limit` greift in
    Scroll-Reihenfolge, nicht alphabetisch. Das ist Absicht -- alphabetisch
    truncieren wuerde bei `--limit 50` immer dasselbe Album treffen und damit
    eine unrepraesentative Stichprobe liefern (derselbe Fallstrick wie bei
    `pipeline --limit`, docs/performance.md). Fuer eine gezielte Teilmenge sind
    `person`, `album` und `path_contains` da.

    Innerhalb des Ergebnisses ordnet die Sortierung nach Pfad die Fotos eines
    Ordners zusammen, was dem SMB-Lesen entgegenkommt.
    """
    flt = build_filter(**filter_args)
    found: list[Photo] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            scroll_filter=flt,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in batch:
            payload = point.payload or {}
            if not payload.get("file_path"):
                continue
            found.append(Photo(point.id, payload))
            if limit and len(found) >= limit:
                return sorted(found, key=lambda p: p.file_path)
        if offset is None:
            break
    return sorted(found, key=lambda p: p.file_path)


def _load_image(photo: Photo) -> bool:
    """Foto vom NAS holen und verkleinern. Laeuft im Leser-Pool.

    Mit Wiederholung, weil ein Neustart des SMB-Dienstes sonst dutzende Fotos
    als "unlesbar" abstempelt, mit denen nichts ist.
    """
    try:
        photo.image_b64 = retry_io(lambda: jpeg_b64(photo.file_path),
                                   what=photo.file_path)
        return True
    except Exception as e:
        logger.warning("Unreadable, skipping: %s (%s)", photo.file_path, e)
        photo.failed = True
        return False


def run(
    client,
    collection: str = "photos",
    ollama_url: Optional[str] = None,
    workers: int = 4,
    io_workers: int = 6,
    num_ctx: int = 0,
    limit: Optional[int] = None,
    dry_run: bool = False,
    track: bool = True,
    write_exif: bool = True,
    exif_only: bool = False,
    **filter_args,
) -> dict[str, int]:
    """Captions fuer die ausgewaehlten Fotos erzeugen und Textvektoren erneuern.

    Der Satz geht in den Index *und* ins EXIF (ImageDescription), sonst lebt
    er nur in Qdrant. `--exif-only` schreibt vorhandene Index-Sätze nach,
    ohne das Modell.
    """
    stats = {
        "selected": 0, "captioned": 0, "unreadable": 0, "failed": 0, "reembedded": 0,
        "exif_written": 0, "exif_skipped": 0, "exif_failed": 0,
    }
    if exif_only:
        filter_args = {**filter_args, "missing_only": False, "has_caption": True}
    photos = select_photos(client, collection, limit=limit, **filter_args)
    stats["selected"] = len(photos)
    if not photos:
        logger.info("Nothing to caption.")
        return stats
    logger.info("%d Fotos ausgewaehlt", len(photos))
    if dry_run:
        for photo in photos[:20]:
            logger.info("  %s", photo.file_path)
        if len(photos) > 20:
            logger.info("  ... und %d weitere", len(photos) - 20)
        return stats

    if exif_only:
        _write_file_captions(photos, stats)
        logger.info(
            "EXIF: %d geschrieben, %d übersprungen, %d fehlgeschlagen",
            stats["exif_written"], stats["exif_skipped"], stats["exif_failed"],
        )
        return stats

    job = _start_job(client, len(photos), collection) if track else None
    # num_ctx=0: keine Kontextgroesse mitschicken -> kein Reload, das
    # geladene Modell bleibt wie es ist. Ohne GPU-Modelle daneben brauchen
    # wir den kleinen Kontext nicht.
    captioner = Captioner(ollama_url, num_ctx=num_ctx)
    embedder = TextEmbedder(ollama_url)
    started = time.time()

    def caption_one(photo: Photo) -> None:
        structured = captioner.caption_structured(
            photo.file_path, payload_context(photo.payload), image_b64=photo.image_b64
        )
        photo.image_b64 = None
        if not structured:
            photo.failed = True
            return
        photo.caption_de = (structured.get("caption_de") or "").strip() or None
        photo.scene_tags = merge_tags(photo.scene_tags, structured.get("scene_tags") or [])

    for chunk in _chunks(photos, BATCH):
        _read_images(chunk, io_workers)
        ready = [p for p in chunk if p.image_b64 is not None]
        stats["unreadable"] += len(chunk) - len(ready)

        run_captions(ready, caption_one, workers)

        done = [p for p in ready if p.caption_de]
        stats["failed"] += len(ready) - len(done)
        if done:
            _write_captions(client, collection, done)
            stats["captioned"] += len(done)
            if write_exif:
                _write_file_captions(done, stats)
            # Erst schreiben, dann einbetten -- das Dokument soll die neue
            # Caption enthalten.
            result = rebuild_text_vectors(
                client, [p.point_id for p in done], collection=collection, embedder=embedder
            )
            stats["reembedded"] += result.get("updated", 0)

        if job:
            job.update(
                phase="captioning",
                processed=stats["captioned"],
                errors=stats["failed"] + stats["unreadable"],
            )
        logger.info(
            "%d/%d  %.2f s/Foto",
            stats["captioned"], len(photos),
            (time.time() - started) / max(1, stats["captioned"]),
        )

    if job:
        job.finish("done", phase="done", processed=stats["captioned"],
                   errors=stats["failed"] + stats["unreadable"])
    elapsed = time.time() - started
    logger.info(
        "Fertig: %d Captions in %s (%.2f s/Foto), %d unlesbar, %d fehlgeschlagen, "
        "EXIF %d geschrieben / %d übersprungen / %d fehlgeschlagen",
        stats["captioned"], _fmt(elapsed),
        elapsed / max(1, stats["captioned"]), stats["unreadable"], stats["failed"],
        stats["exif_written"], stats["exif_skipped"], stats["exif_failed"],
    )
    return stats


def _read_images(photos: list[Photo], io_workers: int) -> None:
    """SMB ist latenz-, nicht bandbreitengebunden -- parallel lesen lohnt sich."""
    if io_workers <= 1 or len(photos) == 1:
        for photo in photos:
            _load_image(photo)
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(io_workers, len(photos))) as pool:
        list(pool.map(_load_image, photos))


def _write_captions(client, collection: str, photos: list[Photo]) -> None:
    """Caption, Quelle, Tags und Kopfzeile zurueckschreiben.

    `set_payload` statt `upsert` -- es ersetzt nur die genannten Felder und
    kann deshalb nichts loeschen, was dieser Lauf gar nicht kennt (ein `upsert`
    wuerde Personen, Notizen und Vektoren mitnehmen).

    `wait=True` ist hier kein Luxus: gleich danach liest `rebuild_text_vectors`
    den Punkt erneut, um das Dokument zu bauen. Ohne die Zusage saehe es unter
    Umstaenden noch die alte, leere Caption.
    """
    for photo in photos:
        merged = dict(photo.payload)
        merged["caption_de"] = photo.caption_de
        merged["scene_tags"] = photo.scene_tags
        try:
            client.set_payload(
                collection_name=collection,
                payload={
                    "caption_de": photo.caption_de,
                    "caption_source": "llm",
                    "scene_tags": photo.scene_tags,
                    "caption_display": caption_display(merged),
                },
                points=[photo.point_id],
                wait=True,
            )
        except Exception as e:
            logger.warning("Payload update failed for %s: %s", photo.file_path, e)


def _write_file_captions(photos: list[Photo], stats: dict) -> None:
    """Satz in die Datei — best effort, der Index ist schon geschrieben."""
    from ingest.exif_writer import write_caption
    from ingest.netfs import retry_io

    for photo in photos:
        text = (photo.caption_de or (photo.payload or {}).get("caption_de") or "").strip()
        if not text:
            stats["exif_skipped"] = stats.get("exif_skipped", 0) + 1
            continue
        source = (photo.payload or {}).get("caption_source") or "llm"
        try:
            out = retry_io(
                lambda p=photo, t=text, s=source: write_caption(
                    p.file_path, t, source=s, dry_run=False, overwrite=False,
                ),
                what=photo.file_path,
            )
        except Exception as e:
            stats["exif_failed"] = stats.get("exif_failed", 0) + 1
            logger.warning("EXIF-Caption fehlgeschlagen: %s (%s)", photo.file_path, e)
            continue
        if out.get("written"):
            stats["exif_written"] = stats.get("exif_written", 0) + 1
        else:
            stats["exif_skipped"] = stats.get("exif_skipped", 0) + 1
            if out.get("reason"):
                logger.debug("EXIF-Caption %s: %s", photo.file_path, out["reason"])


def _start_job(client, total: int, collection: str):
    try:
        from ingest.jobs import JobTracker

        job = JobTracker(client, kind="caption", source=collection)
        job.update(phase="captioning", total=total, force=True)
        return job
    except Exception as e:  # Job-Tracking darf den Lauf nie stoppen.
        logger.debug("Job tracking unavailable: %s", e)
        return None


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Captions nachziehen, ohne insightface und CLIP zu laden.",
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL"))
    parser.add_argument("--all", action="store_true",
                        help="Auch Fotos neu beschriften, die schon eine LLM-Caption haben")
    parser.add_argument("--person", help="Nur Fotos mit dieser bestaetigten Person")
    parser.add_argument("--album", help="Nur Fotos aus Alben, deren Name diesen Text enthaelt")
    parser.add_argument("--path", dest="path_contains", help="Nur Pfade, die diesen Text enthalten")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4,
                        help="Gleichzeitige Caption-Anfragen (4 ist das Optimum)")
    parser.add_argument("--io-workers", type=int, default=6, help="Leser-Threads fuer das NAS")
    parser.add_argument("--num-ctx", type=int, default=0,
                        help="0 (Standard) = Kontext des geladenen Modells nutzen, kein Reload")
    parser.add_argument("--dry-run", action="store_true", help="Nur zeigen, was betroffen waere")
    parser.add_argument("--no-track", action="store_true", help="Kein Job-Eintrag in Qdrant")
    parser.add_argument("--no-exif", action="store_true",
                        help="Satz nur in den Index, nicht in die Datei")
    parser.add_argument("--exif-only", action="store_true",
                        help="Vorhandene Index-Captions ins EXIF schreiben, ohne das Modell")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    from qdrant_client import QdrantClient

    client = QdrantClient(url=args.qdrant_url)
    run(
        client,
        collection=args.collection,
        ollama_url=args.ollama_url,
        workers=args.workers,
        io_workers=args.io_workers,
        num_ctx=args.num_ctx,
        limit=args.limit,
        dry_run=args.dry_run,
        track=not args.no_track,
        write_exif=not args.no_exif,
        exif_only=args.exif_only,
        missing_only=not args.all,
        person=args.person,
        album=args.album,
        path_contains=args.path_contains,
    )


if __name__ == "__main__":
    main()
