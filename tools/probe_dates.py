"""Probe: EXIF-Tags einer Datei oder einer Index-Nacht.

Damit ein Mensch oder ein LLM denselben Vergleich anstellen kann wie in
`docs/dates.md` — Datei lesen, nicht den Index glauben, nichts schreiben.

    python -m tools.probe_dates --file /mnt/photo/Fotos/Album/x.jpg
    python -m tools.probe_dates --night 2009-01-20
    python -m tools.backfill_taken_at --preview
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from ingest.exif_extractor import exif_capture_stamp


def _decode(raw) -> str | None:
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", "ignore")
    text = str(raw).strip("\x00 ")
    return text or None


def inspect_file(path: str) -> dict:
    """306 / Original / Digitalisiert / gewählte Aufnahmezeit einer Datei."""
    from PIL import Image

    out = {
        "path": path,
        "file": Path(path).name,
        "chosen": None,
        "datetime_306": None,
        "original_36867": None,
        "digitized_36868": None,
        "make": None,
        "model": None,
        "error": None,
    }
    try:
        with Image.open(path) as img:
            exif = img.getexif() or {}
            out["chosen"] = exif_capture_stamp(exif)
            out["datetime_306"] = _decode(exif.get(306))
            try:
                ifd = exif.get_ifd(0x8769) or {}
            except Exception:
                ifd = {}
            out["original_36867"] = _decode(ifd.get(36867))
            out["digitized_36868"] = _decode(ifd.get(36868))
            out["make"] = _decode(exif.get(271))
            out["model"] = _decode(exif.get(272))
    except Exception as e:
        out["error"] = str(e)
    return out


def format_inspect(info: dict) -> str:
    if info.get("error"):
        return f"{info.get('path')}\n  Fehler: {info['error']}"
    lines = [
        info.get("path") or info.get("file"),
        f"  gewählt            {info.get('chosen') or '—'}",
        f"  DateTime (306)     {info.get('datetime_306') or '—'}",
        f"  DateTimeOriginal   {info.get('original_36867') or '—'}",
        f"  DateTimeDigitized  {info.get('digitized_36868') or '—'}",
    ]
    cam = " ".join(x for x in (info.get("make"), info.get("model")) if x)
    if cam:
        lines.append(f"  Kamera             {cam}")
    orig = (info.get("original_36867") or "")[:10].replace(":", "-")
    copied = (info.get("datetime_306") or "")[:10].replace(":", "-")
    if orig and copied and orig != copied:
        lines.append(f"  → 306 ist ein anderer Tag als Original (Import/Export?)")
    return "\n".join(lines)


def inspect_night(client, day: str, collection: str = "photos",
                  sample: int = 8) -> dict:
    """Fotos, deren Index-`taken_at` an diesem Kalendertag liegt."""
    if len(day) != 10:
        raise ValueError("Tag als YYYY-MM-DD")
    offset = None
    rows = []
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=256, offset=offset,
            with_payload=["folder_name", "taken_at", "date_source", "file_path"],
            with_vectors=False,
        )
        for p in batch:
            pl = p.payload or {}
            taken = pl.get("taken_at") or ""
            if taken.startswith(day):
                rows.append(pl)
        if offset is None:
            break
    folders = Counter(r.get("folder_name") or "?" for r in rows)
    samples = []
    for r in rows[:sample]:
        path = r.get("file_path")
        info = inspect_file(path) if path else {"error": "kein Pfad", "file": "?"}
        info["folder"] = r.get("folder_name")
        info["index_taken_at"] = r.get("taken_at")
        info["date_source"] = r.get("date_source")
        samples.append(info)
    return {"day": day, "count": len(rows), "folders": folders, "samples": samples}


def _print_night(report: dict) -> None:
    print(f"{report['count']} Fotos mit Index-Datum {report['day']}")
    print("Ordner:")
    for name, n in report["folders"].most_common():
        print(f"  {n:4d}  {name}")
    print("Stichprobe:")
    for info in report["samples"]:
        print(f"  [{info.get('folder')}] index {info.get('index_taken_at')} "
              f"src={info.get('date_source')}")
        print(format_inspect(info))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Eine Datei auf der Platte inspizieren")
    parser.add_argument("--night", metavar="YYYY-MM-DD",
                        help="Alle Index-Fotos dieses Tages, Tags von der Platte")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--sample", type=int, default=8)
    args = parser.parse_args()

    if args.file:
        print(format_inspect(inspect_file(args.file)))
        return
    if args.night:
        from qdrant_client import QdrantClient
        _print_night(inspect_night(
            QdrantClient(url=args.qdrant_url), args.night,
            collection=args.collection, sample=args.sample,
        ))
        return
    parser.error("Entweder --file oder --night angeben. "
                 "Bestandweit: python -m tools.backfill_taken_at --preview")


if __name__ == "__main__":
    main()
