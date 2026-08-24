"""Textkomposition fuer Anzeige und Text-Vektor.

Arbeitet auf einem Payload-Dict, damit dieselbe Logik im Ingest (aus einem
frischen PhotoRecord) und beim nachtraeglichen Re-Embed (aus dem, was in
Qdrant liegt) gilt. Zwei Ausgaben mit verschiedenen Aufgaben:

`caption_display` ist die Kopfzeile fuer Menschen: Datum, Jahreszeit, Anlass, Ort.

`grounded_document` ist das, was eingebettet wird. Gemessen an der Stichprobe
liegt die Cosinus-Aehnlichkeit zwischen Fotos *desselben* Albums bei 0.95-0.997,
wenn nur Ordner/Datum/Sequenz eingehen -- der Vektor wird zum Album-Fingerabdruck
und kann Fotos darin nicht mehr unterscheiden. Nur das Album mit Captions lag bei
0.738. Deshalb stehen hier die unterscheidenden Teile (Personen, Notizen, Caption)
vorn und ausfuehrlich, der albumweit identische Kontext knapp am Ende. Fuer
"welches Album" gibt es Payload-Filter, die exakt und in Millisekunden antworten.
"""
from __future__ import annotations

import re
from typing import Any

MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)

# Ordnernamen tragen oft ein fuehrendes Datum ("2016_04_23 Junggesellenabschied")
# oder eine Kamera-Kennung. Beides steht schon strukturiert im Payload.
RE_LEADING_DATE = re.compile(r"^\s*\d{4}[-_.]?\d{0,2}[-_.]?\d{0,2}\s*[-_. ]\s*")
RE_TRAILING_DATE = re.compile(r"\s*[-_. ]\s*\d{4}([-_.]\d{2}){0,2}\s*$")
GENERIC_FOLDERS = {
    "dcim", "fotos", "photos", "bilder", "images", "pictures", "camera", "kamera",
}
RE_CAMERA_FOLDER = re.compile(r"^\d{3}[A-Z_]{3,8}$|^\d{3}(msdcf|canon|nikon|olymp|_fuji)$", re.I)


def season(date: str | None) -> str | None:
    """Meteorologische Jahreszeit aus einem ISO-Datum."""
    if not date or len(date) < 7:
        return None
    try:
        month = int(date[5:7])
    except ValueError:
        return None
    if month in (12, 1, 2):
        return "Winter"
    if month in (3, 4, 5):
        return "Frühling"
    if month in (6, 7, 8):
        return "Sommer"
    if month in (9, 10, 11):
        return "Herbst"
    return None


def format_date(date: str | None, confidence: float | None) -> str | None:
    """Datum so genau ausschreiben, wie es tatsaechlich belegt ist.

    Ein aus der Dateizeit geratenes Datum als '31. Oktober 2018' zu behaupten
    waere falsche Praezision -- daraus wird 'um Oktober 2018'.
    """
    if not date or len(date) < 4:
        return None
    conf = 1.0 if confidence is None else float(confidence)
    year = date[:4]
    if len(date) < 10:
        return year
    try:
        month_name = MONTHS_DE[int(date[5:7]) - 1]
        day = int(date[8:10])
    except (ValueError, IndexError):
        return year
    if conf >= 0.9:
        return f"{day}. {month_name} {year}"
    if conf >= 0.6:
        return f"{month_name} {year}"
    return f"um {month_name} {year}"


def event_name(payload: dict[str, Any]) -> str | None:
    """Der Anlass aus dem Ordnernamen, ohne Datum und ohne Kameraverzeichnis."""
    folder = (payload.get("folder_name") or "").strip()
    if not folder:
        return None
    if folder.lower() in GENERIC_FOLDERS or RE_CAMERA_FOLDER.match(folder):
        return None
    cleaned = RE_TRAILING_DATE.sub("", RE_LEADING_DATE.sub("", folder)).strip(" -_.")
    return cleaned or None


def person_names(payload: dict[str, Any]) -> list[str]:
    """Bestaetigte Personen zuerst; Face-Match-Vorschlaege sind keine Bestaetigung."""
    names = payload.get("person_names") or payload.get("person_ids") or []
    return [str(n) for n in names if n and not str(n).startswith("_")]


def caption_display(payload: dict[str, Any]) -> str | None:
    """Kopfzeile: '23. August 2015 · Sommer · Junggesellenabschied · Berlin'."""
    parts: list[str] = []
    date_str = format_date(payload.get("date"), payload.get("date_confidence"))
    if date_str:
        parts.append(date_str)
    sea = season(payload.get("date"))
    if sea:
        parts.append(sea)
    event = event_name(payload)
    if event:
        parts.append(event)
    location = payload.get("location")
    if location and str(location).strip().lower() != (event or "").lower():
        parts.append(str(location).strip())
    return " · ".join(parts) or None


def grounded_document(payload: dict[str, Any]) -> str:
    """Der Text, der eingebettet wird. Unterscheidendes zuerst."""
    lines: list[str] = []

    names = person_names(payload)
    if names:
        lines.append(f"Personen: {', '.join(names)}")

    notes = payload.get("annotations") or []
    if notes:
        lines.append(", ".join(str(n) for n in notes))

    caption = payload.get("caption_de")
    if caption:
        lines.append(str(caption))

    tags = payload.get("scene_tags") or []
    if tags:
        lines.append(", ".join(str(t) for t in tags[:8]))

    # Albumweit identisch -- knapp, und ans Ende.
    head = caption_display(payload)
    if head:
        lines.append(head)

    if not names:
        suggestions = payload.get("person_suggestions") or []
        if suggestions:
            lines.append(f"Vermutlich: {', '.join(str(s) for s in suggestions)}")

    return "\n".join(lines)


def record_payload(record) -> dict[str, Any]:
    """PhotoRecord -> das Dict, das die Funktionen oben erwarten."""
    return {
        "date": getattr(record, "date", None),
        "date_confidence": getattr(record, "date_confidence", None),
        "folder_name": getattr(record, "folder_name", None),
        "location": getattr(record, "location", None),
        "person_ids": getattr(record, "person_ids", None),
        "person_suggestions": getattr(record, "person_suggestions", None),
        "annotations": getattr(record, "annotations", None),
        "caption_de": getattr(record, "caption_de", None),
        "scene_tags": getattr(record, "scene_tags", None),
    }
