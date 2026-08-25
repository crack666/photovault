"""Text-Vektoren im ganzen Bestand neu bauen.

Der Vektor entsteht aus `grounding.grounded_document`. Aendert sich diese
Funktion -- oder kommen nachtraeglich Captions, Namen und Notizen hinzu --,
dann sind die Vektoren im Index aelter als die Regel, nach der sie entstanden
sind. Fuer einzelne Fotos gibt es `POST /api/photos/reembed`; fuer alle
siebzehntausend fehlte der Weg.

    python -m tools.reembed_all --dry-run     # was waere zu tun, wie lange
    python -m tools.reembed_all               # und los

**Es gibt kein Wiederaufsetzen.** Das erzeugte Dokument wird nicht im Payload
abgelegt, es ist also nicht feststellbar, welcher Vektor noch zur heutigen
Regel passt und welcher nicht. Der Lauf ist dafuer folgenlos wiederholbar --
zweimal einbetten ergibt denselben Vektor. Wer in Haeppchen arbeiten will,
nimmt `--prefix` fuer ein Verzeichnis oder `--limit` fuer eine Obergrenze.

**Der Lauf belegt Ollama.** Je Foto ein Embedding-Aufruf, gemessen ~130 ms.
Laeuft parallel ein Vision-Lauf, teilen sich beide die Grafikkarte -- dann
lieber warten oder in Haeppchen arbeiten.
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from api.qdrant_util import PHOTOS, client
from ingest.reembed import rebuild_text_vectors

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

#: Gemessen an diesem Bestand, je Foto.
SECONDS_PER_PHOTO = 0.13


def collect(qc, collection: str, prefix: str | None) -> list[str]:
    """Punkt-IDs sammeln, die neu eingebettet werden sollen."""
    ids: list[str] = []
    seen = 0
    offset = None
    while True:
        batch, offset = qc.scroll(
            collection_name=collection, limit=512, offset=offset,
            with_payload=["file_path"], with_vectors=False,
        )
        for point in batch:
            seen += 1
            path = str((point.payload or {}).get("file_path") or "")
            if prefix and not path.startswith(prefix):
                continue
            ids.append(str(point.id))
        if offset is None:
            break
    logger.info("%d von %d Fotos vorgemerkt", len(ids), seen)
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--dry-run", action="store_true", help="nur zaehlen und schaetzen")
    ap.add_argument("--limit", type=int, help="hoechstens so viele Fotos")
    ap.add_argument("--batch", type=int, default=64, help="Fotos je Schreibvorgang")
    ap.add_argument("--prefix", help="nur Dateipfade unter diesem Verzeichnis")
    ap.add_argument("--collection", default=PHOTOS)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    qc = client()
    ids = collect(qc, args.collection, args.prefix)
    if args.limit:
        ids = ids[: args.limit]

    if not ids:
        print("Nichts zu tun.")
        return

    minutes = len(ids) * SECONDS_PER_PHOTO / 60
    print(f"{len(ids)} Fotos, geschaetzt {minutes:.0f} min Ollama-Zeit.")
    if args.dry_run:
        print("Trockenlauf -- nichts geschrieben.")
        return

    total = {"updated": 0, "skipped": 0, "failed": 0}
    t0 = time.time()
    for start in range(0, len(ids), args.batch):
        chunk = ids[start : start + args.batch]
        stats = rebuild_text_vectors(
            qc, chunk, collection=args.collection, ollama_url=OLLAMA_URL
        )
        for key in total:
            total[key] += stats.get(key, 0)
        done = start + len(chunk)
        rate = done / max(time.time() - t0, 0.001)
        left = (len(ids) - done) / max(rate, 0.001) / 60
        print(
            f"\r  {done}/{len(ids)}  {rate:.1f}/s  noch {left:.0f} min"
            f"  neu {total['updated']}  fehlgeschlagen {total['failed']}",
            end="",
            flush=True,
        )
    print(
        f"\nFertig in {(time.time() - t0) / 60:.1f} min: "
        f"{total['updated']} neu, {total['skipped']} uebersprungen, "
        f"{total['failed']} fehlgeschlagen."
    )


if __name__ == "__main__":
    main()
