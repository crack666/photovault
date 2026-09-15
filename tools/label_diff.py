"""Kontinentnamen: die gerechnete Karte gegen die heutigen Regeln.

Wer die Benennungsregeln anfasst -- Stoppwoerter, Mindestanteil,
Stamm-Dedupe --, sieht hier fuer *alle* Kontinente nebeneinander, was
sich aendert. Das ist die Messung, die eine Regel rechtfertigt oder
verwirft. An einem Beispiel getunt, macht eine Heuristik das naechste
Beispiel kaputt: die Mindestquote von 10 % haette `holzoberfläche`
entfernt, aber auch `abiball · silvester`, `funken`, `videospiel` --
und liess Fuellwoerter nachruecken. Das sah man nur im Vergleich ueber
alle 40.

    python -m tools.label_diff              # visuelle Kontinente
    python -m tools.label_diff --themen     # Schubladen der Themen-Anordnung

Gerechnet wird mit derselben Funktion, die der Kartenbau benutzt, auf den
Captions von heute. Weichen deshalb auch Kontinente ab, deren Regel sich
nicht geaendert hat, sind seit dem Kartenbau Captions dazugekommen -- die
Zeile "unveraendert" sagt, wie viele das betrifft.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from api.qdrant_util import PHOTOS, client
from tools import atlas_build as ab


def load(atlas: Path, themen: bool):
    d = json.loads(atlas.read_text(encoding="utf-8"))
    if themen:
        if not d.get("themes"):
            raise SystemExit("Die Karte hat keine Themen-Anordnung -- erst neu rechnen.")
        cl, clusters = d["themes"]["cl"], d["themes"]["clusters"]
    else:
        cl, clusters = d["cl"], d["clusters"]
    ids = d["ids"]
    q = client()
    cap: dict[str, tuple[str, list[str]]] = {}
    for i in range(0, len(ids), 512):
        for p in q.retrieve(collection_name=PHOTOS, ids=ids[i:i + 512],
                            with_payload=["caption_de", "scene_tags", "person_names"],
                            with_vectors=False):
            pl = p.payload or {}
            cap[str(p.id)] = (pl.get("caption_de") or "", pl.get("scene_tags") or [],
                              pl.get("person_names") or [])
    leer = ("", [], [])
    # Mit `person_names`: die Namensregel zaehlt bestaetigte Gesichter, und
    # ein Vergleich ohne sie zeigte Namen, die der Bau laengst streicht.
    meta = [{"caption": cap.get(i, leer)[0], "tags": cap.get(i, leer)[1],
             "person_names": cap.get(i, leer)[2]} for i in ids]
    return np.asarray(cl), meta, clusters


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atlas", type=Path, default=ab.OUT_DIR / "atlas.json")
    ap.add_argument("--themen", action="store_true", help="die Themen-Anordnung vergleichen")
    args = ap.parse_args(argv)

    labels, meta, alt = load(args.atlas, args.themen)
    k = len(alt)
    neu = ab.label_clusters(labels, meta, k)
    breite = 44
    print(f"{'#':>3} {'n':>5}  {'gerechnet':{breite}} {'heutige Regeln':{breite}}")
    gleich = 0
    for c in range(k):
        n = int((labels == c).sum())
        a = " · ".join(alt[c]["terms"])
        b = " · ".join(neu[c]["terms"])
        titel = alt[c].get("title")
        marke = "  " if a == b else "* "
        gleich += a == b
        zeile = f"{marke}{c:>2} {n:>5}  {a:{breite}} {b:{breite}}"
        print(zeile + (f"   [{titel}]" if titel and a != b else ""))
    print(f"\n{gleich} von {k} unveraendert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
