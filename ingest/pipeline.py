"""Ingest Pipeline Orchestrierung."""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IngestConfig:
    source: str
    #: Weitere Wurzeln. `source` bleibt die erste -- so aendert sich fuer
    #: bestehende Aufrufer nichts.
    extra_sources: list[str] = field(default_factory=list)
    #: Verzeichnisse, die trotz passender Wurzel aussen vor bleiben.
    exclude: list[str] = field(default_factory=list)
    qdrant_url: str = "http://localhost:6333"
    collection: str = "photos"
    batch_size: int = 50
    face_threshold: float = 0.4
    # Mindest-Softmax-Wahrscheinlichkeit eines Szenen-Konzepts, kein roher Cosinus.
    clip_threshold: float = 0.04
    model_dir: str = "/models"
    resume: bool = True
    skip_caption: bool = False
    ollama_url: Optional[str] = None
    limit: Optional[int] = None
    include: Optional[str] = None
    progress_every: int = 10
    thumbs: bool = True
    #: Leser-Threads. >1 schaltet auf das Fliessband um (ingest/parallel.py).
    #: 6 war im Test der beste Wert -- darueber bremst die GIL-Konkurrenz
    #: den GPU-Thread mehr, als die zusaetzlichen Leser einbringen.
    workers: int = 6
    #: Wieviele Bilder CLIP in einem Durchlauf verarbeitet.
    gpu_batch: int = 16
    #: Gleichzeitige Caption-Anfragen *je Writer-Thread*.
    #: Der Standard 1 ist mit Absicht gewaehlt: `finish()` laeuft ohnehin in
    #: jedem der `write_workers` Threads (bei --workers 6 sind das 3), die
    #: Pipeline stellt Ollama also schon ohne Pool drei gleichzeitige Anfragen
    #: -- und mehr als etwa vier bringen nichts, weil Ollama sie serialisiert.
    #: Gemessen: 1 und 4 liefern 146,2 s bzw. 145,2 s fuer 48 Fotos.
    #: Siehe docs/performance.md.
    caption_workers: int = 1


class StageTimer:
    """Akkumuliert Wall-Clock pro Pipeline-Stufe."""

    ORDER = (
        "scan",
        "file_times",
        "decode",
        "exif",
        "folder",
        "face",
        "face_match",
        "clip",
        "thumb",
        "normalize",
        "caption",
        "text_embed",
        "write",
    )

    def __init__(self) -> None:
        self.totals: dict[str, float] = {}
        self.counts: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self.totals[name] = self.totals.get(name, 0.0) + dt
            self.counts[name] = self.counts.get(name, 0) + 1

    def report(self, n_photos: int) -> str:
        rows = [f"{'stage':<12} {'total_s':>9} {'ms/photo':>10} {'share':>7}"]
        grand = sum(self.totals.values()) or 1.0
        for name in self.ORDER:
            if name not in self.totals:
                continue
            total = self.totals[name]
            per = (total / n_photos * 1000) if n_photos else 0.0
            rows.append(f"{name:<12} {total:>9.2f} {per:>10.1f} {total / grand * 100:>6.1f}%")
        rows.append(f"{'SUM':<12} {grand:>9.2f} {(grand / n_photos * 1000) if n_photos else 0:>10.1f}")
        return "\n".join(rows)


@dataclass
class IngestProgress:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    phase: str = "idle"
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def percent(self) -> float:
        return (self.processed / self.total) * 100 if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "processed": self.processed,
            "skipped": self.skipped,
            "errors": self.errors,
            "phase": self.phase,
            "percent": round(self.percent, 1),
        }


@dataclass
class PhotoRecord:
    photo_id: str
    file_path: str
    date: Optional[str] = None
    date_source: Optional[str] = None
    date_confidence: float = 0.0
    date_hint: Optional[str] = None
    date_hint_source: Optional[str] = None
    taken_at: Optional[str] = None
    #: Papierkorb-Stempel. Steht in keiner Datei, nur im Index -- wird beim
    #: Re-Ingest aus dem bestehenden Punkt uebernommen, damit ein Lauf mit
    #: --no-resume Weggeworfenes nicht zurueckholt.
    trashed_at: Optional[str] = None
    #: Volle Aufnahmezeit aus EXIF, falls vorhanden.
    exif_datetime: Optional[str] = None
    gps: Optional[list[float]] = None
    exif: Optional[dict] = None
    folder_name: Optional[str] = None
    subfolder: Optional[str] = None
    folder_type: Optional[str] = None
    folder_people: list[str] = field(default_factory=list)
    sequence_in_folder: Optional[int] = None
    location: Optional[str] = None
    location_hint: Optional[str] = None
    location_key: Optional[str] = None
    location_lc: Optional[str] = None
    location_source: Optional[str] = None
    file_mtime: Optional[str] = None
    file_ctime: Optional[str] = None
    file_size: Optional[int] = None
    #: sha256 des Dateiinhalts. Schluessel fuer den Vorschaubild-Cache und
    #: Grundlage dafuer, eine von aussen verschobene Datei wiederzuerkennen.
    #: Wird *nach* dem Bildladen gebildet -- dann liegt die Datei im
    #: Seiten-Cache und kostet 0,9 statt 35,8 ms.
    content_sha256: Optional[str] = None
    face_count: int = 0
    face_embedding: Optional[list[float]] = None
    face_boxes: list[list[int]] = field(default_factory=list)
    faces: list[dict] = field(default_factory=list)
    scene_tags: list[str] = field(default_factory=list)
    clip_embedding: Optional[list[float]] = None
    caption_de: Optional[str] = None
    caption_display: Optional[str] = None
    caption_source: Optional[str] = None
    caption_locked: bool = False
    text_embedding: Optional[list[float]] = None
    person_ids: list[str] = field(default_factory=list)
    person_names: list[str] = field(default_factory=list)
    person_suggestions: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    event_name: Optional[str] = None
    event_excluded: bool = False
    file_warning: Optional[str] = None
    ingested_at: Optional[str] = None


class IngestPipeline:
    def __init__(self, config: IngestConfig):
        self.config = config
        self.progress = IngestProgress()
        self.timer = StageTimer()
        self.job = None
        self.parallel_clock = None

    def run(self) -> IngestProgress:
        logger.info("Starting ingest pipeline")
        self.progress.started_at = time.time()
        t = self.timer
        from ingest import grounding
        from ingest.captioner import Captioner
        from ingest.exif_extractor import ExifExtractor
        from ingest.face_embedder import FaceEmbedder
        from ingest.face_matcher import FaceMatcher
        from ingest.folder_parser import FolderParser
        from ingest.normalizer import Normalizer
        from ingest.qdrant_writer import QdrantWriter
        from ingest.scanner import NASScanner
        from ingest.scene_tagger import SceneTagger
        from ingest.spaces import common_root
        from ingest.text_embedder import TextEmbedder

        sources = [self.config.source, *self.config.extra_sources]
        scanner = NASScanner(sources, exclude=self.config.exclude)
        self.progress.phase = "scan"
        with t.stage("scan"):
            files = scanner.scan()
        found = len(files)

        if self.config.include:
            needle = self.config.include.lower()
            files = [f for f in files if needle in f.lower()]
            logger.info("Include filter %r: %d of %d files", self.config.include, len(files), found)

        writer = QdrantWriter(
            self.config.qdrant_url, self.config.collection,
            space_root=common_root([str(s).rstrip("/") for s in sources]),
        )

        from ingest.jobs import JobTracker

        self.job = JobTracker(
            writer.client,
            kind="ingest-caption" if not self.config.skip_caption else "ingest",
            source=self.config.source,
            detail={"collection": self.config.collection},
        )

        if self.config.resume:
            before = len(files)
            files = self._drop_already_indexed(writer, files)
            self.progress.skipped += before - len(files)
            logger.info("Resume: %d already indexed, %d to process", before - len(files), len(files))

        if self.config.limit is not None:
            files = files[: self.config.limit]
            logger.info("Limit %d applied", self.config.limit)

        self.progress.total = len(files)
        logger.info("Processing %d photos (scanner found %d)", len(files), found)
        self.job.update(
            total=len(files), skipped=self.progress.skipped, phase="loading models", force=True
        )
        if not files:
            self.progress.finished_at = time.time()
            self.progress.phase = "done"
            self.job.finish("done", phase="done")
            return self.progress

        # Qdrants upsert ersetzt das ganze Payload. Alles, was nicht aus der
        # Datei rekonstruierbar ist -- bestaetigte Personen, eigene Notizen und
        # teuer erzeugte Captions -- muss deshalb vorher gerettet werden.
        preserved = self._load_preserved(writer, files)
        if preserved:
            logger.info("Preserving user data on %d existing photos", len(preserved))

        exif_ext = ExifExtractor()
        folder_parser = FolderParser(sources)
        face_emb = FaceEmbedder(self.config.model_dir)
        scene = SceneTagger(self.config.model_dir, self.config.clip_threshold)
        captioner = None if self.config.skip_caption else Captioner(self.config.ollama_url)
        text_emb = TextEmbedder(self.config.ollama_url)
        if captioner is not None:
            self._warm_caption_model()
        normalizer = Normalizer()
        matcher = FaceMatcher(writer.client)

        loop_start = time.time()

        if self.config.workers > 1:
            self._run_parallel(
                files, preserved, loop_start,
                exif_ext=exif_ext, folder_parser=folder_parser, face_emb=face_emb,
                scene=scene, captioner=captioner, text_emb=text_emb,
                normalizer=normalizer, writer=writer, matcher=matcher,
            )
            self.progress.finished_at = time.time()
            self.progress.phase = "done"
            self.job.finish(
                "done" if not self.progress.errors else "done-with-errors",
                phase="done", processed=self.progress.processed, errors=self.progress.errors,
            )
            wall = self.progress.finished_at - loop_start
            logger.info(
                "Ingest complete: %d processed, %d errors, %.1f s wall, %.2f photos/s",
                self.progress.processed, self.progress.errors, wall,
                self.progress.processed / wall if wall else 0.0,
            )
            return self.progress

        seen = 0
        for i in range(0, len(files), self.config.batch_size):
            batch = files[i : i + self.config.batch_size]
            self.progress.phase = f"process ({i}/{len(files)})"
            pending: list[tuple[PhotoRecord, str]] = []
            for fp in batch:
                try:
                    photo_id = hashlib.sha256(fp.encode("utf-8")).hexdigest()
                    record = PhotoRecord(photo_id=photo_id, file_path=fp)
                    prior = preserved.get(photo_id)
                    _apply_prior(record, prior)
                    with t.stage("file_times"):
                        _fill_file_times(record, Path(fp))

                    # Einmal oeffnen, alles daraus bedienen: EXIF, Gesichter,
                    # CLIP und Vorschaubild lasen bisher jeweils neu vom NAS.
                    with t.stage("decode"):
                        raw_img, rgb, warn = _load_image(fp)
                    if warn:
                        record.file_warning = warn

                    with t.stage("exif"):
                        exif_data = exif_ext.extract(fp, image=raw_img)
                    record.date = exif_data.get("date")
                    record.date_source = exif_data.get("date_source")
                    record.date_confidence = exif_data.get("date_confidence", 0.0)
                    record.exif_datetime = exif_data.get("datetime")
                    record.gps = exif_data.get("gps")
                    record.exif = exif_data.get("raw")

                    with t.stage("folder"):
                        fd = folder_parser.parse(fp)
                    record.folder_name = fd.get("folder_name")
                    record.subfolder = fd.get("subfolder")
                    record.folder_type = fd.get("folder_type")
                    record.folder_people = fd.get("people") or []
                    record.sequence_in_folder = fd.get("sequence")
                    record.location_hint = fd.get("location_hint")
                    record.location_key = fd.get("location_key")
                    record.date_hint = fd.get("date_hint")
                    record.date_hint_source = fd.get("date_hint_source")

                    with t.stage("face"):
                        fr = face_emb.process(fp, image=rgb)
                    record.face_count = fr["count"]
                    record.face_embedding = fr["primary_embedding"]
                    record.face_boxes = fr["boxes"]
                    record.faces = fr.get("faces") or []
                    with t.stage("face_match"):
                        record.person_suggestions = [
                            s["id"] for s in matcher.suggest(record.face_embedding)
                        ]

                    with t.stage("clip"):
                        sr = scene.process(fp, image=rgb)
                    record.scene_tags = sr["tags"]
                    record.clip_embedding = sr["embedding"]

                    if self.config.thumbs:
                        # Jetzt gleich, solange die Datei ohnehin gelesen wird --
                        # sonst zahlt die erste Suche 48 SMB-Reads auf einmal.
                        with t.stage("thumb"):
                            _make_thumb(fp, rgb)

                    with t.stage("normalize"):
                        normalizer.normalize(record)

                    ctx = record_context(record)
                    locked = bool(prior and prior.get("caption_locked"))
                    if locked:
                        # Von Hand geschrieben -- das Modell hat hier nichts zu suchen.
                        record.caption_de = prior.get("caption_de")
                        record.caption_source = prior.get("caption_source") or "manual"
                        record.caption_locked = True
                    elif captioner is not None:
                        from ingest.captioner import jpeg_b64

                        with t.stage("caption"):
                            structured = captioner.caption_structured(
                                fp, ctx,
                                image_b64=jpeg_b64(fp, image=rgb) if rgb is not None else None,
                            )
                        if structured:
                            from ingest.captioner import merge_tags

                            record.caption_de = structured.get("caption_de")
                            record.caption_source = "llm"
                            record.scene_tags = merge_tags(
                                record.scene_tags, structured.get("scene_tags") or [])
                    elif prior:
                        record.caption_de = prior.get("caption_de")
                        record.caption_source = prior.get("caption_source")

                    gp = grounding.record_payload(record)
                    record.caption_display = grounding.caption_display(gp)
                    record.ingested_at = datetime.now(timezone.utc).isoformat()
                    # Einbetten erst am Batch-Ende: 20 Dokumente in einem
                    # Ollama-Request kosten 33 ms/Stueck statt 120 ms einzeln.
                    pending.append((record, grounding.grounded_document(gp)))
                except Exception as e:
                    logger.warning("Error processing %s: %s", fp, e)
                    self.progress.errors += 1

                seen += 1
                if self.config.progress_every and seen % self.config.progress_every == 0:
                    elapsed = time.time() - loop_start
                    rate = seen / elapsed if elapsed else 0.0
                    eta = (len(files) - seen) / rate if rate else 0.0
                    logger.info(
                        "%d/%d (%.0f%%)  %.2f photos/s  %.1f s/photo  ETA %s  errors %d",
                        seen,
                        len(files),
                        seen / len(files) * 100,
                        rate,
                        1 / rate if rate else 0.0,
                        _fmt_dur(eta),
                        self.progress.errors,
                    )
                self.job.update(
                    processed=seen,
                    errors=self.progress.errors,
                    phase=f"{Path(fp).parent.name}",
                )

            self._flush_batch(pending, text_emb, writer, t)

        self.progress.finished_at = time.time()
        self.progress.phase = "done"
        self.job.finish(
            "done" if not self.progress.errors else "done-with-errors",
            phase="done",
            processed=self.progress.processed,
            errors=self.progress.errors,
        )
        wall = self.progress.finished_at - loop_start
        logger.info(
            "Ingest complete: %d processed, %d errors, %.1f s wall, %.2f photos/s",
            self.progress.processed,
            self.progress.errors,
            wall,
            self.progress.processed / wall if wall else 0.0,
        )
        return self.progress

    def _run_parallel(
        self, files, preserved, loop_start, *, exif_ext, folder_parser, face_emb,
        scene, captioner, text_emb, normalizer, writer, matcher,
    ) -> None:
        """Fließband statt Reihenfolge -- siehe ingest/parallel.py."""
        from ingest import grounding
        from ingest.captioner import grounded_text  # noqa: F401  (Kompatibilitaet)
        from ingest.parallel import StageClock, run_parallel

        cfg = self.config
        clock = StageClock()
        self.parallel_clock = clock
        io_workers = cfg.workers
        write_workers = max(1, min(4, cfg.workers // 2))
        logger.info(
            "Parallel mode: %d readers, 1 GPU worker (batch %d), %d writers",
            io_workers, cfg.gpu_batch, write_workers,
        )

        # Modelle vorab laden -- sonst tun es mehrere Threads gleichzeitig.
        face_emb._ensure_loaded()
        scene._ensure_loaded()

        def build_record(fp, _clock, _unused):
            from ingest.parallel import _Timed
            photo_id = hashlib.sha256(fp.encode("utf-8")).hexdigest()
            record = PhotoRecord(photo_id=photo_id, file_path=fp)
            prior = preserved.get(photo_id)
            _apply_prior(record, prior)
            _fill_file_times(record, Path(fp))

            with _Timed(clock, "  io:decode"):
                from ingest.netfs import retry_io

                # Ein SMB-Aussetzer soll den Datensatz nicht kosten.
                raw_img, rgb, warn = retry_io(lambda: _load_image(fp), what=fp)
            if warn:
                record.file_warning = warn
            # Jetzt, nicht vorher: die Datei liegt nach dem Dekodieren im
            # Seiten-Cache, und der Hash kostet dann fast nichts.
            from ingest.identity import content_hash

            record.content_sha256 = content_hash(fp)
            if rgb is None:
                # Kein lesbares Bild -- aber Datum, Album und Pfad sind trotzdem
                # etwas wert. Das Foto faellt sonst still aus dem Index.
                logger.warning("Undecodable, indexing metadata only: %s", fp)
            with _Timed(clock, "  io:exif"):
                exif_data = exif_ext.extract(fp, image=raw_img)
            record.date = exif_data.get("date")
            record.date_source = exif_data.get("date_source")
            record.date_confidence = exif_data.get("date_confidence", 0.0)
            record.exif_datetime = exif_data.get("datetime")
            record.gps = exif_data.get("gps")
            record.exif = exif_data.get("raw")

            with _Timed(clock, "  io:folder"):
                fd = folder_parser.parse(fp)
            record.folder_name = fd.get("folder_name")
            record.subfolder = fd.get("subfolder")
            record.folder_type = fd.get("folder_type")
            record.folder_people = fd.get("people") or []
            record.sequence_in_folder = fd.get("sequence")
            record.location_hint = fd.get("location_hint")
            record.location_key = fd.get("location_key")
            record.date_hint = fd.get("date_hint")
            record.date_hint_source = fd.get("date_hint_source")

            record._bgr = None
            record._clip = None
            record._caption_b64 = None
            if rgb is not None:
                if captioner is not None:
                    # Sonst liest der Caption-Schritt die Datei ein zweites Mal
                    # ueber SMB und dekodiert sie erneut.
                    from ingest.captioner import jpeg_b64

                    with _Timed(clock, "  io:caption_jpeg"):
                        record._caption_b64 = jpeg_b64(fp, image=rgb)
                if cfg.thumbs:
                    with _Timed(clock, "  io:thumb"):
                        _make_thumb(fp, rgb)

                # Beides ist reine CPU-Arbeit und gehoert deshalb hierher, nicht
                # in den GPU-Thread: die BGR-Kopie kostet bei 12 MP rund 36 MB,
                # das CLIP-Preprocessing skaliert auf 224x224.
                from ingest.face_embedder import to_bgr

                with _Timed(clock, "  io:bgr"):
                    record._bgr = to_bgr(rgb)
                with _Timed(clock, "  io:clip_pre"):
                    record._clip = scene.preprocess(rgb)
            # Das grosse PIL-Bild wird ab hier nicht mehr gebraucht.
            record._prior = prior
            return record

        def gpu_step(batch):
            """Gesichter einzeln, CLIP als Stapel -- so ist die GPU am besten ausgelastet."""
            from ingest.parallel import _Timed

            for record in batch:
                if record._bgr is None:
                    continue
                with _Timed(clock, "  gpu:face"):
                    fr = face_emb.process(record.file_path, bgr=record._bgr)
                record.face_count = fr["count"]
                record.face_embedding = fr["primary_embedding"]
                record.face_boxes = fr["boxes"]
                record.faces = fr.get("faces") or []
                with _Timed(clock, "  gpu:match"):
                    record.person_suggestions = [
                        s["id"] for s in matcher.suggest(record.face_embedding)
                    ]
            with_image = [r for r in batch if r._clip is not None]
            with _Timed(clock, "  gpu:clip"):
                results = scene.encode_tensors([r._clip for r in with_image])
            for record, sr in zip(with_image, results):
                record.scene_tags = sr["tags"]
                record.clip_embedding = sr["embedding"]
                normalizer.normalize(record)
            for record in batch:
                if record._clip is None:
                    normalizer.normalize(record)
                # Speicher sofort freigeben -- sonst haelt die Warteschlange
                # dutzende dekodierte Bilder gleichzeitig.
                record._bgr = None
                record._clip = None

        def caption_one(record):
            """Eine Caption holen und ins Record schreiben. Laeuft im Pool."""
            from ingest.captioner import merge_tags

            structured = captioner.caption_structured(
                record.file_path,
                record_context(record),
                image_b64=getattr(record, "_caption_b64", None),
            )
            record._caption_b64 = None
            if structured:
                record.caption_de = structured.get("caption_de")
                record.caption_source = "llm"
                record.scene_tags = merge_tags(record.scene_tags,
                                               structured.get("scene_tags") or [])

        def finish(batch):
            from ingest.captioner import run_captions
            from ingest.parallel import _Timed

            todo = []
            for record in batch:
                prior = record._prior
                if prior and prior.get("caption_locked"):
                    # Von Hand geschrieben -- das Modell hat hier nichts zu suchen.
                    record.caption_de = prior.get("caption_de")
                    record.caption_source = prior.get("caption_source") or "manual"
                    record.caption_locked = True
                    record._caption_b64 = None
                elif captioner is not None:
                    todo.append(record)
                elif prior:
                    record.caption_de = prior.get("caption_de")
                    record.caption_source = prior.get("caption_source")

            if todo:
                with _Timed(clock, "  w:caption"):
                    run_captions(todo, caption_one, cfg.caption_workers)

            docs = []
            for record in batch:
                gp = grounding.record_payload(record)
                record.caption_display = grounding.caption_display(gp)
                record.ingested_at = datetime.now(timezone.utc).isoformat()
                docs.append(grounding.grounded_document(gp))

            from ingest.parallel import _Timed

            indexed = [(i, d) for i, d in enumerate(docs) if d]
            if indexed:
                with _Timed(clock, "  w:embed"):
                    vectors = text_emb.embed_batch([d for _, d in indexed])
                for (i, _), vec in zip(indexed, vectors):
                    batch[i].text_embedding = vec
            for record in batch:
                with _Timed(clock, "  w:qdrant"):
                    writer.upsert(record)

        def on_progress(n, path):
            self.progress.processed = n
            if cfg.progress_every and n % cfg.progress_every == 0:
                elapsed = time.time() - loop_start
                rate = n / elapsed if elapsed else 0.0
                logger.info(
                    "%d/%d (%.0f%%)  %.2f photos/s  ETA %s  errors %d",
                    n, len(files), n / len(files) * 100, rate,
                    _fmt_dur((len(files) - n) / rate if rate else 0), self.progress.errors,
                )
            self.job.update(processed=n, errors=self.progress.errors,
                            phase=Path(path).parent.name if path else "")

        stats = run_parallel(
            files,
            io_workers=io_workers,
            write_workers=write_workers,
            gpu_batch=cfg.gpu_batch,
            build_record=build_record,
            gpu_step=gpu_step,
            finish=finish,
            on_progress=on_progress,
            clock=clock,
        )
        self.progress.processed = stats.processed
        self.progress.errors = stats.errors

    #: Felder, die nicht aus der Datei rekonstruierbar sind und einen
    #: Re-Ingest ueberleben muessen.
    PRESERVE_FIELDS = (
        "caption_de",
        "caption_source",
        "caption_locked",
        "annotations",
        "person_ids",
        "person_names",
        "event_name",
        "event_excluded",
        # Der Papierkorb-Stempel existiert nur im Index -- in der Datei steht
        # nichts davon. Ein Lauf mit --no-resume holte weggeworfene Fotos
        # dadurch stillschweigend zurueck.
        #
        # `space` steht hier bewusst NICHT: es wird aus dem Pfad gerechnet.
        # Weicht das Feld vom Pfad ab, ist das Feld falsch (ingest/spaces.py),
        # und ein verschobenes Foto bekaeme seinen alten Bereich zurueck.
        "trashed_at",
    )

    def _flush_batch(self, pending, text_emb, writer, t) -> None:
        """Text-Vektoren des Batches in einem Rutsch holen, dann schreiben."""
        if not pending:
            return
        indexed = [(i, doc) for i, (_, doc) in enumerate(pending) if doc]
        if indexed:
            with t.stage("text_embed"):
                vectors = text_emb.embed_batch([doc for _, doc in indexed])
            for (i, _), vec in zip(indexed, vectors):
                pending[i][0].text_embedding = vec
        for record, _ in pending:
            try:
                with t.stage("write"):
                    writer.upsert(record)
                self.progress.processed += 1
            except Exception as e:
                logger.warning("Error writing %s: %s", record.file_path, e)
                self.progress.errors += 1
        pending.clear()

    def _load_preserved(self, writer, files: list[str]) -> dict[str, dict]:
        """photo_id -> {caption_de, annotations, person_ids, person_names}."""
        by_point = {
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_DNS, hashlib.sha256(f.encode("utf-8")).hexdigest()
                )
            ): hashlib.sha256(f.encode("utf-8")).hexdigest()
            for f in files
        }
        out: dict[str, dict] = {}
        ids = list(by_point)
        for i in range(0, len(ids), 256):
            try:
                found = writer.client.retrieve(
                    collection_name=self.config.collection,
                    ids=ids[i : i + 256],
                    with_payload=list(self.PRESERVE_FIELDS),
                    with_vectors=False,
                )
            except Exception as e:
                logger.warning("Preload of user data failed, continuing: %s", e)
                return out
            for point in found:
                payload = point.payload or {}
                kept = {k: payload.get(k) for k in self.PRESERVE_FIELDS if payload.get(k)}
                if kept:
                    out[by_point[str(point.id)]] = kept
        return out

    def _warm_caption_model(self) -> None:
        """Das Caption-Modell einmal kontrolliert mit `CAPTION_NUM_CTX` laden.

        Ollama laedt neu, sobald das angeforderte `num_ctx` vom geladenen
        abweicht -- typisch 112 s, weil das Profil auf 128k steht. Passiert das
        erst mitten im Lauf unter mehreren gleichzeitigen Anfragen, warten alle
        Writer darauf, und der Scheduler wirft dabei das Embedding-Modell aus
        dem VRAM (bei 128k bleiben nur 2,6 GB frei). Beides einmal vorab und in
        definierter Reihenfolge: erst das grosse Modell, dann den Embedder
        zurueckholen.

        Nebeneffekt, der den Lauf ueberhaupt erst passen laesst: bei 8192 statt
        131072 werden 4,6 GB frei -- Platz fuer insightface und CLIP, die im
        selben Prozess auf derselben Karte liegen.
        """
        from ingest.ollama_client import (
            CAPTION_MODEL, CAPTION_NUM_CTX, EMBED_MODEL, ollama_url, post_json,
        )

        from ingest.captioner import caption_options

        url = ollama_url(self.config.ollama_url)
        started = time.time()
        options = dict(caption_options())
        options["num_predict"] = 1
        try:
            post_json(
                f"{url}/api/generate",
                {"model": CAPTION_MODEL, "prompt": "ok", "stream": False,
                 "options": options, "keep_alive": -1},
                timeout=900,
            )
        except Exception as e:
            # Kein Grund abzubrechen -- der erste Caption-Aufruf laedt sonst eben.
            logger.warning("Could not warm %s: %s", CAPTION_MODEL, e)
            return
        logger.info(
            "Warmed %s (%s) in %.0fs", CAPTION_MODEL,
            f"num_ctx={CAPTION_NUM_CTX}" if CAPTION_NUM_CTX else "Kontext laut Profil",
            time.time() - started,
        )
        try:
            post_json(f"{url}/api/embed",
                      {"model": EMBED_MODEL, "input": "ok", "keep_alive": -1},
                      timeout=300)
        except Exception as e:
            logger.warning("Could not re-pin %s: %s", EMBED_MODEL, e)

    def _drop_already_indexed(self, writer, files: list[str]) -> list[str]:
        """Fotos aussortieren, die schon als Punkt in der Collection liegen."""
        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, hashlib.sha256(f.encode("utf-8")).hexdigest()))
            for f in files
        ]
        existing: set[str] = set()
        for i in range(0, len(ids), 256):
            chunk = ids[i : i + 256]
            try:
                found = writer.client.retrieve(
                    collection_name=self.config.collection,
                    ids=chunk,
                    with_payload=False,
                    with_vectors=False,
                )
                existing.update(str(p.id) for p in found)
            except Exception as e:
                logger.warning("Resume lookup failed, processing all: %s", e)
                return files
        return [f for f, pid in zip(files, ids) if pid not in existing]


def _dry_run(sources: list[str], exclude: list[str]) -> None:
    """Zeigen, was aufgenommen wuerde -- je Verzeichnis und gesamt.

    Ein Archiv enthaelt selten nur Alben. Wer 40 000 Dateien indiziert, will
    vorher sehen, welche das sind, statt es hinterher am Suchergebnis zu
    merken.
    """
    from collections import Counter

    from ingest.scanner import NASScanner

    files = NASScanner(sources, exclude=exclude).scan()
    per_dir: Counter = Counter()
    roots = [str(s).rstrip("/") for s in sources]
    for f in files:
        root = max((r for r in roots if f.startswith(r + "/")), key=len, default=None)
        if root is None:
            per_dir["(ausserhalb)"] += 1
            continue
        rest = f[len(root) + 1:].split("/")
        per_dir[f"{Path(root).name}/{rest[0]}" if len(rest) > 1 else Path(root).name] += 1
    print()
    for name, n in per_dir.most_common(40):
        print(f"  {name[:56]:56s} {n:7d}")
    if len(per_dir) > 40:
        print(f"  ... und {len(per_dir) - 40} weitere Verzeichnisse")
    print(f"  {'GESAMT':56s} {len(files):7d}")
    print()
    print("Nichts geschrieben (--dry-run).")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PhotoVault Ingest")
    parser.add_argument(
        "--source", action="append", default=None,
        help="Verzeichnis, das aufgenommen wird. Mehrfach angebbar.",
    )
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Verzeichnis, das ausgelassen wird. Mehrfach angebbar.",
    )
    parser.add_argument(
        "--sources-file",
        help="Textdatei mit einem Verzeichnis je Zeile ('#' kommentiert aus, "
             "fuehrendes '-' schliesst aus). Alternative zu --source.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Nur zeigen, was aufgenommen wuerde -- nichts schreiben.",
    )
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--model-dir",
        default=os.environ.get("MODEL_DIR", str(Path.home() / ".cache" / "photovault-models")),
    )
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument(
        "--skip-caption",
        action="store_true",
        help="Kein Qwen-Vision; Metadaten+Face+CLIP werden trotzdem indexiert und text-embeddet",
    )
    parser.add_argument("--limit", type=int, default=None, help="Nur die ersten N Fotos")
    parser.add_argument("--include", default=None, help="Nur Pfade, die diesen Text enthalten")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Auch bereits indizierte Fotos erneut verarbeiten",
    )
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument(
        "--no-thumbs",
        action="store_true",
        help="Keine Vorschaubilder erzeugen (die erste Suche wird dann langsam)",
    )
    parser.add_argument(
        "--workers", type=int, default=6,
        help="Leser-Threads; 1 = altes sequenzielles Verhalten, 0 = CPU-Anzahl",
    )
    parser.add_argument("--gpu-batch", type=int, default=16, help="Bilder je CLIP-Durchlauf")
    parser.add_argument(
        "--caption-workers", type=int, default=1,
        help="Caption-Anfragen je Writer-Thread (die Writer parallelisieren bereits)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not args.verbose:
        for noisy in ("httpx", "httpcore", "urllib3", "PIL"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    sources = list(args.source or [])
    exclude = list(args.exclude)
    if args.sources_file:
        from ingest.scanner import load_sources

        inc, exc = load_sources(args.sources_file)
        sources = inc + sources
        exclude = exc + exclude
    if not sources:
        parser.error("Weder --source noch --sources-file angegeben.")

    if args.dry_run:
        _dry_run(sources, exclude)
        return

    config = IngestConfig(
        source=sources[0],
        extra_sources=sources[1:],
        exclude=exclude,
        qdrant_url=args.qdrant_url,
        batch_size=args.batch_size,
        model_dir=args.model_dir,
        skip_caption=args.skip_caption,
        ollama_url=args.ollama_url,
        collection=args.collection,
        limit=args.limit,
        include=args.include,
        resume=not args.no_resume,
        progress_every=args.progress_every,
        thumbs=not args.no_thumbs,
        workers=(os.cpu_count() or 4) if args.workers == 0 else args.workers,
        gpu_batch=args.gpu_batch,
        caption_workers=args.caption_workers,
    )
    pipeline = IngestPipeline(config)
    progress = pipeline.run()
    print(f"\nDone: {progress.to_dict()}")
    if progress.processed:
        wall = (progress.finished_at or 0) - (progress.started_at or 0)
        print(f"\nWall: {wall:.1f}s   {progress.processed / wall:.2f} photos/s\n")
        if pipeline.parallel_clock is not None:
            totals = pipeline.parallel_clock.totals()
            print(f"{'stage':<16}{'total_s':>9}{'ms/photo':>10}   (laufen gleichzeitig)")
            for name, secs in sorted(totals.items(), key=lambda kv: -kv[1]):
                print(f"{name:<16}{secs:>9.1f}{secs / progress.processed * 1000:>10.1f}")
        else:
            print(pipeline.timer.report(progress.processed))


def _load_image(file_path: str):
    """(Original, RGB-Fassung, Warnung). Das Original traegt die EXIF-Daten, die beim
    Konvertieren verloren gehen; die RGB-Fassung brauchen die Modelle."""
    try:
        from PIL import Image, ImageFile

        ImageFile.LOAD_TRUNCATED_IMAGES = True
        raw = Image.open(file_path)
        warn = None
        try:
            raw.load()
        except OSError as e:
            logger.warning("truncated image during ingest: %s (%s)", file_path, e)
            warn = "truncated"
            if getattr(raw, "im", None) is None:
                return None, None, "unreadable"
        rgb = raw if raw.mode == "RGB" else raw.convert("RGB")
        return raw, rgb, warn
    except Exception as e:
        logger.debug("Could not decode %s: %s", file_path, e)
        return None, None, "unreadable"


def _make_thumb(file_path: str, image=None) -> None:
    """Vorschaubild in den Cache legen. Ein Fehler hier darf den Ingest nie stoppen."""
    try:
        from api.thumbs import get_thumb

        get_thumb(file_path, size=320, image=image)
    except Exception as e:
        logger.debug("Thumb generation failed for %s: %s", file_path, e)


def _fmt_dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _fill_file_times(record: PhotoRecord, path: Path) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    record.file_size = st.st_size
    record.file_mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    record.file_ctime = datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()


def _apply_prior(record: PhotoRecord, prior: dict | None) -> None:
    if not prior:
        return
    record.person_ids = list(prior.get("person_ids") or [])
    record.person_names = list(prior.get("person_names") or [])
    record.annotations = list(prior.get("annotations") or [])
    if prior.get("event_name"):
        record.event_name = prior["event_name"]
    record.event_excluded = bool(prior.get("event_excluded"))
    # Nur weiterreichen, nie setzen: was im Papierkorb liegt, bleibt dort,
    # auch wenn die Datei erneut eingelesen wird.
    record.trashed_at = prior.get("trashed_at")


def record_context(record: PhotoRecord) -> dict:
    return {
        "folder_name": record.folder_name,
        "event_name": record.event_name,
        "date": record.date,
        "date_source": record.date_source,
        "file_ctime": record.file_ctime,
        "file_mtime": record.file_mtime,
        "filename": Path(record.file_path).name if record.file_path else None,
        "sequence": record.sequence_in_folder,
        "location": record.location,
        "face_count": record.face_count,
        "people_assigned": record.person_suggestions or record.person_ids,
        "people_album": record.folder_people,
        "clip_tags": record.scene_tags,
    }


if __name__ == "__main__":
    main()
