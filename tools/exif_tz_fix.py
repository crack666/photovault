"""Von PhotoVault in UTC geschriebene Aufnahmezeiten in Ortszeit umschreiben.

Bis Issue #43 hat `tools/exif_repair.py` die Uhrzeit aus dem Index -- UTC --
unveraendert nach `DateTimeOriginal` geschrieben. Das Feld ist Ortszeit; jeder
Betrachter zeigt solche Dateien im Sommer zwei Stunden, im Winter eine Stunde
zu frueh.

Betroffen ist, was diese Notiz traegt (Beispiel aus dem Issue):

    photovault:src=filename;mtime=2022-07-04T17:51:19;cap=llm
    DateTimeOriginal 2022:07:04 17:51:19

Erkennungsmerkmal: `DateTimeOriginal` ist *genau* die in der Notiz
festgehaltene Dateizeit (UTC). Nur dann ist sicher, dass die Uhr aus der
Dateizeit stammt und in UTC geschrieben wurde. Alles andere bleibt liegen:

- keine Notiz / fremde Datei / `src=exif` -- nicht von uns, nicht anfassen
- `src=accepted`, `offset`, `revert` -- ganze Zeitpunkte, nicht aus mtime
- Notiz schon im neuen Format (`+mtime`) oder mit `OffsetTimeOriginal`
- `DateTimeOriginal` ungleich Notiz-mtime -- Herkunft der Uhr unklar
- Ortszeit laege auf einem anderen Tag als der geschriebene (nahe
  Mitternacht) -- dann gehoert die Uhr nicht zu dem Tag aus dem Namen;
  das ist eine Entscheidung fuer einen Menschen, kein Umrechnen

Korrigiert wird mit `write_capture_time` (Ortszeit + `OffsetTimeOriginal`,
Dateizeit bleibt, Gegenprobe auf den Zeitpunkt). Der `prev`-Eintrag der Notiz
bleibt der urspruengliche -- unser eigener Fehler wird nicht zum
"vorherigen Wert". Die Notiz bekommt `src=filename+mtime`.

Der Index muss danach nicht nachgezogen werden: `taken_at` war und bleibt UTC.

    python -m tools.exif_tz_fix /mnt/photo                    # Trockenlauf, zaehlen
    python -m tools.exif_tz_fix /mnt/photo --sample 5         # mit Beispielen
    python -m tools.exif_tz_fix /mnt/photo --apply --limit 50 # erst eine Handvoll
    python -m tools.exif_tz_fix /mnt/photo --apply

Ortszeit: `PHOTOVAULT_TZ` oder `TZ` (z. B. `TZ=Europe/Berlin`), sonst die des
Systems. Im Container ist das UTC -- dann laeuft das Werkzeug leer
("Ortszeit = UTC") und schreibt nichts.
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ingest.exif_writer import (
    _DT_ORIGINAL,
    _OFFSET_ORIGINAL,
    _USER_COMMENT,
    WRITABLE_SUFFIXES,
    ExifWriteError,
    _parse,
    note_fields,
    write_capture_time,
)
from ingest.localtime import to_local, zone
from ingest.netfs import retry_io
from tools.exif_repair import CLOCK_FROM_MTIME, note_source

logger = logging.getLogger(__name__)

#: Herkuenfte, deren Uhr aus der Dateizeit kam. `abgeleitet` ist der
#: Platzhalter von `exif_repair`, wenn der Index keine Herkunft nannte.
FIXABLE = CLOCK_FROM_MTIME | {"file_time", "abgeleitet"}

FIX = "wird korrigiert"
DONE = "korrigiert"


def classify(path: str) -> tuple[str, datetime | None, str]:
    """`(befund, zeitpunkt_utc, neue_herkunft)` fuer eine Datei. Liest nur."""
    import piexif

    if os.path.splitext(path)[1].lower() not in WRITABLE_SUFFIXES:
        return "anderes Format", None, ""
    try:
        exif = piexif.load(path)
    except Exception:
        return "nicht lesbar", None, ""
    ifd = exif.get("Exif") or {}
    fields = note_fields(ifd.get(_USER_COMMENT))
    src = fields.get("src") or ""
    if not src:
        return "keine Datumsnotiz", None, ""
    if "+" in src:
        return "schon im neuen Format", None, ""
    if src not in FIXABLE:
        return f"Herkunft {src}", None, ""
    if ifd.get(_OFFSET_ORIGINAL):
        return "hat schon OffsetTimeOriginal", None, ""
    written = _parse(ifd.get(_DT_ORIGINAL))
    if written is None:
        return "kein DateTimeOriginal", None, ""
    raw = (fields.get("mtime") or "").strip().rstrip("Z")
    try:
        mtime_utc = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return "keine mtime in der Notiz", None, ""
    if written != mtime_utc:
        return "Uhr nicht aus der Notiz-mtime", None, ""
    when = mtime_utc.replace(tzinfo=timezone.utc)
    local = to_local(when).replace(tzinfo=None)
    if local == written:
        return "Ortszeit = UTC", None, ""
    if src != "file_time" and local.date() != written.date():
        return "Tag wuerde wechseln (von Hand)", None, ""
    return FIX, when, note_source(src)


def _walk(roots: list[str]):
    for root in roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if os.path.splitext(name)[1].lower() in WRITABLE_SUFFIXES:
                    yield os.path.join(dirpath, name)


def _process(path: str, apply: bool) -> tuple[str, str, str]:
    try:
        verdict, when, source = retry_io(lambda: classify(path), what=path)
    except FileNotFoundError:
        return path, "Datei weg", ""
    except Exception as e:
        logger.debug("nicht lesbar (%s): %s", path, e)
        return path, "nicht lesbar", ""
    if verdict != FIX or when is None:
        return path, verdict, ""
    try:
        out = retry_io(
            lambda: write_capture_time(path, when, source=source, dry_run=not apply,
                                       overwrite=True, keep_prev=True),
            what=path,
        )
    except ExifWriteError as e:
        logger.warning("%s", e)
        return path, "Schreiben fehlgeschlagen", ""
    except Exception as e:
        logger.warning("%s: %s", path, e)
        return path, "Schreiben fehlgeschlagen", ""
    detail = f"{out.get('previous')} -> {out.get('new')} {out.get('offset') or ''}".strip()
    if out["written"]:
        return path, DONE, detail
    return path, (FIX if not apply else out["reason"]), detail


def run(roots: list[str], apply: bool = False, workers: int = 8,
        limit: int | None = None, sample: int = 3) -> Counter:
    tally: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    paths = []
    for path in _walk(roots):
        paths.append(path)
        if limit and len(paths) >= limit:
            break
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, verdict, detail in pool.map(lambda p: _process(p, apply), paths):
            tally[verdict] += 1
            if len(examples[verdict]) < sample:
                examples[verdict].append(f"{path}  {detail}".rstrip())

    logger.info("%s ueber %d Dateien (Ortszeit: %s):",
                "Angewandt" if apply else "Trockenlauf", len(paths), zone())
    for key, n in tally.most_common():
        logger.info("   %-34s %6d", key, n)
        for line in examples.get(key, []):
            logger.info("        %s", line)
    if not apply and tally.get(FIX):
        logger.info("Mit --apply werden diese %d Dateien geaendert.", tally[FIX])
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="+", help="Ordner oder Dateien")
    parser.add_argument("--apply", action="store_true",
                        help="Tatsaechlich schreiben. Ohne das nur zaehlen.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None,
                        help="Hoechstens so viele Dateien ansehen")
    parser.add_argument("--sample", type=int, default=3,
                        help="Beispiele je Befund")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run(args.roots, apply=args.apply, workers=args.workers,
        limit=args.limit, sample=args.sample)


if __name__ == "__main__":
    main()
