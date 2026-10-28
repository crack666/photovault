"""Ortszeit fuer Werte, die ohne Zeitzone in die Datei gehen.

Der Index haelt Dateizeiten in UTC (`2022-07-04T17:51:19Z`). EXIF
`DateTimeOriginal` hat keine Zeitzone und wird von jedem Betrachter als
**Ortszeit** gelesen. Wer den Indexwert unveraendert hineinschreibt, stellt
die Datei im Sommer zwei Stunden, im Winter eine Stunde zu frueh -- und nahe
Mitternacht auf den Vortag (Issue #43).

Welche Ortszeit gemeint ist, sagt `PHOTOVAULT_TZ`, sonst `TZ`, sonst die
Systemzeitzone. Im Container ist die Systemzeitzone meist UTC; dort muss
eine der beiden Variablen gesetzt sein (docker-compose setzt `TZ`).
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone, tzinfo


def zone() -> tzinfo:
    """Die Zeitzone, in der Aufnahmezeiten in Dateien stehen."""
    name = (os.environ.get("PHOTOVAULT_TZ") or os.environ.get("TZ") or "").strip()
    name = name.lstrip(":")
    if name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_utc(value) -> datetime | None:
    """ISO-Zeitstempel mit Zone (`Z`, `+00:00`) als bewusste Zeit.

    Ohne Zone: `None` -- dann ist nicht bekannt, was gemeint ist, und raten
    waere genau der Fehler, um den es hier geht.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def to_local(dt: datetime) -> datetime:
    """Bewusste Zeit in die Ortszeit (`zone()`), weiterhin mit Zone."""
    return dt.astimezone(zone())


def local_day(value) -> date | None:
    """Kalendertag eines Zeitstempels in Ortszeit.

    Ein Dateiname wie `IMG-20220705-WA0001` nennt den Tag, wie ihn das
    Telefon vor Ort sah. Vergleichen laesst sich das nur mit dem Tag der
    Dateizeit *in Ortszeit*: um 00:30 Ortszeit ist es in UTC noch gestern.
    Werte ohne Zone werden so genommen, wie sie dastehen.
    """
    aware = parse_utc(value)
    if aware is not None:
        return to_local(aware).date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
