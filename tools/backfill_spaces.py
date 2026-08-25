"""Das Payload-Feld `space` nachtragen und die fehlenden Indizes anlegen.

Der Bereich eines Fotos *ist* die erste Ordnerebene unter der gemeinsamen
Wurzel (siehe ingest/spaces.py). Die Karte rechnet ihn beim Bauen aus dem Pfad
aus. Die Suche kann das nicht: Qdrant filtert Schlüsselwörter, keine Präfixe.
Also wird dieselbe Rechnung einmal in den Payload geschrieben.

Das Feld ist damit ein Zwischenspeicher, keine zweite Wahrheit -- weicht es
vom Pfad ab, ist das Feld falsch. `--check` sagt, wie viele abweichen, ohne
etwas zu schreiben; ein erneuter Lauf richtet es. Nach jedem Verschieben von
Fotos ist ein Lauf fällig.

Nebenbei entstehen drei Payload-Indizes, die fehlten: `space` (der neue
Filter), `folder_name` (die Albumsuche lief als Full Scan) und `trashed_at`
(jede Ansicht filtert seit dem Papierkorb darauf).

    python -m tools.backfill_spaces --check
    python -m tools.backfill_spaces
"""
from __future__ import annotations

import argparse
import collections
import logging
import sys

from api.qdrant_util import PHOTOS, TRASH_KEY, client
from ingest.spaces import common_root, space_of

logger = logging.getLogger(__name__)

BATCH = 512

#: Feld -> Indextyp. Alle drei werden gefiltert, keiner war indiziert.
INDEXES = {
    "space": "keyword",
    "folder_name": "keyword",
    TRASH_KEY: "datetime",
}


def load_paths(q) -> list[tuple[str, str, str]]:
    """(Punkt-ID, Pfad, eingetragener Bereich) für alle Fotos."""
    out, offset = [], None
    while True:
        batch, offset = q.scroll(
            collection_name=PHOTOS, limit=BATCH, offset=offset,
            with_payload=["file_path", "space"], with_vectors=False,
        )
        for point in batch:
            payload = point.payload or {}
            out.append((str(point.id), payload.get("file_path") or "",
                        payload.get("space") or ""))
        if offset is None:
            return out


def plan(rows: list[tuple[str, str, str]]) -> tuple[str, dict[str, list[str]], int]:
    """Wurzel, {Bereich: [Punkt-IDs, die geschrieben werden müssen]}, Anzahl korrekt."""
    root = common_root([path for _, path, _ in rows])
    todo: dict[str, list[str]] = collections.defaultdict(list)
    ok = 0
    for pid, path, have in rows:
        want = space_of(path, root)
        if have == want:
            ok += 1
        else:
            todo[want].append(pid)
    return root, dict(todo), ok


def existing_indexes(q) -> set[str]:
    """Welche Payload-Indizes die Sammlung schon hat."""
    try:
        info = q.get_collection(PHOTOS)
    except Exception as e:
        logger.warning("Sammlung nicht lesbar: %s", e)
        return set()
    return set((getattr(info, "payload_schema", None) or {}).keys())


def ensure_indexes(q, dry_run: bool) -> list[str]:
    """Fehlende Payload-Indizes anlegen. Vorhandene meldet Qdrant als Fehler."""
    made = []
    have = existing_indexes(q) if dry_run else set()
    for field, kind in INDEXES.items():
        if dry_run:
            # Den Zustand melden, nicht die Absicht -- sonst behauptet ein
            # Probelauf Arbeit, die es nicht gibt.
            made.append(f"{field} war schon da" if field in have
                        else f"{field} ({kind}) fehlt noch")
            continue
        try:
            q.create_payload_index(collection_name=PHOTOS, field_name=field,
                                   field_schema=kind, wait=True)
            made.append(f"{field} ({kind}) angelegt")
        except Exception as e:
            # "already exists" ist der Normalfall beim zweiten Lauf und kein
            # Fehler -- alles andere schon, und das soll man sehen.
            text = str(e)
            if "already exists" in text or "already" in text.lower():
                made.append(f"{field} war schon da")
            else:
                made.append(f"{field} FEHLGESCHLAGEN: {type(e).__name__}: {e}")
    return made


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="nur zählen, nichts schreiben")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Ein Lauf macht 34 Scroll-Aufrufe; die einzeln zu protokollieren
    # verdeckt genau das, was man lesen will.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    q = client()
    rows = load_paths(q)
    if not rows:
        print("Keine Fotos im Index.")
        return 1

    root, todo, ok = plan(rows)
    offen = sum(len(v) for v in todo.values())
    print(f"Wurzel: {root or '/'}")
    print(f"{len(rows)} Fotos, {ok} schon richtig, {offen} zu schreiben\n")
    for name in sorted(todo, key=lambda n: -len(todo[n])):
        print(f"  {name:24s} {len(todo[name]):6d}")
    print()

    for line in ensure_indexes(q, args.check):
        print(f"  Index: {line}")

    if args.check:
        print("\n--check: nichts geschrieben.")
        return 0
    if not offen:
        print("\nNichts zu tun.")
        return 0

    for name, ids in todo.items():
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            q.set_payload(collection_name=PHOTOS, payload={"space": name},
                          points=chunk, wait=True)
        print(f"  {name}: {len(ids)} geschrieben")
    print("\nFertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
