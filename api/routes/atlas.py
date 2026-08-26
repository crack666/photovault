"""Was die Karte nicht wissen kann.

`atlas.json` ist ein Standbild: gerechnet zu einem Zeitpunkt, danach
unveränderlich bis zum nächsten Lauf. Wer danach ein Foto in den Papierkorb
legt oder endgültig löscht, sieht es auf der Karte weiter stehen -- und beim
Anklicken einen 404. Genau so gemeldet: „selbst nach hartem Neuladen tauchen
die gelöschten Bilder wieder auf".

Die Oberfläche merkte sich Weggeräumtes zwar lokal, aber nur was *sie selbst*
getan hatte. Wer im Reiter „Papierkorb" löscht oder von einem anderen Rechner
schaut, fällt durch dieses Netz.

Also fragt die Karte beim Laden hier nach: welche ihrer Punkte gibt es nicht
mehr, und welche liegen im Papierkorb? Gerechnet wird das gegen dieselbe
Datei, die die Karte lädt -- der Server liest sie selbst, damit der Browser
nicht 17.000 Kennungen hochschicken muss.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.qdrant_util import PHOTOS, client

logger = logging.getLogger(__name__)
router = APIRouter()

ATLAS_FILE = Path(__file__).resolve().parents[2] / "web" / "static" / "atlas" / "atlas.json"

#: So viele Kennungen je Abfrage. Ohne Payload und ohne Vektoren ist das
#: billig; 17.000 Punkte sind damit siebzehn Rundreisen.
BATCH = 1000

#: Wie lange die Antwort gilt. Sie ändert sich nur, wenn gelöscht wird --
#: und dann darf man einen Moment alt sein, statt bei jedem Kartenaufbau
#: den ganzen Bestand zu prüfen.
TTL_SECONDS = 30

_cache: dict = {"at": 0.0, "value": None, "mtime": 0.0}


def atlas_ids() -> tuple[list[str], str]:
    """Punkt-Kennungen und Bauzeitpunkt aus der Kartendatei."""
    with ATLAS_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return [str(x) for x in data.get("ids", [])], str(data.get("built_at") or "")


def check(q, ids: list[str]) -> tuple[list[str], list[str]]:
    """(nicht mehr vorhanden, im Papierkorb) -- in der Reihenfolge der Karte."""
    weg: list[str] = []
    korb: list[str] = []
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        found = q.retrieve(collection_name=PHOTOS, ids=chunk,
                           with_payload=["trashed_at"], with_vectors=False)
        da = {}
        for p in found:
            da[str(p.id)] = (p.payload or {}).get("trashed_at")
        for pid in chunk:
            if pid not in da:
                weg.append(pid)
            elif da[pid]:
                korb.append(pid)
    return weg, korb


@router.get("/gone")
def gone() -> dict:
    """Punkte der Karte, die es nicht mehr gibt oder die vorgemerkt sind."""
    if not ATLAS_FILE.exists():
        raise HTTPException(404, "Keine Karte gerechnet")

    mtime = ATLAS_FILE.stat().st_mtime
    now = time.time()
    if (_cache["value"] is not None and _cache["mtime"] == mtime
            and now - _cache["at"] < TTL_SECONDS):
        return _cache["value"]

    try:
        ids, built_at = atlas_ids()
    except Exception as e:
        raise HTTPException(500, f"Karte nicht lesbar: {e}") from e

    q = client()
    try:
        weg, korb = check(q, ids)
    except Exception as e:
        # Kein leeres Ergebnis vortäuschen: das hieße „alles noch da", und
        # die Karte zeigte weiter Gelöschtes.
        logger.exception("Abgleich der Karte fehlgeschlagen")
        raise HTTPException(502, f"Abgleich fehlgeschlagen: {e}") from e

    value = {
        "built_at": built_at,
        "checked": len(ids),
        "deleted": weg,
        "trashed": korb,
    }
    _cache.update({"at": now, "value": value, "mtime": mtime})
    return value
