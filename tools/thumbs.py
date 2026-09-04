"""Den Vorschaubild-Cache ansehen, aufraeumen, umziehen, fuellen.

Der Cache ist der Grund, warum der Atlas schnell ist: nach dem ersten Zoom
werden die Originale nicht mehr geholt. 14.593 Fotos belegen darin rund
295 MB gegen 17,5 GB Originale -- Faktor 61.

Drei Dinge, die man daran tun will:

*Nachsehen.* Wo liegt er, wie gross ist er, wieviel davon ist Muell.

*Aufraeumen.* Der Schluessel ist der Dateipfad (`sha256(pfad)`). Wird ein
Foto verschoben oder geloescht, passt der Schluessel nicht mehr -- die alten
Kacheln bleiben liegen. Gemessen: 14.855 Waisen, 94,5 MB.

*Fuellen.* Vor einem Lauf soll dastehen, was er kostet, nicht danach.

    python -m tools.thumbs                  nachsehen
    python -m tools.thumbs --prune          Waisen loeschen
    python -m tools.thumbs --move           vom alten Ort herueberschieben
    python -m tools.thumbs --warm           fehlende erzeugen
    python -m tools.thumbs --warm --sizes 160,320
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path

from api.qdrant_util import PHOTOS, client
from api.thumbs import CACHE_DIR, LEGACY_CACHE, get_thumb

logger = logging.getLogger(__name__)

#: Diese beiden braucht die Karte. 640 und 1280 entstehen bei Bedarf --
#: 1280 fuer alle waere 2,4 GB fuer eine Ansicht, die man je Foto einmal
#: aufmacht.
ATLAS_SIZES = (160, 320)

MB = 1048576


def indexed_paths(q) -> list[str]:
    out, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, limit=1024,
            with_payload=["file_path"], with_vectors=False, offset=offset,
        )
        out.extend((p.payload or {}).get("file_path") or "" for p in batch)
        if offset is None:
            return [p for p in out if p]


def digest(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def scan(base: Path, sizes) -> dict[int, dict[str, tuple[Path, int]]]:
    """{groesse: {digest: (Pfad, Bytes)}} -- was im Cache liegt."""
    out = {s: {} for s in sizes}
    if not base.is_dir():
        return out
    for f in base.rglob("*.jpg"):
        name = f.name
        if "_" not in name:
            continue
        dig, _, rest = name.partition("_")
        try:
            size = int(rest.removesuffix(".jpg"))
        except ValueError:
            continue
        if size not in out:
            continue
        try:
            out[size][dig] = (f, f.stat().st_size)
        except OSError:
            pass
    return out


def report(sizes) -> dict:
    q = client()
    paths = indexed_paths(q)
    soll = {digest(p) for p in paths}
    da = scan(CACHE_DIR, sizes)
    alt = scan(LEGACY_CACHE, sizes) if LEGACY_CACHE != CACHE_DIR else {s: {} for s in sizes}

    print(f"Cache:      {CACHE_DIR}")
    if any(alt.values()):
        print(f"alter Ort:  {LEGACY_CACHE}   (wird beim Lesen noch benutzt)")
    print(f"Fotos im Index: {len(paths)}")
    print()
    print(f"{'px':>5} {'vorhanden':>10} {'MB':>8} {'fehlt':>7} {'verwaist':>9} {'MB':>8}")
    zus = {"missing": {}, "orphans": {}, "bytes_orphan": 0, "bytes_used": 0,
           "avg": {}}
    for s in sizes:
        hier = da[s] | alt[s]        # neuer Ort gewinnt nicht, wir zaehlen nur
        gut = {d: v for d, v in hier.items() if d in soll}
        muell = {d: v for d, v in hier.items() if d not in soll}
        fehlt = soll - set(hier)
        b_gut = sum(v[1] for v in gut.values())
        b_muell = sum(v[1] for v in muell.values())
        zus["missing"][s] = fehlt
        zus["orphans"][s] = muell
        zus["bytes_orphan"] += b_muell
        zus["bytes_used"] += b_gut
        # Mittelwert je Groesse, aus den vorhandenen dieser Groesse. Vorher
        # stand hier der Gesamtverbrauch geteilt durch die Zahl im *neuen*
        # Verzeichnis -- das ist leer, und die Schaetzung meldete 34 GB fuer
        # 118 Kacheln.
        zus["avg"][s] = b_gut / len(gut) if gut else 0
        print(f"{s:5d} {len(gut):10d} {b_gut/MB:8.1f} {len(fehlt):7d} {len(muell):9d} {b_muell/MB:8.1f}")
    # Was sonst noch im Verzeichnis liegt. Ohne diese Zeile wundert man
    # sich, warum nach --prune immer noch mehr belegt ist als "gebraucht":
    # Grossansicht (1280) und Gesichtsausschnitte zaehlen hier nicht mit.
    rest_n = rest_b = 0
    for basis in {CACHE_DIR, LEGACY_CACHE}:
        if not basis.is_dir():
            continue
        for f in basis.rglob("*.jpg"):
            dig, _, rn = f.name.partition("_")
            try:
                if int(rn.removesuffix(".jpg")) in sizes:
                    continue
            except ValueError:
                pass
            rest_n += 1
            try:
                rest_b += f.stat().st_size
            except OSError:
                pass

    print()
    print(f"gebraucht: {zus['bytes_used']/MB:.0f} MB   verwaist: {zus['bytes_orphan']/MB:.0f} MB")
    if rest_n:
        print(f"dazu {rest_n} Kacheln anderer Groessen ({rest_b/MB:.0f} MB) -- "
              f"Grossansicht und Gesichter, hier nicht betrachtet.")
        print("            --sizes 160,320,640,1280 nimmt sie mit hinein.")
    if zus["bytes_orphan"] > 0:
        print("            --prune gibt den verwaisten Platz frei.")
    fehlend = sum(len(v) for v in zus["missing"].values())
    if fehlend:
        kosten = sum(len(zus["missing"][s]) * zus["avg"][s] for s in sizes)
        print(f"fehlend:   {fehlend} Kacheln, geschaetzt {kosten/MB:.1f} MB")
        print("            --warm erzeugt sie.")
    return zus


def prune(zus) -> int:
    weg = 0
    frei = 0
    for _s, muell in zus["orphans"].items():
        for f, b in muell.values():
            try:
                f.unlink()
                weg += 1
                frei += b
            except OSError as e:
                logger.debug("nicht loeschbar: %s (%s)", f, e)
    print(f"{weg} verwaiste Kacheln entfernt, {frei/MB:.0f} MB frei.")
    return weg


def move() -> int:
    """Vom alten Ort in den neuen schieben, statt neu zu rechnen."""
    if LEGACY_CACHE == CACHE_DIR or not LEGACY_CACHE.is_dir():
        print("Nichts umzuziehen.")
        return 0
    n = 0
    for f in LEGACY_CACHE.rglob("*.jpg"):
        ziel = CACHE_DIR / f.relative_to(LEGACY_CACHE)
        if ziel.exists():
            f.unlink()
            continue
        ziel.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(f), str(ziel))
            n += 1
        except OSError as e:
            logger.warning("konnte %s nicht verschieben: %s", f, e)
    print(f"{n} Kacheln nach {CACHE_DIR} verschoben.")
    return n


def rekey(sizes) -> int:
    """Vorhandene Kacheln vom Pfad- auf den Inhalts-Schluessel umbenennen.

    Stufe 3 stellt den Cache-Schluessel um. Ohne diesen Lauf lesen alte
    Kacheln weiter (der Rueckfall in `_find_cached` sorgt dafuer), aber jede
    Verschiebung wuerde ab dann eine zweite Kachel erzeugen. Umbenennen ist
    ein Verzeichniseintrag; neu rechnen waeren 295 MB ueber das
    Netzlaufwerk.

    Fotos ohne Inhalts-Hash bleiben unberuehrt -- fuer sie gibt es noch
    keinen neuen Schluessel. `tools/backfill_hash.py` holt das nach.
    """
    from api.thumbs import _rel, cache_keys

    q = client()
    paare, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, limit=512, offset=offset,
            with_payload=["file_path", "content_sha256"], with_vectors=False,
        )
        for p in batch:
            pl = p.payload or {}
            fp, h = pl.get("file_path"), pl.get("content_sha256")
            if fp and h:
                paare.append((fp, h))
        if offset is None:
            break

    if not paare:
        print("Kein Foto hat einen Inhalts-Hash -- erst tools/backfill_hash.py.")
        return 0

    umbenannt = schon = fehlt = 0
    for fp, h in paare:
        neu_key, _ = cache_keys(fp, None, 0.35, h)
        alt_key = fp
        for s_ in sizes:
            ziel = CACHE_DIR / _rel(neu_key, s_)
            if ziel.is_file():
                schon += 1
                continue
            quelle = None
            for basis in (CACHE_DIR, LEGACY_CACHE):
                kandidat = basis / _rel(alt_key, s_)
                if kandidat.is_file():
                    quelle = kandidat
                    break
            if quelle is None:
                fehlt += 1
                continue
            try:
                ziel.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(quelle), str(ziel))
                umbenannt += 1
            except OSError as e:
                logger.debug("konnte %s nicht umbenennen: %s", quelle, e)
                fehlt += 1
    print(f"{umbenannt} Kacheln umbenannt, {schon} lagen schon richtig, "
          f"{fehlt} nicht gefunden.")
    return umbenannt


def warm(zus, sizes) -> int:
    q = client()
    nach_digest = {digest(p): p for p in indexed_paths(q)}
    todo = []
    for s in sizes:
        for d in zus["missing"][s]:
            p = nach_digest.get(d)
            if p:
                todo.append((p, s))
    if not todo:
        print("Nichts zu tun -- alle Kacheln sind da.")
        return 0
    print(f"{len(todo)} Kacheln erzeugen. Jede liest das Original einmal;")
    print("ueber ein Netzlaufwerk ist das der langsame Teil.")
    fertig = 0
    for i, (p, s) in enumerate(todo, 1):
        try:
            get_thumb(p, size=s)
            fertig += 1
        except Exception as e:
            logger.debug("%s (%s px): %s", p, s, e)
        if i % 200 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
    print(f"{fertig} von {len(todo)} erzeugt.")
    return fertig


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prune", action="store_true", help="verwaiste Kacheln loeschen")
    ap.add_argument("--move", action="store_true", help="vom alten Ort herueberschieben")
    ap.add_argument("--warm", action="store_true", help="fehlende Kacheln erzeugen")
    ap.add_argument("--rekey", action="store_true",
                    help="vom Pfad- auf den Inhalts-Schluessel umbenennen")
    ap.add_argument("--sizes", default=",".join(str(s) for s in ATLAS_SIZES),
                    help="welche Groessen, mit Komma getrennt")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    if args.move:
        move()
        print()
    if args.rekey:
        rekey(sizes)
        print()
    zus = report(sizes)
    if args.prune:
        print()
        prune(zus)
    if args.warm:
        print()
        warm(zus, sizes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
