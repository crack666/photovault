"""CLIP-Etiketten aus Fotos entfernen, die eine Beschreibung haben.

Seit die Etiketten aus der Beschreibung gewinnen (`merge_tags`), gilt das
fuer jeden *neuen* Caption-Lauf. Im Bestand stehen die alten CLIP-Raten
aber weiter im Feld: ein Wundfoto traegt `radfahren, skifahren,
screenshot, hund, kinder` vor seinen sechs richtigen Etiketten und ist im
Atlas unter "skifahren" filterbar. Ein voller Caption-Lauf wuerde das
richten -- 14.500 Fotos durch das Vision-Modell, Stunden. Das hier ist der
Weg ohne Modell.

Auseinanderhalten lassen sich die Quellen nur an der Zeichenkette: die 44
CLIP-Begriffe sind bekannt, LLM-Etiketten sind freier Text. Wo beides
zusammenfaellt -- das LLM sagt auch mal "hund" --, darf nicht blind
geloescht werden. Deshalb bleibt ein CLIP-Begriff, wenn die Beschreibung
ihn stuetzt (sein Wortstamm darin vorkommt). Ein "hund" bei "Ein Hund
liegt auf der Wiese" bleibt; ein "skifahren" bei einer Wunde geht.

Fotos ohne Beschreibung werden nicht angefasst: fuer sie sind die
CLIP-Etiketten der einzige Rueckfall.

    python -m tools.prune_clip_tags              # Trockenlauf mit Zahlen
    python -m tools.prune_clip_tags --apply      # und schreiben
"""
from __future__ import annotations

import argparse
import collections
import logging
import sys

from api.qdrant_util import PHOTOS, client
from ingest.scene_tagger import SCENE_CONCEPTS

logger = logging.getLogger(__name__)


def falte(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return s


GEFALTET = {falte(x) for x in SCENE_CONCEPTS}


def gestuetzt(tag: str, caption_gefaltet: str) -> bool:
    """Kommt der Begriff -- oder sein Stamm -- in der Beschreibung vor?

    `radfahren` steht in einer Beschreibung als "Fahrrad" oder "radelt";
    der Stamm ohne Verbendung (`radfahr`) faengt beides nicht, aber "rad"
    waere zu kurz und traefe "Rad" in "Grad". Vier Zeichen Stamm sind die
    Untergrenze: `hund` in "Hundeleine", `party` in "Partyhut".
    """
    t = falte(tag)
    if t in caption_gefaltet:
        return True
    stamm = t[:-2] if len(t) > 6 and t.endswith(("en", "er", "es")) else t
    return len(stamm) >= 4 and stamm in caption_gefaltet


def plan(qc, collection: str = PHOTOS) -> tuple[list[tuple[str, list[str]]], dict]:
    """[(Punkt-ID, neue Etiketten)] fuer jedes Foto, an dem sich etwas aendert."""
    aenderungen: list[tuple[str, list[str]]] = []
    zus = {"fotos": 0, "mit_caption": 0, "weg": collections.Counter(),
           "behalten": collections.Counter(), "leer_danach": 0}
    off = None
    while True:
        batch, off = qc.scroll(collection_name=collection, limit=1024, offset=off,
                               with_payload=["scene_tags", "caption_de"], with_vectors=False)
        for p in batch:
            pl = p.payload or {}
            zus["fotos"] += 1
            tags = list(pl.get("scene_tags") or [])
            cap = pl.get("caption_de") or ""
            if not cap or not tags:
                continue
            zus["mit_caption"] += 1
            cg = falte(cap)
            bleibt = []
            for t in tags:
                if falte(t) not in GEFALTET:
                    bleibt.append(t)                 # freier LLM-Begriff
                elif gestuetzt(t, cg):
                    zus["behalten"][t] += 1
                    bleibt.append(t)
                else:
                    zus["weg"][t] += 1
            if bleibt != tags:
                aenderungen.append((str(p.id), bleibt))
                if not bleibt:
                    zus["leer_danach"] += 1
        if off is None:
            break
    return aenderungen, zus


def apply(qc, aenderungen, collection: str = PHOTOS, batch: int = 256) -> int:
    n = 0
    for i in range(0, len(aenderungen), batch):
        for pid, tags in aenderungen[i:i + batch]:
            qc.set_payload(collection_name=collection, payload={"scene_tags": tags},
                           points=[pid], wait=False)
            n += 1
        print(f"  {n}/{len(aenderungen)}", flush=True)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="schreiben statt nur zeigen")
    ap.add_argument("--collection", default=PHOTOS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    qc = client()
    aenderungen, zus = plan(qc, args.collection)
    weg, beh = zus["weg"], zus["behalten"]
    print(f"{zus['fotos']} Fotos, {zus['mit_caption']} mit Beschreibung und Etiketten.")
    print(f"{len(aenderungen)} Fotos aendern sich; {sum(weg.values())} CLIP-Etiketten gehen, "
          f"{sum(beh.values())} bleiben, weil die Beschreibung sie stuetzt.")
    print(f"{zus['leer_danach']} Fotos haetten danach keine Etiketten "
          f"(die Beschreibung nannte selbst keine).")
    print("\nhaeufigste entfernte Begriffe      weg  gestuetzt behalten")
    for t, n in weg.most_common(12):
        print(f"  {t:22} {n:>7} {beh.get(t, 0):>10}")
    if not args.apply:
        print("\nTrockenlauf -- nichts geschrieben. --apply schreibt.")
        return 0
    n = apply(qc, aenderungen, args.collection)
    print(f"{n} Fotos geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
