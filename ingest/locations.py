"""Orte aus Ordnernamen erkennen.

Die alte Liste enthielt nur Länder. In einem privaten Archiv heißen Alben aber
„groemitz“, „Papertec Bowling“ oder „Kastenlauf“ — kein einziges Foto bekam so
einen Ort. Deshalb zwei Quellen:

1. Eine erweiterte Liste bekannter Orte (Länder, deutsche Städte, Regionen).
2. Eine eigene Liste unter `PHOTOVAULT_PLACES` bzw. `places.json` im Projekt --
   dort trägt man die Orte ein, die nur im eigenen Archiv vorkommen.

Bewusst konservativ: Lieber kein Ort als ein falscher. Ein Ordner heißt
„20. Geburtstag“, das ist ein Geburtstag und kein Ort — solche Namen dürfen nicht
zufällig auf eine Stadt passen.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

COUNTRIES = {
    "griechenland": "Griechenland", "greece": "Griechenland",
    "italien": "Italien", "italy": "Italien",
    "spanien": "Spanien", "spain": "Spanien",
    "portugal": "Portugal", "turkei": "Türkei", "turkey": "Türkei",
    "thailand": "Thailand", "bali": "Bali", "indonesien": "Indonesien",
    "deutschland": "Deutschland", "germany": "Deutschland",
    "osterreich": "Österreich", "austria": "Österreich",
    "schweiz": "Schweiz", "switzerland": "Schweiz",
    "frankreich": "Frankreich", "france": "Frankreich",
    "niederlande": "Niederlande", "netherlands": "Niederlande", "holland": "Niederlande",
    "danemark": "Dänemark", "denmark": "Dänemark",
    "norwegen": "Norwegen", "norway": "Norwegen",
    "schweden": "Schweden", "sweden": "Schweden",
    "island": "Island", "iceland": "Island",
    "kroatien": "Kroatien", "croatia": "Kroatien",
    "ungarn": "Ungarn", "hungary": "Ungarn",
    "polen": "Polen", "poland": "Polen",
    "tschechien": "Tschechien", "czech": "Tschechien",
    "kanada": "Kanada", "canada": "Kanada",
    "usa": "USA", "amerika": "USA", "america": "USA",
    "japan": "Japan", "china": "China", "vietnam": "Vietnam",
    "agypten": "Ägypten", "egypt": "Ägypten", "marokko": "Marokko",
    "belgien": "Belgien", "luxemburg": "Luxemburg", "irland": "Irland",
    "england": "England", "schottland": "Schottland", "london": "London",
    "mallorca": "Mallorca", "ibiza": "Ibiza", "kreta": "Kreta", "rhodos": "Rhodos",
}

CITIES = {
    "berlin": "Berlin", "hamburg": "Hamburg", "muenchen": "München",
    "munchen": "München", "koeln": "Köln", "koln": "Köln",
    "frankfurt": "Frankfurt", "stuttgart": "Stuttgart", "duesseldorf": "Düsseldorf",
    "dusseldorf": "Düsseldorf", "dortmund": "Dortmund", "essen": None,  # zu mehrdeutig
    "leipzig": "Leipzig", "dresden": "Dresden", "hannover": "Hannover",
    "nuernberg": "Nürnberg", "nurnberg": "Nürnberg", "bremen": "Bremen",
    "potsdam": "Potsdam", "rostock": "Rostock", "kiel": "Kiel",
    "luebeck": "Lübeck", "lubeck": "Lübeck", "magdeburg": "Magdeburg",
    "wien": "Wien", "zuerich": "Zürich", "zurich": "Zürich",
    "amsterdam": "Amsterdam", "paris": "Paris", "prag": "Prag", "rom": "Rom",
    "barcelona": "Barcelona", "madrid": "Madrid", "lissabon": "Lissabon",
    "groemitz": "Grömitz", "gromitz": "Grömitz",
    "timmendorf": "Timmendorfer Strand", "scharbeutz": "Scharbeutz",
    "sylt": "Sylt", "usedom": "Usedom", "ruegen": "Rügen", "rugen": "Rügen",
    "harz": "Harz", "allgaeu": "Allgäu", "allgau": "Allgäu",
    "ostsee": "Ostsee", "nordsee": "Nordsee", "bodensee": "Bodensee",
}

_extra_cache: dict[str, dict] | None = None


def _fold(text: str) -> str:
    """Umlaute und Akzente entfernen, damit „Grömitz“ und „groemitz“ gleich sind."""
    text = text.lower().strip()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _load_extra() -> dict[str, str]:
    """Eigene Ortsliste: {"suchbegriff": "Anzeigename"}."""
    global _extra_cache
    if _extra_cache is not None:
        return _extra_cache
    _extra_cache = {}
    path = os.environ.get("PHOTOVAULT_PLACES") or str(
        Path(__file__).resolve().parent.parent / "places.json"
    )
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _extra_cache = {_fold(k): str(v) for k, v in data.items() if v}
            logger.info("Loaded %d custom places from %s", len(_extra_cache), path)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read places file %s: %s", path, e)
    return _extra_cache


def all_places() -> dict[str, str]:
    places = {k: v for k, v in {**COUNTRIES, **CITIES}.items() if v}
    places.update(_load_extra())
    return places


def detect(*texts: str | None) -> tuple[str, str] | None:
    """(Anzeigename, Schlüssel) für den ersten erkannten Ort, sonst None.

    Trifft nur auf ganze Wörter -- „Kastenlauf“ darf nicht „Kasten“ ergeben,
    und ein Album „Essen 2010“ bleibt lieber ohne Ort als fälschlich in der
    Stadt Essen zu landen.
    """
    places = all_places()
    for text in texts:
        if not text:
            continue
        words = set(re.split(r"[^a-z0-9]+", _fold(text)))
        for key, label in places.items():
            parts = key.split()
            if len(parts) > 1:
                if _fold(key) in _fold(text):
                    return label, key
            elif key in words:
                return label, key
    return None


def reset_cache() -> None:
    global _extra_cache
    _extra_cache = None
