"""Nachsehen, was in den angeblich kaputten Dateien noch steckt.

Rein lesend. Beantwortet drei Fragen je Datei:

*Ist sie abgeschnitten oder in der Mitte zerstoert?* Ein abgeschnittenes
JPEG dekodiert bis zu einer Zeile und bricht dann ab -- die oberen
Bildzeilen sind heil. Zerstoerte Huffman-Daten liefern Muell ab der
Stelle.

*Wieviel davon ist heil?* Pillow mit LOAD_TRUNCATED_IMAGES liefert das
Bild; die unteren Zeilen sind dann grau. Der Anteil sagt, ob sich Retten
lohnt.

*Steckt ein eingebettetes Vorschaubild im EXIF?* Kameras legen dort ein
kleines JPEG ab, das vor den Bilddaten steht -- bei einem abgebrochenen
Transfer ist es deshalb meist vollstaendig.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image, ImageFile


#: Wie weit vorn das eingebettete Vorschaubild stecken kann. Der APP1-Block
#: ist auf 64 KB begrenzt; alles darueber ist schon Bilddaten, und ein
#: Zufallstreffer auf ein SOI-Muster darin waere kein Vorschaubild.
APP1_MAX = 70000


def exif_thumb(b: bytes) -> tuple[int, tuple[int, int] | None]:
    """(Bytes, Groesse) des eingebetteten Vorschaubilds -- oder (0, None).

    Das ist die Rueckfall-Rettung: Kameras legen im EXIF ein kleines JPEG
    ab, und weil es *vor* den Bilddaten steht, ist es bei einem
    abgebrochenen Transfer meist vollstaendig -- auch wenn vom Hauptbild
    nichts mehr zu holen ist.
    """
    # Das eingebettete JPEG steht in der IFD1. Pillow gibt es nicht direkt
    # heraus, also im APP1-Block nach dem naechsten SOI suchen.
    app1_ende = min(len(b), APP1_MAX)
    start = b.find(b"\xff\xd8\xff", 2, app1_ende)
    if start < 0:
        return 0, None
    ende = b.find(b"\xff\xd9", start)
    if ende < 0:
        return 0, None
    daten = b[start:ende + 2]
    try:
        t = Image.open(io.BytesIO(daten))
        t.load()
        return len(daten), t.size
    except Exception:
        return 0, None


def grauanteil(im: Image.Image) -> float:
    """Anteil der Bildhoehe, der noch echte Daten traegt.

    Pillow fuellt nicht dekodierte Zeilen mit dem Initialwert des Puffers.
    Wir suchen von unten die erste Zeile, die nicht durchgehend derselbe
    Wert ist.
    """
    g = im.convert("L")
    w, h = g.size
    px = g.load()
    schritt = max(1, w // 64)
    for y in range(h - 1, -1, -1):
        reihe = {px[x, y] for x in range(0, w, schritt)}
        if len(reihe) > 2:
            return (y + 1) / h
    return 0.0


def analyse(p: Path) -> dict:
    b = p.read_bytes()
    out = {"name": p.name, "bytes": len(b), "ffd9": b.rfind(b"\xff\xd9")}

    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        im = Image.open(io.BytesIO(b))
        im.load()
        out["streng"] = f"ok {im.size[0]}x{im.size[1]}"
    except Exception as e:
        out["streng"] = f"{type(e).__name__}: {e}"[:50]

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        im = Image.open(io.BytesIO(b))
        im.load()
        out["lose"] = f"{im.size[0]}x{im.size[1]}"
        out["heil"] = round(grauanteil(im) * 100)
    except Exception as e:
        out["lose"] = f"{type(e).__name__}"
        out["heil"] = 0

    n, groesse = exif_thumb(b)
    out["thumb"] = f"{groesse[0]}x{groesse[1]} ({n} B)" if groesse else "-"
    return out


def main(argv):
    ziel = Path(argv[1])
    dateien = sorted(p for p in ziel.iterdir()
                     if p.suffix.lower() in {".jpg", ".jpeg"}) if ziel.is_dir() else [ziel]
    print(f"{len(dateien)} Datei(en) in {ziel}")
    print()
    print(f"{'Datei':18}{'KB':>7}{'ffd9':>10}{'heil%':>7}  {'Vorschau':<18} streng")
    zus = {"ganz": 0, "teil": 0, "nichts": 0, "mit_thumb": 0}
    for p in dateien:
        r = analyse(p)
        print(f"{r['name']:18}{r['bytes']//1024:>7}{r['ffd9']:>10}{r['heil']:>7}  "
              f"{r['thumb']:<18} {r['streng']}")
        if r["streng"].startswith("ok"):
            zus["ganz"] += 1
        elif r["heil"] > 0:
            zus["teil"] += 1
        else:
            zus["nichts"] += 1
        if r["thumb"] != "-":
            zus["mit_thumb"] += 1
    print()
    print(f"vollstaendig: {zus['ganz']}   teilweise: {zus['teil']}   "
          f"nichts: {zus['nichts']}   mit eingebetteter Vorschau: {zus['mit_thumb']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
