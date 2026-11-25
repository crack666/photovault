"""Alle Fotos und Videos mit einem anderen Bildmodell einbetten (#66).

Schreibt in den Raum des Modells (`ingest/image_space.py`), fuer SigLIP 2
also die Beistell-Collection `photos_siglip2`. `photos` bleibt dabei
unberuehrt: der CLIP-Vektor, die Namen, Notizen und Tags darin aendert
dieser Lauf nicht -- ausser mit `--tags`, siehe unten.

Fortsetzbar: was im neuen Raum schon einen Vektor hat, wird uebersprungen.
Damit laesst sich der Bestand in Etappen hinter dem GPU-Tor rechnen
(`--minutes`), und nach einem neuen Ingest zieht derselbe Aufruf nur die
neuen Punkte nach.

    python -m tools.reembed_image --dry-run              # was zu tun ist
    python -m tools.reembed_image --minutes 50           # eine Etappe
    python -m tools.reembed_image --tags --dry-run       # Tag-Wechsel ansehen
    python -m tools.reembed_image --tags                 # Tags uebernehmen
    python -m tools.reembed_image --mit-tags             # beides nacheinander (Jobs-Seite)

**`--tags`** uebernimmt die Szenen-Tags des neuen Modells nach
`photos.scene_tags` -- nur fuer Punkte **ohne** `caption_de`, wie
`tools.backfill_clip`: wo eine Beschreibung ist, stammen die Tags aus ihr
und sind die besseren. Der alte Stand wird vorher nach `scene_tags_clip`
gesichert (nur beim ersten Mal), ein Zurueckdrehen ist damit ein
Umkopieren. Die Tags stehen auch im Dokument des Textvektors
(`grounding.grounded_document`); der wird fuer genau diese Punkte gleich
erneuert, wenn ein Embedder da ist (`--ohne-text` laesst es).

Rechnet auf der Grafikkarte und fragt das GPU-Tor **nicht** selbst:
vorher `gpu-tor.ps1 reservieren`.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from api.qdrant_util import PHOTOS, client

logger = logging.getLogger(__name__)

BATCH = 64
#: Leser fuer Dekodieren und Vorverarbeiten. Der langsame Teil ist das
#: Netzlaufwerk, nicht die GPU (40 Bilder/s, #59).
READERS = 8


def collect(qc, skip: set[str], prefix: str | None = None) -> list[tuple]:
    """[(Punkt-ID, Pfad, ist_video, hat_caption)] fuer alles, was noch fehlt.

    Der Papierkorb bleibt drin, wie bei `backfill_clip`: wer ein Foto
    rettet, soll es im neuen Raum wiederfinden.
    """
    out, offset = [], None
    while True:
        batch, offset = qc.scroll(collection_name=PHOTOS, limit=1024, offset=offset,
                                  with_payload=["file_path", "kind", "caption_de"],
                                  with_vectors=False)
        for p in batch:
            pid = str(p.id)
            if pid in skip:
                continue
            pl = p.payload or {}
            fp = pl.get("file_path") or ""
            if not fp or (prefix and not fp.startswith(prefix)):
                continue
            out.append((pid, fp, pl.get("kind") == "video", bool(pl.get("caption_de"))))
        if offset is None:
            return out


def _prepare(tagger, item: tuple) -> tuple:
    """(item, [Tensoren], frames|None, Grund|None) -- laeuft im Leser-Pool."""
    pid, fp, ist_video, _cap = item
    try:
        if ist_video:
            from ingest.video_clip import sample_frames

            frames = sample_frames(fp)
            return item, [tagger.preprocess(img) for _, img in frames], frames, None
        from ingest.pipeline import _load_image

        _raw, rgb, warn = _load_image(fp)
        if rgb is None:
            return item, [], None, f"nicht dekodierbar ({warn})"
        return item, [tagger.preprocess(rgb)], None, None
    except Exception as e:  # eine Datei, nicht der Lauf
        return item, [], None, str(e)


def run(qc, spec, aufgaben: list[tuple], model_dir: str, *, batch: int = BATCH,
        minutes: float | None = None, readers: int = READERS, on_progress=None) -> dict:
    """Einbetten und schreiben, bis alles durch ist oder die Zeit um."""
    from ingest import image_space
    from ingest.scene_tagger import SceneTagger
    from ingest.video_clip import choose

    tagger = SceneTagger(model_dir, model=spec.key)
    image_space.ensure_collection(qc, spec)
    fertig = uebersprungen = 0
    begonnen = time.time()
    frist = begonnen + minutes * 60 if minutes else None
    pool = ThreadPoolExecutor(max_workers=readers)
    angehalten = False
    try:
        for i in range(0, len(aufgaben), batch):
            if frist and time.time() >= frist:
                angehalten = True
                break
            vorbereitet = list(pool.map(lambda it: _prepare(tagger, it), aufgaben[i:i + batch]))
            tensoren, owner = [], []
            for k, (_item, ts, _frames, grund) in enumerate(vorbereitet):
                for t_ in ts:
                    tensoren.append(t_)
                    owner.append(k)
            ergebnisse = tagger.encode_tensors(tensoren) if tensoren else []
            je: dict[int, list[dict]] = {}
            for k, r in zip(owner, ergebnisse):
                je.setdefault(k, []).append(r)
            items = []
            for k, (item, _ts, frames, grund) in enumerate(vorbereitet):
                pid, fp, ist_video, _cap = item
                got = je.get(k)
                if not got:
                    logger.warning("uebersprungen: %s (%s)", fp, grund or "kein Bild")
                    uebersprungen += 1
                    continue
                r = choose(got, frames) if ist_video else got[0]
                if not r.get("embedding"):
                    uebersprungen += 1
                    continue
                items.append((pid, r["embedding"], {
                    "kind": "video" if ist_video else "photo",
                    "scene_tags": list(r.get("tags") or []),
                }))
            image_space.write(qc, spec, items)
            fertig += len(items)
            dauer = time.time() - begonnen
            if on_progress:
                on_progress(fertig, uebersprungen)
            print(f"  {fertig + uebersprungen}/{len(aufgaben)}  "
                  f"{fertig / dauer if dauer else 0:.1f}/s", flush=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"done": fertig, "skipped": uebersprungen, "stopped": angehalten,
            "seconds": round(time.time() - begonnen, 1)}


def plan_tags(qc, spec) -> list[tuple[str, list[str], list[str] | None, bool]]:
    """[(ID, neue Tags, alte Tags, Sicherung schon da)] fuer Punkte ohne Beschreibung."""
    neu: dict[str, list[str]] = {}
    offset = None
    while True:
        batch, offset = qc.scroll(collection_name=spec.collection, limit=2048, offset=offset,
                                  with_payload=["scene_tags"], with_vectors=False)
        for p in batch:
            neu[str(p.id)] = list((p.payload or {}).get("scene_tags") or [])
        if offset is None:
            break
    out, offset = [], None
    while True:
        batch, offset = qc.scroll(collection_name=PHOTOS, limit=1024, offset=offset,
                                  with_payload=["caption_de", "scene_tags", "scene_tags_clip"],
                                  with_vectors=False)
        for p in batch:
            pl = p.payload or {}
            pid = str(p.id)
            if pl.get("caption_de") or pid not in neu:
                continue
            alt = pl.get("scene_tags")
            if list(alt or []) == neu[pid]:
                continue
            out.append((pid, neu[pid], alt, "scene_tags_clip" in pl))
        if offset is None:
            return out


def apply_tags(qc, plan: list[tuple]) -> int:
    """Den Plan schreiben; die alten Tags vorher sichern."""
    for pid, neu, alt, gesichert in plan:
        pl: dict = {"scene_tags": neu}
        if not gesichert:
            pl["scene_tags_clip"] = list(alt or [])
        qc.set_payload(collection_name=PHOTOS, payload=pl, points=[pid], wait=False)
    return len(plan)


def _retext(qc, plan: list[tuple]) -> dict | None:
    """Textvektoren der Punkte, deren Tags gewechselt haben. None = kein Embedder."""
    from api.capabilities import missing
    from ingest.ollama_client import apply_llm_env
    from ingest.reembed import rebuild_text_vectors

    apply_llm_env()
    grund = missing(kinds=("embed",))
    if grund:
        print(f"Textvektoren bleiben, wie sie sind: {grund}")
        return None
    # `apply_tags` schreibt ohne `wait`; ein Abruf mit Zusage davor, damit das
    # Dokument die neuen Tags sieht.
    if plan:
        qc.set_payload(collection_name=PHOTOS, payload={"scene_tags": plan[-1][1]},
                       points=[plan[-1][0]], wait=True)
    return rebuild_text_vectors(qc, [p[0] for p in plan])


def _tags(qc, spec, *, dry_run: bool, retext: bool) -> None:
    plan = plan_tags(qc, spec)
    neu = sum(1 for x in plan if not x[3])
    print(f"{len(plan)} Punkt(e) ohne Beschreibung bekommen {spec.key}-Tags "
          f"({neu} sichern ihre alten nach scene_tags_clip).")
    for pid, n, a, _g in plan[:10]:
        print(f"  {pid}: {a} -> {n}")
    if dry_run or not plan:
        print("Trockenlauf -- nichts geschrieben." if dry_run else "Nichts zu tun.")
        return
    print(f"{apply_tags(qc, plan)} geschrieben.")
    if retext:
        got = _retext(qc, plan)
        if got:
            print(f"Textvektoren: {got['updated']} neu, {got['skipped']} uebersprungen, "
                  f"{got['failed']} fehlgeschlagen.")


def main(argv=None) -> int:
    from ingest import image_space
    from ingest.scene_tagger import image_model

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="siglip2", help="Bildmodell (Standard: siglip2)")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, was zu tun ist")
    ap.add_argument("--prefix", help="nur Dateipfade unter diesem Verzeichnis")
    ap.add_argument("--limit", type=int, help="hoechstens so viele Punkte")
    ap.add_argument("--minutes", type=float, help="nach so vielen Minuten anhalten (Etappe)")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--tags", action="store_true",
                    help="statt einzubetten: Szenen-Tags des Modells uebernehmen (ohne Beschreibung)")
    ap.add_argument("--mit-tags", action="store_true",
                    help="nach dem Einbetten gleich die Tags uebernehmen (nur, wenn alles durch ist)")
    ap.add_argument("--ohne-text", action="store_true",
                    help="nach --tags die Textvektoren nicht erneuern")
    ap.add_argument("--model-dir",
                    default=os.environ.get("MODEL_DIR",
                                           str(Path.home() / ".cache" / "photovault-models")))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    spec = image_model(args.model)
    if spec.collection == PHOTOS:
        print("Fuer clip: python -m tools.backfill_clip")
        return 2
    qc = client()

    if args.tags:
        _tags(qc, spec, dry_run=args.dry_run, retext=not args.ohne_text)
        return 0

    have, base = image_space.fill(qc, spec)
    print(f"{spec.collection}: {have} von {base} Punkten mit clip-Vektor "
          f"({100 * have / base if base else 0:.1f} %)")
    aufgaben = collect(qc, image_space.existing_ids(qc, spec), args.prefix)
    videos = sum(1 for a in aufgaben if a[2])
    print(f"{len(aufgaben)} offen, davon {videos} Videos.")
    if args.limit is not None:
        aufgaben = aufgaben[: args.limit]
    if args.dry_run or not aufgaben:
        print("Trockenlauf -- nichts geschrieben." if args.dry_run else "Nichts zu tun.")
        if args.mit_tags and not args.dry_run:
            _tags(qc, spec, dry_run=False, retext=not args.ohne_text)
        return 0
    job = None
    if os.environ.get("PHOTOVAULT_JOB_CHILD") == "1":
        from ingest.jobs import JobTracker

        job = JobTracker(qc, kind="image-space", source=spec.collection)
        job.update(phase="embedding", total=len(aufgaben), force=True)
    try:
        got = run(qc, spec, aufgaben, args.model_dir, batch=args.batch, minutes=args.minutes,
                  on_progress=(lambda n, f: job.update(processed=n, errors=f)) if job else None)
        have, base = image_space.fill(qc, spec)
        if args.mit_tags and not got["stopped"] and args.limit is None:
            _tags(qc, spec, dry_run=False, retext=not args.ohne_text)
    except Exception:
        if job:
            job.finish("error", phase="abgebrochen", errors=1)
        raise
    if job:
        job.finish("done", phase="done", processed=got["done"], errors=got["skipped"])
    print(f"{got['done']} geschrieben, {got['skipped']} uebersprungen, {got['seconds']} s"
          + (" (Etappe um, fortsetzbar)" if got["stopped"] else "")
          + f". Jetzt {have} von {base}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
