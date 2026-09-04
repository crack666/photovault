"""Fehlende clip-Vektoren nachziehen.

Ohne clip-Vektor ist ein Foto nicht auf der Karte und nicht in der
Aehnlichkeitssuche -- es existiert im Index, aber nicht im Atlas, und der
Atlas ist die Ansicht, um die es hier geht. Die Jobs-Seite zaehlt diese
Luecke; behoben werden konnte sie bisher nur durch einen vollen Neu-Ingest,
und der ueberspringt genau diese Fotos, weil sie schon indiziert sind.

Der Hinweis daneben lautete "Datei pruefen: meist beschaedigt oder nicht
lesbar". Nachgemessen an den 59 Faellen dieses Bestands war das falsch:

* 56 Dateien liegen zu 98-100 % vor. Es fehlen 0-73 Bytes am Ende -- die
  Endemarkierung und ein Rest der letzten Bildzeilen. In voller Auflaesung
  dekodierbar, alle mit heilem EXIF-Vorschaubild.
* 1 Datei liegt zu 75 % vor, ebenfalls dekodierbar.
* 2 sind wirklich Muell: 36 Byte, ein UUID-Text mit `.png` am Namen.

Der Grund war nicht die Datei, sondern der Zeitpunkt: sie wurden
aufgenommen, bevor `_load_image` abgeschnittene Bilder tolerierte. Genau
dieselbe Funktion benutzt dieser Lauf, deshalb genuegt er.

    python -m tools.backfill_clip --dry-run        # was zu tun ist
    python -m tools.backfill_clip                  # und los
    python -m tools.backfill_clip --prefix /mnt/photo/Fotos/Weihnachten
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from api.qdrant_util import PHOTOS, client
from ingest.identity import uid_of

logger = logging.getLogger(__name__)

BATCH = 32


def collect(qc, collection: str, prefix: str | None) -> list[tuple[str, str]]:
    """[(Punkt-ID, Dateipfad)] fuer Fotos ohne clip-Vektor.

    Gelesen wird mit `with_vectors=["clip"]` statt ueber einen Filter: eine
    fehlende *Vektor*-Komponente ist keine Payload-Eigenschaft, nach der man
    filtern koennte. Der Papierkorb bleibt drin -- ein Wartungslauf, der
    stillschweigend Daten ueberspringt, ist schlimmer als einer, der ein
    paar Punkte zuviel anfasst.
    """
    out, offset = [], None
    while True:
        batch, offset = qc.scroll(
            collection_name=collection, limit=512, offset=offset,
            with_payload=["file_path"], with_vectors=["clip"],
        )
        for p in batch:
            if (p.vector or {}).get("clip"):
                continue
            fp = (p.payload or {}).get("file_path") or ""
            if not fp or (prefix and not fp.startswith(prefix)):
                continue
            out.append((str(p.id), fp))
        if offset is None:
            return out


def klassifiziere(pfade: list[tuple[str, str]]) -> dict[str, list]:
    """Vor dem Lauf trennen: fehlt die Datei, ist sie Muell, oder geht es.

    Ein Trockenlauf, der nur "59 Fotos" sagt, hilft so wenig wie die Zeile,
    aus der er entstanden ist.
    """
    aus: dict[str, list] = {"fehlt": [], "winzig": [], "geht": []}
    for pid, fp in pfade:
        try:
            groesse = os.path.getsize(fp)
        except OSError:
            aus["fehlt"].append((pid, fp))
            continue
        # Unter einem Kilobyte kann kein JPEG stecken -- die beiden Faelle
        # hier waren 36 Byte UUID-Text mit .png am Namen.
        if groesse < 1024:
            aus["winzig"].append((pid, fp, groesse))
        else:
            aus["geht"].append((pid, fp))
    return aus


def run(qc, collection: str, aufgaben: list[tuple[str, str]], model_dir: str,
        threshold: float, batch: int = BATCH) -> dict:
    from qdrant_client.models import PointVectors

    from ingest.pipeline import _load_image
    from ingest.scene_tagger import SceneTagger

    tagger = SceneTagger(model_dir, threshold)
    fertig = uebersprungen = 0
    begonnen = time.time()

    for i in range(0, len(aufgaben), batch):
        stapel = aufgaben[i : i + batch]
        punkte, tags_je_id = [], {}
        for pid, fp in stapel:
            _raw, rgb, warn = _load_image(fp)
            if rgb is None:
                logger.warning("nicht dekodierbar, uebersprungen: %s (%s)", fp, warn)
                uebersprungen += 1
                continue
            try:
                sr = tagger.process_image(rgb)
            except Exception as e:
                logger.warning("clip fehlgeschlagen: %s (%s)", fp, e)
                uebersprungen += 1
                continue
            vec = sr.get("embedding")
            if not vec:
                logger.warning("kein Vektor zurueck: %s", fp)
                uebersprungen += 1
                continue
            punkte.append(PointVectors(id=pid, vector={"clip": vec}))
            if sr.get("tags"):
                tags_je_id[pid] = sr["tags"]

        if not punkte:
            continue
        qc.update_vectors(collection_name=collection, points=punkte, wait=True)
        # Szenen-Etiketten kommen aus demselben Durchgang. Sie separat zu
        # holen waere ein zweites Dekodieren derselben Datei.
        for pid, tags in tags_je_id.items():
            try:
                qc.set_payload(collection_name=collection,
                               payload={"scene_tags": tags}, points=[pid], wait=False)
            except Exception as e:
                logger.debug("scene_tags fuer %s nicht geschrieben: %s", pid, e)
        fertig += len(punkte)
        print(f"  {fertig}/{len(aufgaben)}", flush=True)

    return {"done": fertig, "skipped": uebersprungen,
            "seconds": round(time.time() - begonnen, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, was zu tun ist")
    ap.add_argument("--prefix", help="nur Dateipfade unter diesem Verzeichnis")
    ap.add_argument("--limit", type=int, help="hoechstens so viele Fotos")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--collection", default=PHOTOS)
    ap.add_argument("--model-dir",
                    default=os.environ.get("MODEL_DIR",
                                           str(Path.home() / ".cache" / "photovault-models")))
    ap.add_argument("--threshold", type=float, default=0.2)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    qc = client()
    offen = collect(qc, args.collection, args.prefix)
    if not offen:
        print("Kein Foto ohne clip-Vektor.")
        return 0

    gruppen = klassifiziere(offen)
    print(f"{len(offen)} Foto(s) ohne clip-Vektor:")
    print(f"  {len(gruppen['geht']):>5}  dekodierbar -- werden nachgezogen")
    if gruppen["winzig"]:
        print(f"  {len(gruppen['winzig']):>5}  unter 1 KB -- kein Bild darin:")
        for _pid, fp, groesse in gruppen["winzig"]:
            print(f"           {groesse:>6} B  {fp}")
    if gruppen["fehlt"]:
        print(f"  {len(gruppen['fehlt']):>5}  Datei nicht vorhanden:")
        for _pid, fp in gruppen["fehlt"][:20]:
            print(f"                   {fp}")

    aufgaben = gruppen["geht"]
    if args.limit is not None:
        aufgaben = aufgaben[: args.limit]
    if args.dry_run:
        print()
        print("Trockenlauf -- nichts geschrieben.")
        return 0
    if not aufgaben:
        print()
        print("Nichts nachzuziehen.")
        return 0

    print()
    ergebnis = run(qc, args.collection, aufgaben, args.model_dir, args.threshold,
                   batch=args.batch)
    print(f"{ergebnis['done']} nachgezogen, {ergebnis['skipped']} uebersprungen, "
          f"{ergebnis['seconds']} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
