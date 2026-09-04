"""Stufe 1 der Identitäts-Umstellung: `photo_uid` nachtragen.

Bis jetzt war die Identität eines Fotos `sha256(Dateipfad)` -- und daran hing
der Qdrant-Punkt, der Fremdschlüssel der Gesichter und der Schlüssel im
Vorschaubild-Cache. Ein Pfad, vier Rollen. Ein Rename ausserhalb von
PhotoVault erzeugte deshalb ein neues Foto und liess Namen, Beschreibungen
und Gesichter am alten hängen.

Dieser Lauf ändert **keinen Wert**. Er schreibt `photo_uid` mit demselben
Inhalt, den `photo_id` schon hat, und friert ihn damit ein: ab jetzt wird die
Kennung gelesen statt aus dem Pfad gerechnet.

Genau deshalb ist die Umstellung billig. Gemessen an diesem Bestand:

    14.593 Punkte, alle mit photo_id, keine doppelt
    point_id == uuid5(photo_id) fuer alle 14.593
    photo_id == sha256(pfad)    fuer alle 14.593

Die Gesichter zeigen also schon auf diesen Wert, die Punkt-IDs sind schon
daraus gebildet, der Cache liegt schon darunter. Kein Umhängen, kein
Umschreiben, keine neuen Punkt-IDs -- ein Feldzuwachs.

Rücknehmbar: das Feld wieder zu löschen stellt den alten Zustand her.

    python -m tools.migrate_uid --check     nur zählen
    python -m tools.migrate_uid             nachtragen
"""
from __future__ import annotations

import argparse
import logging
import sys

from api.qdrant_util import PHOTOS, client

logger = logging.getLogger(__name__)
BATCH = 512


def ensure_index(q, dry_run: bool) -> str:
    """Den Index auf `photo_uid` anlegen.

    Er entsteht sonst erst beim naechsten Ingest-Lauf (`PHOTO_INDEXES`), und
    bis dahin waere das Wiedererkennen einer verschobenen Datei ein Full Scan
    ueber 14.593 Punkte -- je Datei.
    """
    try:
        info = q.get_collection(PHOTOS)
        vorhanden = "photo_uid" in (getattr(info, "payload_schema", None) or {})
    except Exception as e:
        return f"Sammlung nicht lesbar: {e}"
    if vorhanden:
        return "war schon da"
    if dry_run:
        return "fehlt noch"
    try:
        q.create_payload_index(collection_name=PHOTOS, field_name="photo_uid",
                               field_schema="keyword", wait=True)
        return "angelegt"
    except Exception as e:
        return f"FEHLGESCHLAGEN: {type(e).__name__}: {e}"


def load(q) -> list[tuple[str, str, str]]:
    """(Punkt-ID, photo_id, photo_uid) für alle Fotos."""
    out, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, limit=BATCH, offset=offset,
            with_payload=["photo_id", "photo_uid"], with_vectors=False,
        )
        for p in batch:
            pl = p.payload or {}
            out.append((str(p.id), pl.get("photo_id") or "", pl.get("photo_uid") or ""))
        if offset is None:
            return out


def plan(rows) -> dict:
    """Was zu tun ist -- und was auffällt, bevor etwas geschrieben wird."""
    todo, ok, abweichend, ohne = [], 0, [], []
    for pid, phid, uid in rows:
        if not phid:
            # Ohne alte Kennung gibt es nichts einzufrieren. Eine zu erfinden
            # waere schlimmer: die Gesichter zeigen dann ins Leere.
            ohne.append(pid)
            continue
        if uid == phid:
            ok += 1
        elif uid:
            # Schon gesetzt, aber anders. Das darf dieser Lauf nicht
            # ueberschreiben -- es hiesse, eine bereits eingefrorene Kennung
            # zu bewegen, und genau das soll nie passieren.
            abweichend.append((pid, phid, uid))
        else:
            todo.append((pid, phid))
    return {"todo": todo, "ok": ok, "diverging": abweichend, "without_id": ohne}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="nur zählen, nichts schreiben")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    q = client()
    rows = load(q)
    if not rows:
        print("Keine Fotos im Index.")
        return 1
    p = plan(rows)

    print(f"{len(rows)} Fotos")
    print(f"  schon eingefroren : {p['ok']}")
    print(f"  nachzutragen      : {len(p['todo'])}")
    if p["diverging"]:
        print(f"  abweichend        : {len(p['diverging'])}  <- NICHT angetastet")
        for pid, phid, uid in p["diverging"][:5]:
            print(f"      {pid}  photo_id={phid[:12]}…  photo_uid={uid[:12]}…")
        print("      Diese Punkte tragen schon eine andere Kennung. Sie zu")
        print("      ueberschreiben hiesse, eine eingefrorene Kennung zu")
        print("      bewegen -- der Lauf laesst sie deshalb stehen.")
    if p["without_id"]:
        print(f"  ohne photo_id     : {len(p['without_id'])}  <- uebersprungen")

    print(f"  Index photo_uid   : {ensure_index(q, args.check)}")

    if args.check:
        print("\n--check: nichts geschrieben.")
        return 0
    if not p["todo"]:
        print("\nNichts zu tun.")
        return 0

    for i in range(0, len(p["todo"]), BATCH):
        chunk = p["todo"][i:i + BATCH]
        # Je Punkt ein eigener Wert -- kein gemeinsames set_payload moeglich.
        for pid, phid in chunk:
            q.set_payload(collection_name=PHOTOS, payload={"photo_uid": phid},
                          points=[pid], wait=False)
        print(f"  {min(i + BATCH, len(p['todo']))}/{len(p['todo'])}")
    # Einmal am Ende warten, statt bei jedem der 14.593 Schreibvorgaenge.
    q.set_payload(collection_name=PHOTOS, payload={"photo_uid": p["todo"][-1][1]},
                  points=[p["todo"][-1][0]], wait=True)
    print("\nFertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
