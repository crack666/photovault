"""Eine falsch gestellte Kamerauhr um einen festen Versatz korrigieren.

Der Gegenfall zu `tools/exif_repair.py`: dort werden **fehlende** Zeiten
ergaenzt und vorhandene nie angetastet. Hier wird eine vorhandene Zeit
ueberschrieben -- weil sie nachweislich falsch ist. Das ist der gefaehrlichere
Vorgang, deshalb steht er als eigenes Werkzeug mit eigenen Schranken:

* **Der Bezug ist eine Gruppe, kein Bestand.** Album und Kamera muessen genannt
  werden; ein Lauf ueber alles gibt es nicht.
* **`--expect` ist Pflicht bei `--apply`.** Wer schreibt, sagt vorher, wie viele
  Fotos er erwartet. Weicht der Bestand ab, bricht der Lauf ab, statt eine
  veraenderte Auswahl zu beschreiben.
* **Trockenlauf ist der Standard** und zeigt jeden einzelnen Wert.
* **Der Index wird aus der Datei nachgezogen**, nicht aus der Rechnung: nach dem
  Schreiben wird die Aufnahmezeit zurueckgelesen und *dieser* Wert in Qdrant
  gesetzt. Waere der Schreibvorgang anders ausgefallen als gedacht, stuende es
  im Index und nicht nur in diesem Programm.

Der alte Wert bleibt in der Herkunftsnotiz (`prev=`) stehen, der Lauf ist ueber
`ingest.exif_writer.revert` umkehrbar.

Warum ein Versatz und nicht ein Datum: die Uhr einer Kamera geht nicht falsch,
sie steht falsch. Reihenfolge und Abstaende innerhalb der Serie sind richtig --
nur der Nullpunkt nicht. Ein Versatz erhaelt genau das.

    python -m tools.exif_offset --album "..." --camera KODAK --offset 1400d12h
    python -m tools.exif_offset --album "..." --camera KODAK --offset 1400d12h \
        --expect 12 --apply
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta

from ingest.exif_writer import (
    WRITABLE_SUFFIXES, ExifWriteError, read_capture_time, write_capture_time,
)
from ingest.netfs import retry_io

logger = logging.getLogger(__name__)

BATCH = 256

#: "1400d12h", "-3h", "90m", "1400d" -- Vorzeichen gilt fuer den ganzen Ausdruck.
_RE_OFFSET = re.compile(r"(\d+(?:\.\d+)?)([dhms])")


class Abbruch(RuntimeError):
    """Der Lauf haelt an, statt etwas Unerwartetes zu schreiben."""


def parse_offset(text: str) -> timedelta:
    """`1400d12h` -> timedelta(days=1400, hours=12).

    Reine Tageszahlen sind erlaubt (`1400.5`), aber die Einheit ist besser:
    ein halber Tag ist eine Aussage ueber die Uhrzeit und sollte auch so
    dastehen.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("leerer Versatz")
    negativ = raw.startswith("-")
    raw = raw.lstrip("+-")

    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        delta = timedelta(days=float(raw))
    else:
        teile = _RE_OFFSET.findall(raw)
        if not teile or "".join(z + e for z, e in teile) != raw:
            raise ValueError(
                "Versatz nicht lesbar: %r -- erwartet z.B. 1400d12h, 90m, -3h" % text)
        einheit = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
        delta = timedelta(**{einheit[e]: float(z) for z, e in teile})

    if delta == timedelta(0):
        raise ValueError("Versatz 0 aendert nichts")
    return -delta if negativ else delta


def _parse_taken_at(value: str | None) -> datetime | None:
    if not value or len(value) < 19:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _model(payload: dict) -> str:
    exif = payload.get("exif")
    wert = (exif or {}).get("Model") if isinstance(exif, dict) else None
    return (wert or "").strip("\x00 ")


def sammeln(client, album: str, camera: str | None,
            collection: str = "photos") -> list[tuple[str, str, datetime]]:
    """Die Gruppe aus dem Index holen: `(punkt_id, pfad, alte_zeit)`.

    Sortiert nach Aufnahmezeit, damit der Trockenlauf die Serie in ihrer
    eigenen Reihenfolge zeigt -- die ja gerade das ist, was erhalten bleibt.
    """
    from qdrant_client.http import models as qm

    treffer: list[tuple[str, str, datetime]] = []
    offset = None
    flt = qm.Filter(must=[qm.FieldCondition(
        key="folder_name", match=qm.MatchValue(value=album))])
    while True:
        batch, offset = client.scroll(
            collection_name=collection, scroll_filter=flt, limit=BATCH,
            offset=offset, with_payload=["file_path", "taken_at", "exif"],
            with_vectors=False,
        )
        for punkt in batch:
            payload = punkt.payload or {}
            if camera and camera.lower() not in _model(payload).lower():
                continue
            pfad = payload.get("file_path")
            alt = _parse_taken_at(payload.get("taken_at"))
            if not pfad or alt is None:
                continue
            treffer.append((punkt.id, pfad, alt))
        if offset is None:
            break
    treffer.sort(key=lambda t: t[2])
    return treffer


def _nicht_beschreibbar(treffer) -> list[str]:
    return [p for _, p, _ in treffer
            if os.path.splitext(p)[1].lower() not in WRITABLE_SUFFIXES]


def _schreiben(client, collection, punkt_id, pfad, neu, apply):
    """Datei schreiben, dann den Index aus der Datei nachziehen."""
    try:
        ergebnis = retry_io(
            lambda: write_capture_time(pfad, neu, source="offset",
                                       dry_run=not apply, overwrite=True),
            what=pfad,
        )
    except (ExifWriteError, FileNotFoundError) as e:
        return "Schreiben fehlgeschlagen: %s" % e
    except Exception as e:  # noqa: BLE001 -- ein Foto darf den Lauf nicht kippen
        logger.warning("%s: %s", pfad, e)
        return "Schreiben fehlgeschlagen"

    if not apply:
        return "waere geschrieben" if not ergebnis["reason"] else ergebnis["reason"]
    if not ergebnis["written"]:
        return ergebnis["reason"] or "nicht geschrieben"

    # Nicht die Rechnung in den Index legen, sondern das, was in der Datei steht.
    try:
        steht = retry_io(lambda: read_capture_time(pfad), what=pfad)
    except Exception as e:  # noqa: BLE001
        logger.warning("geschrieben, aber nicht zurueckgelesen (%s): %s", pfad, e)
        return "geschrieben, Index offen"
    if steht is None:
        return "geschrieben, Index offen"
    iso = steht.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    client.set_payload(collection_name=collection,
                       payload={"taken_at": iso, "date": iso[:10]},
                       points=[punkt_id], wait=True)
    return "geschrieben"


def run(client, album: str, offset: timedelta, camera: str | None = None,
        collection: str = "photos", apply: bool = False,
        expect: int | None = None) -> dict:
    treffer = sammeln(client, album, camera, collection)
    if not treffer:
        raise Abbruch("Keine Fotos fuer Album %r%s gefunden."
                      % (album, " / Kamera %r" % camera if camera else ""))

    if expect is not None and len(treffer) != expect:
        raise Abbruch(
            "Erwartet waren %d Fotos, im Index stehen %d. Der Bestand hat sich "
            "seit der Messung geaendert -- erst nachsehen, dann schreiben."
            % (expect, len(treffer)))
    if apply and expect is None:
        raise Abbruch("--apply verlangt --expect: sage vorher, wie viele Fotos "
                      "du erwartest.")

    unbeschreibbar = _nicht_beschreibbar(treffer)
    if unbeschreibbar:
        raise Abbruch(
            "%d Datei(en) sind nicht verlustfrei beschreibbar (%s). Der Lauf "
            "wuerde die Gruppe zerreissen -- Auswahl enger fassen."
            % (len(unbeschreibbar), os.path.basename(unbeschreibbar[0])))

    tage = offset.total_seconds() / 86400
    logger.info("%s: %d Fotos, Versatz %s (%+.4f Tage)",
                "Schreibe" if apply else "Trockenlauf", len(treffer), offset, tage)
    logger.info("%-28s %-19s -> %-19s", "Datei", "steht auf", "wird")

    zaehler: dict[str, int] = {}
    for punkt_id, pfad, alt in treffer:
        neu = alt + offset
        ausgang = _schreiben(client, collection, punkt_id, pfad, neu, apply)
        zaehler[ausgang] = zaehler.get(ausgang, 0) + 1
        logger.info("%-28s %s -> %s  %s", os.path.basename(pfad)[:28],
                    alt.strftime("%Y-%m-%d %H:%M:%S"),
                    neu.strftime("%Y-%m-%d %H:%M:%S"), ausgang)

    for schluessel, n in sorted(zaehler.items(), key=lambda kv: -kv[1]):
        logger.info("   %-28s %4d", schluessel, n)
    if not apply:
        logger.info("Nichts geschrieben. Mit --expect %d --apply wird es ernst.",
                    len(treffer))
    return zaehler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--album", required=True,
                        help="folder_name der Gruppe, exakt")
    parser.add_argument("--camera", default=None,
                        help="Teilstring aus exif.Model -- ohne das gilt das ganze Album")
    parser.add_argument("--offset", required=True,
                        help="z.B. 1400d12h, -3h, 90m oder eine Tageszahl")
    parser.add_argument("--expect", type=int, default=None,
                        help="erwartete Anzahl Fotos; bei --apply Pflicht")
    parser.add_argument("--apply", action="store_true",
                        help="Tatsaechlich schreiben. Ohne das nur zeigen.")
    parser.add_argument("--qdrant-url",
                        default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection",
                        default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from qdrant_client import QdrantClient

    try:
        offset = parse_offset(args.offset)
    except ValueError as e:
        print("Versatz: %s" % e, file=sys.stderr)
        raise SystemExit(2)

    try:
        run(QdrantClient(url=args.qdrant_url), album=args.album, offset=offset,
            camera=args.camera, collection=args.collection,
            apply=args.apply, expect=args.expect)
    except Abbruch as e:
        print("Abgebrochen: %s" % e, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
