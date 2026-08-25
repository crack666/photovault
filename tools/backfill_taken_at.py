"""`taken_at` für bereits indizierte Fotos nachziehen.

Bis 2026-08-24 verwarf der Ingest die Uhrzeit: der EXIF-Extraktor schnitt sie
mit `strftime("%Y-%m-%d")` ab, der Normalizer baute den Zeitstempel aus dem
Tagesdatum neu. Jedes Foto stand damit auf Mitternacht -- und ohne Uhrzeit
lässt sich keine Serie von der nächsten trennen.

Dieses Werkzeug liest die Aufnahmezeit nach, ohne den gesamten Ingest zu
wiederholen: nur EXIF, keine Gesichtserkennung, kein CLIP, keine Vektoren.
DateTimeOriginal vor Digitalisiert vor DateTime (Kopierzeit).
`--preview` ist die Probe, bevor jemand schreibt — siehe docs/dates.md.

    python -m tools.backfill_taken_at --preview
    python -m tools.backfill_taken_at --dry-run
    python -m tools.backfill_taken_at
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256


def exif_datetime(file_path: str) -> str | None:
    """Aufnahmezeit aus EXIF -- Original vor Digitalisiert vor DateTime."""
    from datetime import datetime

    from PIL import Image

    from ingest.exif_extractor import exif_capture_stamp

    with Image.open(file_path) as img:
        raw = exif_capture_stamp(img.getexif())
    if not raw:
        return None
    dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(raw) -> str | None:
    from datetime import datetime

    if not raw:
        return None
    text = raw.decode("ascii", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
    text = text.strip("\x00 ").replace("-", ":")[:19]
    try:
        dt = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_exif_pair(file_path: str) -> tuple[str | None, str | None]:
    """(Aufnahme = Original/Digitalisiert/DateTime, reines DateTime-306)."""
    from PIL import Image

    from ingest.exif_extractor import exif_capture_stamp

    with Image.open(file_path) as img:
        exif = img.getexif() or {}
        chosen = _to_iso(exif_capture_stamp(exif))
        copied = _to_iso(exif.get(306))
    return chosen, copied


def preview(client, collection: str = "photos", workers: int = 8,
            limit: int | None = None) -> dict:
    """Trockenlauf: Original vs. bisheriger taken_at, ohne zu schreiben.

    Zählt Tagessprünge (Kopierzeit → Aufnahme) getrennt von bloßen Uhrzeiten.
    """
    from collections import Counter, defaultdict
    from pathlib import Path

    stats = Counter()
    shifts = defaultdict(lambda: {"n": 0, "sample": None})
    samples = []
    started = time.time()
    offset = None
    seen = 0
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["date", "file_path", "taken_at", "folder_name", "date_source"],
            with_vectors=False,
        )
        if not batch:
            break
        payloads = [p.payload or {} for p in batch]

        def _one(pl):
            path = pl.get("file_path")
            if not path:
                return pl, None, None, "no_path"
            try:
                chosen, copied = retry_io(lambda: read_exif_pair(path), what=path)
            except FileNotFoundError:
                return pl, None, None, "missing"
            except Exception:
                return pl, None, None, "unreadable"
            return pl, chosen, copied, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_one, payloads))

        for pl, chosen, copied, err in rows:
            seen += 1
            if err:
                stats[err] += 1
                continue
            old = pl.get("taken_at") or ""
            if not chosen:
                stats["no_exif"] += 1
                continue
            if chosen == old:
                stats["unchanged"] += 1
                continue
            if copied and chosen[:10] != (copied[:10] if copied else "") and chosen[:10] != (old[:10] if old else ""):
                stats["day_shift"] += 1
            elif old[:10] == chosen[:10]:
                stats["time_only"] += 1
            else:
                stats["day_shift"] += 1
            folder = pl.get("folder_name") or "?"
            key = (folder, (old or "?")[:10], chosen[:10])
            rec = shifts[key]
            rec["n"] += 1
            if rec["sample"] is None:
                rec["sample"] = {
                    "file": Path(pl.get("file_path") or "").name,
                    "folder": folder,
                    "old": old,
                    "new": chosen,
                    "copied": copied,
                    "source": pl.get("date_source"),
                }
                samples.append(rec["sample"])

        logger.info("  %d gesehen, %d Tagessprünge, %d nur Uhrzeit",
                    seen, stats["day_shift"], stats["time_only"])
        if offset is None or (limit and seen >= limit):
            break

    stats["seen"] = seen
    stats["elapsed_s"] = round(time.time() - started)
    ranked = sorted(shifts.items(), key=lambda kv: -kv[1]["n"])
    print()
    print(f"Fotos {seen}  unverändert {stats['unchanged']}  "
          f"nur Uhrzeit {stats['time_only']}  anderer Tag {stats['day_shift']}  "
          f"kein EXIF {stats['no_exif']}  fehlend {stats['missing']}  "
          f"unlesbar {stats['unreadable']}  -- {stats['elapsed_s']}s")
    print()
    print("Tagessprünge nach Album (Kopier-/Indexdatum → Aufnahme):")
    shown_groups = 0
    for (folder, old_d, new_d), rec in ranked:
        if old_d == new_d:
            continue
        print(f"  {rec['n']:4d}  {folder[:32]:32s}  {old_d}  →  {new_d}"
              f"  z.B. {rec['sample']['file']}")
        shown_groups += 1
        if shown_groups >= 40:
            break
    print()
    print("Stichproben (Datei, bisher, neu, DateTime-306 falls anders):")
    shown = 0
    for (folder, old_d, new_d), rec in ranked:
        if old_d == new_d:
            continue
        s = rec["sample"]
        extra = ""
        if s["copied"] and s["copied"][:10] != s["new"][:10]:
            extra = f"  306={s['copied']}"
        print(f"  {s['folder']} / {s['file']}")
        print(f"      index {s['old'] or '—'}  →  original {s['new']}{extra}")
        shown += 1
        if shown >= 15:
            break
    return {"stats": dict(stats), "shift_groups": len(shifts)}


def resolve(payload: dict) -> str | None:
    """Neuer Zeitstempel — Original auch dann, wenn der Kalendertag wechselt.

    Die alte Regel (EXIF nur bei gleichem Tag wie payload.date) hat genau die
    Importnächte übersprungen, die Serien vermischen. Dateizeit nur, wenn ihr
    Tag zum bisher gespeicherten Datum passt: sonst ist sie der Kopierzeitpunkt.
    """
    date = payload.get("date")
    path = payload.get("file_path")
    if path:
        try:
            got = retry_io(lambda: exif_datetime(path), what=path)
            if got:
                return got
        except FileNotFoundError:
            logger.debug("weg: %s", path)
        except Exception as e:
            logger.debug("EXIF nicht lesbar (%s): %s", path, e)
    if date and len(date) == 10:
        mtime = payload.get("file_mtime")
        if mtime and str(mtime)[:10] == date and len(str(mtime)) >= 19:
            return f"{str(mtime)[:19]}Z"
    return None


def run(client, collection: str = "photos", workers: int = 8,
        dry_run: bool = False, limit: int | None = None) -> dict:
    stats = {"seen": 0, "from_exif": 0, "from_mtime": 0, "unchanged": 0, "updated": 0}
    started = time.time()
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=["date", "file_path", "file_mtime", "taken_at"],
            with_vectors=False,
        )
        if not batch:
            break
        pairs = [(p.id, p.payload or {}) for p in batch]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fresh = list(pool.map(lambda item: resolve(item[1]), pairs))

        for (pid, payload), new in zip(pairs, fresh):
            stats["seen"] += 1
            if new is None or new == payload.get("taken_at"):
                stats["unchanged"] += 1
                continue
            # Woher der Wert kam, nur fuer die Statistik.
            mtime = str(payload.get("file_mtime") or "")
            stats["from_mtime" if new[:19] == mtime[:19] else "from_exif"] += 1
            if not dry_run:
                patch = {"taken_at": new}
                if new[:10] != (payload.get("date") or "")[:10]:
                    patch["date"] = new[:10]
                client.set_payload(collection_name=collection,
                                   payload=patch, points=[pid], wait=False)
            stats["updated"] += 1

        logger.info("  %d gesehen, %d neu", stats["seen"], stats["updated"])
        if offset is None or (limit and stats["seen"] >= limit):
            break

    elapsed = time.time() - started
    logger.info(
        "%s: %d Fotos, %d Zeitstempel neu (%d aus EXIF, %d aus Dateizeit), "
        "%d unveraendert -- %.0fs",
        "Trockenlauf" if dry_run else "Fertig", stats["seen"], stats["updated"],
        stats["from_exif"], stats["from_mtime"], stats["unchanged"], elapsed,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview", action="store_true",
                        help="Original vs Index, inkl. Tagessprünge, ohne schreiben")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    q = QdrantClient(url=args.qdrant_url)
    if args.preview:
        preview(q, collection=args.collection, workers=args.workers, limit=args.limit)
        return
    run(q, collection=args.collection,
        workers=args.workers, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
