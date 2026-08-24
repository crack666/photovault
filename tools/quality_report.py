"""Qualitaets-Report ueber eine indizierte Foto-Collection.

Beantwortet: Wie viel ist echt belegt (EXIF), wie viel geraten? Wo fehlen
Gesichter, Tags, Captions? Widersprechen sich Datum und Ordnername?

    python -m tools.quality_report --prefix /mnt/d/photovault_sample
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import statistics
from typing import Any

RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")


def scroll_all(client, collection: str, prefix: str | None) -> list[Any]:
    points: list[Any] = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    if prefix:
        points = [p for p in points if (p.payload or {}).get("file_path", "").startswith(prefix)]
    return points


def pct(n: int, total: int) -> str:
    return f"{n:>5}/{total} ({n / total * 100:5.1f}%)" if total else f"{n:>5}/0"


def section(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def report_dates(pts: list[Any]) -> None:
    section("DATUM")
    n = len(pts)
    src = collections.Counter((p.payload or {}).get("date_source") for p in pts)
    for k, v in src.most_common():
        print(f"  date_source {str(k):<12} {pct(v, n)}")
    missing = sum(1 for p in pts if not (p.payload or {}).get("date"))
    print(f"  ohne Datum               {pct(missing, n)}")
    no_taken_at = sum(1 for p in pts if not (p.payload or {}).get("taken_at"))
    print(f"  ohne taken_at (Filter!)  {pct(no_taken_at, n)}")

    years = collections.Counter(
        (p.payload or {}).get("date", "")[:4] for p in pts if (p.payload or {}).get("date")
    )
    print(f"  Jahre: {dict(sorted(years.items()))}")

    # Widerspruch: Jahr im Ordnernamen vs. ermitteltes Jahr
    conflicts = []
    for p in pts:
        pl = p.payload or {}
        folder = pl.get("folder_name") or ""
        date = pl.get("date") or ""
        m = RE_YEAR.search(folder)
        if m and date[:4] and m.group(1) != date[:4]:
            conflicts.append((folder, date, pl.get("date_source"), pl.get("file_path", "")[-40:]))
    print(f"  Ordnerjahr != Datumsjahr {pct(len(conflicts), n)}")
    for c in conflicts[:8]:
        print(f"      {c[0]:<32} date={c[1]} src={c[2]}")
    if len(conflicts) > 8:
        print(f"      ... und {len(conflicts) - 8} weitere")


def report_faces(pts: list[Any]) -> None:
    section("GESICHTER")
    n = len(pts)
    counts = [(p.payload or {}).get("face_count") or 0 for p in pts]
    total_faces = sum(counts)
    zero = sum(1 for c in counts if c == 0)
    print(f"  Gesichter gesamt         {total_faces}")
    print(f"  Fotos ohne Gesicht       {pct(zero, n)}")
    print(f"  Fotos mit >=1 Gesicht    {pct(n - zero, n)}")
    if counts:
        print(f"  pro Foto: mean {statistics.mean(counts):.2f}  median {statistics.median(counts)}  max {max(counts)}")
    dist = collections.Counter(min(c, 6) for c in counts)
    print("  Verteilung: " + "  ".join(f"{k}{'+' if k == 6 else ''}:{v}" for k, v in sorted(dist.items())))

    by_folder = collections.defaultdict(lambda: [0, 0])
    for p in pts:
        pl = p.payload or {}
        f = pl.get("folder_name") or "(root)"
        by_folder[f][0] += pl.get("face_count") or 0
        by_folder[f][1] += 1
    print("  je Ordner (Gesichter/Fotos):")
    for f, (faces, photos) in sorted(by_folder.items(), key=lambda x: -x[1][0]):
        print(f"      {f:<34} {faces:>4} / {photos:<4} = {faces / photos:5.2f}")


def report_scene(pts: list[Any]) -> None:
    section("CLIP / SZENEN-TAGS")
    n = len(pts)
    tags = collections.Counter()
    empty = 0
    per_photo = []
    for p in pts:
        t = (p.payload or {}).get("scene_tags") or []
        per_photo.append(len(t))
        if not t:
            empty += 1
        for x in t:
            tags[x] += 1
    print(f"  Fotos ohne Tags          {pct(empty, n)}")
    if per_photo:
        print(f"  Tags/Foto: mean {statistics.mean(per_photo):.1f}  max {max(per_photo)}")
    print(f"  distinct Tags            {len(tags)}")
    print("  Top 20:")
    for t, c in tags.most_common(20):
        print(f"      {t:<20} {c:>4}  ({c / n * 100:4.1f}% der Fotos)")


def report_caption(pts: list[Any]) -> None:
    section("CAPTIONS")
    n = len(pts)
    caps = [(p.payload or {}).get("caption_de") for p in pts]
    have = [c for c in caps if c]
    print(f"  mit Caption              {pct(len(have), n)}")
    if not have:
        print("  (keine Captions indiziert - Vision uebersprungen)")
        return
    lens = [len(c) for c in have]
    print(f"  Laenge: mean {statistics.mean(lens):.0f}  median {statistics.median(lens):.0f}  min {min(lens)}  max {max(lens)}")
    # Heuristische Halluzinations-/Qualitaetsindikatoren
    generic = sum(1 for c in have if re.search(r"\b(ein|eine) (Person|Gruppe|Bild|Foto)\b", c))
    english = sum(1 for c in have if re.search(r"\b(the|image|shows|people|photo)\b", c, re.I))
    print(f"  sehr generisch           {pct(generic, len(have))}")
    print(f"  enthaelt Englisch        {pct(english, len(have))}")
    dupes = collections.Counter(have)
    rep = [(c, k) for c, k in dupes.most_common(3) if k > 1]
    print(f"  identische Captions      {sum(k for _, k in rep)}")
    for c, k in rep:
        print(f"      {k}x  {c[:70]}")
    print("  Beispiele:")
    for p in pts:
        c = (p.payload or {}).get("caption_de")
        if c:
            pl = p.payload or {}
            print(f"      [{pl.get('folder_name')}] faces={pl.get('face_count')} date={pl.get('date')}")
            print(f"        {c[:160]}")


def report_completeness(pts: list[Any], client, collection: str) -> None:
    section("VOLLSTAENDIGKEIT / VEKTOREN")
    n = len(pts)
    for field in ("file_path", "folder_name", "date", "taken_at", "location", "sequence_in_folder"):
        have = sum(1 for p in pts if (p.payload or {}).get(field) is not None)
        print(f"  {field:<22} {pct(have, n)}")
    ids = [p.id for p in pts[:256]]
    if ids:
        full = client.retrieve(collection_name=collection, ids=ids, with_vectors=True)
        vec_have = collections.Counter()
        for p in full:
            v = p.vector or {}
            if isinstance(v, dict):
                for name, val in v.items():
                    if val:
                        vec_have[name] += 1
        print(f"  -- Vektoren (Stichprobe {len(full)}) --")
        for name in ("face", "clip", "text"):
            print(f"  {name:<22} {pct(vec_have.get(name, 0), len(full))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"))
    ap.add_argument("--collection", default="photos")
    ap.add_argument("--prefix", default=None, help="Nur file_paths mit diesem Praefix")
    args = ap.parse_args()

    from qdrant_client import QdrantClient

    client = QdrantClient(url=args.qdrant_url)
    pts = scroll_all(client, args.collection, args.prefix)
    print(f"Collection '{args.collection}'  Punkte im Report: {len(pts)}")
    if args.prefix:
        print(f"Filter: file_path startswith {args.prefix!r}")
    if not pts:
        print("Keine Punkte - nichts auszuwerten.")
        return

    folders = collections.Counter((p.payload or {}).get("folder_name") for p in pts)
    print(f"Ordner: {len(folders)}")
    for f, c in folders.most_common():
        print(f"  {str(f):<36} {c:>4}")

    report_dates(pts)
    report_faces(pts)
    report_scene(pts)
    report_caption(pts)
    report_completeness(pts, client, args.collection)


if __name__ == "__main__":
    main()
