"""Fotos zu Ereignissen gruppieren.

Ein Ereignis ist eine Serie von Aufnahmen, die zeitlich dicht beieinander
liegen: der Geburtstagsabend, der Wandertag, die halbe Stunde am Strand.
Zwischen zwei Ereignissen klafft eine Lücke von Stunden.

Warum das mehr ist als Sortierung:

1. **Bewertung vererbt sich.** Zeigt ein Foto der Serie eine bekannte Person,
   gehört die Landschaft zehn Minuten davor mit dazu -- auch ohne Gesicht
   darauf. Ohne Gruppen müsste jedes Bild für sich überzeugen, und stille
   Aufnahmen fielen durch.
2. **Namen vererben sich.** Wer eine Serie ansieht und "Max 30. Geburtstag"
   erkennt, benennt damit alle Fotos auf einmal -- derselbe Ablauf wie beim
   Zuordnen von Gesichtern.

Die Grenze zwischen "dieselbe Gelegenheit" und "etwas Neues" ist eine
Setzung, keine Messung. `DEFAULT_GAP` ist ein Startwert; `describe()` zeigt,
was ein Wert am eigenen Bestand bewirkt.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

#: Lücke, ab der eine neue Gelegenheit beginnt.
DEFAULT_GAP = timedelta(hours=3)

#: Zeitstempel ohne Uhrzeit. Entsteht, wenn nur ein Tagesdatum bekannt ist --
#: dann ist "Lücke in Stunden" nicht entscheidbar und der Tag ist die feinste
#: ehrliche Einheit.
_MIDNIGHT = "T00:00:00"


@dataclass
class Event:
    """Eine Serie von Aufnahmen derselben Gelegenheit."""

    photo_ids: list[str] = field(default_factory=list)
    #: Herkunft der Serie -- Ereignisse mischen keine Kanaele.
    channel: str = "camera"
    start: datetime | None = None
    end: datetime | None = None
    #: True, wenn nur Tagesdaten vorlagen -- die Grenzen sind dann grob.
    day_level: bool = False

    @property
    def size(self) -> int:
        return len(self.photo_ids)

    @property
    def span(self) -> timedelta:
        if self.start is None or self.end is None:
            return timedelta(0)
        return self.end - self.start

    def key(self) -> str:
        """Stabile Kennung: Beginn plus Anzahl.

        Bewusst aus dem Inhalt abgeleitet und nicht zufällig, damit ein
        erneuter Lauf dieselben Ereignisse wiedererkennt, solange sich die
        Fotos nicht ändern.
        """
        stamp = self.start.strftime("%Y%m%dT%H%M%S") if self.start else "unbekannt"
        return f"ev-{self.channel}-{stamp}-{self.size:04d}"


def parse_stamp(value: str | None) -> tuple[datetime | None, bool]:
    """ISO-Zeitstempel lesen. Zweiter Rückgabewert: liegt nur ein Tag vor?"""
    if not value or len(value) < 10:
        return None, False
    try:
        dt = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
            return dt, True
        except ValueError:
            return None, False
    return dt, value[10:19] == _MIDNIGHT


def cluster(
    items: Iterable[tuple[str, str | None]] | Iterable[tuple[str, str | None, str]],
    gap: timedelta = DEFAULT_GAP,
) -> list[Event]:
    """`(photo_id, taken_at)` oder `(photo_id, taken_at, kanal)` gruppieren.

    **Ereignisse mischen keine Kanäle.** Zeitliche Nähe allein reicht bei
    Handy-Material nicht: dort fallen am selben Nachmittag ein Partyfoto, drei
    Screenshots und ein weitergeleitetes Meme an. Ohne Trennung entsteht daraus
    eine "Serie" über neun Stunden, die nichts zusammenhält als der Kalender.
    Zwei Handys auf derselben Feier finden trotzdem zusammen -- beide liefern
    den Kanal `camera` (siehe `ingest.provenance`).

    Tagesgenaue Zeitstempel werden nicht mit uhrzeitgenauen vermischt: bei
    ihnen ist jeder Tag ein Ereignis. Sonst würde ein Bestand, in dem die
    Hälfte der Fotos auf Mitternacht steht, zu einem einzigen Riesenklumpen
    je Tag verschmelzen und die echten Serien mitreißen.

    Fotos ohne verwertbares Datum bilden kein Ereignis -- sie bekommen keins,
    statt in ein falsches sortiert zu werden.
    """
    timed: dict[str, list[tuple[datetime, str]]] = {}
    daily: dict[tuple[str, str], list[str]] = {}
    undated = 0
    for item in items:
        photo_id, stamp = item[0], item[1]
        chan = item[2] if len(item) > 2 else "camera"
        dt, day_only = parse_stamp(stamp)
        if dt is None:
            undated += 1
            continue
        if day_only:
            daily.setdefault((chan, dt.strftime("%Y-%m-%d")), []).append(photo_id)
        else:
            timed.setdefault(chan, []).append((dt, photo_id))

    events: list[Event] = []
    for chan, rows in timed.items():
        rows.sort()
        current: Event | None = None
        for dt, photo_id in rows:
            if current is None or dt - current.end > gap:
                current = Event(photo_ids=[photo_id], channel=chan, start=dt, end=dt)
                events.append(current)
            else:
                current.photo_ids.append(photo_id)
                current.end = dt

    for (chan, day), ids in sorted(daily.items()):
        start = datetime.strptime(day, "%Y-%m-%d")
        events.append(Event(photo_ids=ids, channel=chan, start=start, end=start,
                            day_level=True))

    events.sort(key=lambda e: (e.start or datetime.min, e.key()))
    if undated:
        logger.info("%d Fotos ohne verwertbares Datum -- kein Ereignis zugeordnet", undated)
    return events


def describe(events: Sequence[Event]) -> dict:
    """Kennzahlen, um eine Lückenweite zu beurteilen."""
    if not events:
        return {"events": 0, "photos": 0}
    sizes = sorted(e.size for e in events)
    timed = [e for e in events if not e.day_level]
    return {
        "events": len(events),
        "photos": sum(sizes),
        "day_level": sum(1 for e in events if e.day_level),
        "singles": sum(1 for s in sizes if s == 1),
        "median_size": sizes[len(sizes) // 2],
        "largest": sizes[-1],
        "median_span_min": (
            sorted(e.span.total_seconds() / 60 for e in timed)[len(timed) // 2]
            if timed else 0.0
        ),
    }
