"""Stufe 2 der Identitäts-Umstellung: `content_sha256` nachtragen.

Neue Fotos bekommen den Hash beim Ingest -- dort kostet er 0,9 ms, weil die
Datei nach dem Dekodieren im Seiten-Cache liegt. Für die 14.593 bereits
indizierten muss er einmal nachgeholt werden, und da ist er kalt: gemessen
35,8 ms je Datei, also rund neun Minuten für den Bestand.

Wozu:

*Cache-Schlüssel* (Stufe 3). Gleiche Bytes heißen gleiche Kachel. Ein
Verschieben macht dann nichts ungültig, und bitidentische Dateien teilen
sich eine -- statt 14.858 Waisen zu hinterlassen.

*Wiedererkennen* (Stufe 4). Eine von außen verschobene Datei ist dieselbe
Datei; ihr Inhalt sagt das, ihr Pfad nicht.

Der Lauf ist unterbrechbar: was schon einen Hash hat, wird übersprungen.
Fehlende Dateien werden gezählt und benannt, nicht stillschweigend
übergangen -- eine Datei, die nicht da ist, ist ein Befund.

    python -m tools.backfill_hash --check
    python -m tools.backfill_hash
    python -m tools.backfill_hash --limit 500
"""
from __future__ import annotations

import argparse
import collections
import logging
import sys
import time
from pathlib import Path

from api.qdrant_util import PHOTOS, client
from ingest.identity import content_hash

logger = logging.getLogger(__name__)
BATCH = 256


def load(q) -> list[tuple[str, str, str]]:
    """(Punkt-ID, Pfad, vorhandener Hash) für alle Fotos."""
    out, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, limit=512, offset=offset,
            with_payload=["file_path", "content_sha256"], with_vectors=False,
        )
        for p in batch:
            pl = p.payload or {}
            out.append((str(p.id), pl.get("file_path") or "", pl.get("content_sha256") or ""))
        if offset is None:
            return out


def ensure_index(q, dry_run: bool) -> str:
    try:
        info = q.get_collection(PHOTOS)
        vorhanden = "content_sha256" in (getattr(info, "payload_schema", None) or {})
    except Exception as e:
        return f"Sammlung nicht lesbar: {e}"
    if vorhanden:
        return "war schon da"
    if dry_run:
        return "fehlt noch"
    try:
        q.create_payload_index(collection_name=PHOTOS, field_name="content_sha256",
                               field_schema="keyword", wait=True)
        return "angelegt"
    except Exception as e:
        return f"FEHLGESCHLAGEN: {type(e).__name__}: {e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="nur zählen, nichts schreiben")
    ap.add_argument("--limit", type=int, default=0, help="höchstens so viele hashen")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    q = client()
    rows = load(q)
    if not rows:
        print("Keine Fotos im Index.")
        return 1

    offen = [(pid, p) for pid, p, h in rows if p and not h]
    print(f"{len(rows)} Fotos")
    print(f"  schon gehasht : {sum(1 for _, _, h in rows if h)}")
    print(f"  offen         : {len(offen)}")
    print(f"  ohne Pfad     : {sum(1 for _, p, _ in rows if not p)}")
    print(f"  Index         : {ensure_index(q, args.check)}")
    if offen:
        # Bei 35,8 ms je Datei kalt -- die Zahl gehoert vor den Lauf, nicht
        # danach.
        print(f"  geschaetzt    : {len(offen) * 0.0358 / 60:.1f} Minuten")

    if args.check:
        print("\n--check: nichts geschrieben.")
        return 0
    if not offen:
        print("\nNichts zu tun.")
        return 0
    if args.limit:
        offen = offen[:args.limit]

    t0 = time.time()
    fertig, fehlt, puffer = 0, [], []
    for i, (pid, path) in enumerate(offen, 1):
        h = content_hash(path)
        if h is None:
            fehlt.append(path)
        else:
            puffer.append((pid, h))
            fertig += 1
        if len(puffer) >= BATCH or i == len(offen):
            for p_id, p_h in puffer:
                q.set_payload(collection_name=PHOTOS, payload={"content_sha256": p_h},
                              points=[p_id], wait=False)
            puffer.clear()
            rate = i / max(time.time() - t0, 0.001)
            rest = (len(offen) - i) / max(rate, 0.001) / 60
            print(f"  {i}/{len(offen)}  ({rate:.0f}/s, noch {rest:.1f} min)")

    # Die Schreibvorgaenge liefen mit wait=False. Einmal am Ende auf die
    # Sammlung zugreifen genuegt, damit Qdrant sie festgeschrieben hat --
    # sonst meldet ein direkt folgender --check noch alte Zahlen.
    try:
        q.get_collection(PHOTOS)
    except Exception as e:
        logger.debug("Abschluss-Abfrage: %s", e)

    print(f"\n{fertig} gehasht in {(time.time()-t0)/60:.1f} Minuten.")
    if fehlt:
        print(f"{len(fehlt)} Dateien nicht lesbar -- das ist ein Befund, kein Rauschen:")
        for p in fehlt[:10]:
            print(f"   {p}")
        if len(fehlt) > 10:
            print(f"   … und {len(fehlt) - 10} weitere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
