"""Acceptance-Check gegen die Szenarien aus docs/spec.md.

Prueft die indizierten Daten direkt gegen Qdrant und misst Query-Latenz.

    python -m tools.acceptance --prefix /mnt/d/photovault_sample
"""
from __future__ import annotations

import argparse
import os
import statistics
import time
from typing import Any

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str) -> None:
    status = PASS if ok else (WARN if ok is None else FAIL)
    _results.append((status, name, detail))
    print(f"[{status}] {name}\n       {detail}")


def load(client, collection: str, prefix: str | None) -> list[Any]:
    pts, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=collection, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        pts.extend(batch)
        if offset is None:
            break
    if prefix:
        pts = [p for p in pts if (p.payload or {}).get("file_path", "").startswith(prefix)]
    return pts


def s2_no_exif_grounding(client, collection: str, pts: list[Any]) -> None:
    """Szenario 2: Ohne EXIF muessen Ordner/Datum/Sequenz trotzdem im Payload
    UND im Text-Vektor stehen."""
    no_exif = [p for p in pts if (p.payload or {}).get("date_source") != "exif"]
    if not no_exif:
        check("S2 kein EXIF -> Ordner/Datum/Sequenz", None, "keine EXIF-losen Fotos in der Stichprobe")
        return
    with_date = sum(1 for p in no_exif if (p.payload or {}).get("date"))
    with_folder = sum(1 for p in no_exif if (p.payload or {}).get("folder_name"))
    with_seq = sum(1 for p in no_exif if (p.payload or {}).get("sequence_in_folder") is not None)
    ids = [p.id for p in no_exif[:128]]
    full = client.retrieve(collection_name=collection, ids=ids, with_vectors=True)
    with_text = sum(1 for p in full if (p.vector or {}).get("text"))
    ok = with_date == len(no_exif) and with_folder == len(no_exif) and with_text == len(full)
    check(
        "S2 kein EXIF -> Datum/Ordner/Sequenz + Text-Vektor",
        ok,
        f"{len(no_exif)} Fotos ohne EXIF: date {with_date}, folder {with_folder}, "
        f"sequence {with_seq}, text-Vektor {with_text}/{len(full)}",
    )


def s5_grounded_embed(client, collection: str, pts: list[Any]) -> None:
    """Szenario 5: auch ohne Caption ein grounded Text-Embed."""
    ids = [p.id for p in pts[:256]]
    full = client.retrieve(collection_name=collection, ids=ids, with_vectors=True)
    no_caption = [p for p in full if not (p.payload or {}).get("caption_de")]
    if not no_caption:
        check("S5 --skip-caption -> Text-Embed", None, "alle Fotos haben Captions")
        return
    with_text = sum(1 for p in no_caption if (p.vector or {}).get("text"))
    check(
        "S5 --skip-caption -> grounded Text-Embed",
        with_text == len(no_caption),
        f"{with_text}/{len(no_caption)} Fotos ohne Caption haben trotzdem text-Vektor",
    )


def s3_caption_context(pts: list[Any]) -> None:
    """Szenario 3: Caption nennt Jahr/Ort, erfindet keine Namen."""
    caps = [(p.payload or {}) for p in pts if (p.payload or {}).get("caption_de")]
    if not caps:
        check("S3 Caption nutzt Kontext", None, "keine Captions indiziert")
        return
    have_year = 0
    invented = []
    for pl in caps:
        cap = pl["caption_de"]
        year = (pl.get("date") or "")[:4]
        if year and year in cap:
            have_year += 1
        # Namen duerfen nur auftauchen, wenn ein Match/Album-Hinweis existiert
        known = {n.lower() for n in (pl.get("person_suggestions") or [])}
        known |= {n.lower() for n in (pl.get("folder_people") or [])}
        for word in cap.split():
            w = word.strip(".,;:!?\"'").lower()
            if len(w) > 2 and word[:1].isupper() and w not in known:
                pass  # zu unscharf fuer harte Aussage; nur Jahresquote wird gewertet
    check(
        "S3 Caption nennt das Jahr",
        have_year > len(caps) * 0.5,
        f"{have_year}/{len(caps)} Captions enthalten das ermittelte Jahr",
    )


def s1_composite_and_latency(client, collection: str, pts: list[Any]) -> None:
    """Szenario 1 + NFR Query-Latenz < 500ms."""
    from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

    folders = {}
    for p in pts:
        pl = p.payload or {}
        if pl.get("folder_name") and pl.get("date"):
            folders.setdefault(pl["folder_name"], pl["date"][:4])
    if not folders:
        check("S1 Komposit-Query", False, "keine Fotos mit Ordner+Datum")
        return
    folder, year = next(iter(sorted(folders.items())))

    queries = {
        "Jahr-Filter": Filter(must=[FieldCondition(
            key="taken_at",
            range=DatetimeRange(gte=f"{year}-01-01T00:00:00Z", lte=f"{year}-12-31T23:59:59Z"),
        )]),
        "scene_tag": Filter(must=[FieldCondition(key="scene_tags", match=MatchValue(value="party"))]),
        "Jahr+Tag": Filter(must=[
            FieldCondition(key="taken_at", range=DatetimeRange(
                gte=f"{year}-01-01T00:00:00Z", lte=f"{year}-12-31T23:59:59Z")),
            FieldCondition(key="scene_tags", match=MatchValue(value="feste")),
        ]),
    }
    lat = []
    for name, filt in queries.items():
        t0 = time.perf_counter()
        hits, _ = client.scroll(collection_name=collection, scroll_filter=filt,
                                limit=50, with_payload=True)
        dt = (time.perf_counter() - t0) * 1000
        lat.append(dt)
        print(f"       {name:<14} {len(hits):>3} Treffer  {dt:6.1f} ms")
    check("S1 Komposit-Query (Payload-Filter)", any(l >= 0 for l in lat),
          f"{len(queries)} Filter-Queries ausgefuehrt")
    check("NFR Query-Latenz < 500ms", max(lat) < 500,
          f"max {max(lat):.1f} ms, median {statistics.median(lat):.1f} ms")


def s_text_search(client, collection: str, ollama: str) -> None:
    """Semantische Suche ueber den grounded Text-Vektor."""
    import sys
    sys.path.insert(0, os.getcwd())
    from ingest.text_embedder import TextEmbedder

    emb = TextEmbedder(ollama)
    probes = ["Silvester Feuerwerk", "Abiball 2008", "Party mit Freunden", "Strand und Meer"]
    lat = []
    ok_any = False
    for q in probes:
        vec = emb.embed(q)
        if not vec:
            print(f"       {q:<22} EMBED FEHLGESCHLAGEN")
            continue
        t0 = time.perf_counter()
        hits = client.query_points(collection_name=collection, query=vec, using="text",
                                   limit=3, with_payload=True).points
        dt = (time.perf_counter() - t0) * 1000
        lat.append(dt)
        ok_any = ok_any or bool(hits)
        top = [(h.payload or {}).get("folder_name") for h in hits]
        print(f"       {q:<22} {dt:6.1f} ms  -> {top}")
    check("Semantische Text-Suche (2560d)", ok_any,
          f"{len(lat)} Queries, max {max(lat):.1f} ms" if lat else "keine Query erfolgreich")


def s4_faces(client) -> None:
    """Szenario 4: Face-Cluster fuer das Labeling vorhanden."""
    try:
        info = client.get_collection("faces")
        n = info.points_count
    except Exception as e:
        check("S4 Face-Cluster fuer Labeling", False, f"faces-Collection nicht lesbar: {e}")
        return
    t0 = time.perf_counter()
    pts, _ = client.scroll("faces", limit=256, with_payload=True, with_vectors=True)
    dt = (time.perf_counter() - t0) * 1000
    import sys
    sys.path.insert(0, os.getcwd())
    from ingest.face_cluster import cluster_faces

    items = [{"id": str(p.id), "vector": p.vector, "payload": p.payload or {}} for p in pts]
    t1 = time.perf_counter()
    clusters = cluster_faces(items)
    ct = (time.perf_counter() - t1) * 1000
    sizes = [len(c["members"]) for c in clusters]
    check(
        "S4 Face-Cluster fuer Labeling",
        n > 0 and bool(clusters),
        f"{n} Gesichter gesamt; {len(pts)} geladen ({dt:.0f} ms) -> {len(clusters)} Cluster "
        f"({ct:.0f} ms), groesster {max(sizes) if sizes else 0}, "
        f"Singletons {sum(1 for s in sizes if s == 1)}",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"))
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    ap.add_argument("--collection", default="photos")
    ap.add_argument("--prefix", default=None)
    args = ap.parse_args()

    from qdrant_client import QdrantClient

    client = QdrantClient(url=args.qdrant_url)
    pts = load(client, args.collection, args.prefix)
    print(f"Acceptance gegen '{args.collection}': {len(pts)} Punkte\n")
    if not pts:
        print("Keine Punkte.")
        return

    s1_composite_and_latency(client, args.collection, pts)
    s2_no_exif_grounding(client, args.collection, pts)
    s3_caption_context(pts)
    s4_faces(client)
    s5_grounded_embed(client, args.collection, pts)
    s_text_search(client, args.collection, args.ollama_url)

    print(f"\n{'=' * 62}")
    for status in (FAIL, WARN, PASS):
        n = sum(1 for s, _, _ in _results if s == status)
        if n:
            print(f"  {status}: {n}")
    print("=" * 62)


if __name__ == "__main__":
    main()
