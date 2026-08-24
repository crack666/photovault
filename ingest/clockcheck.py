"""Kameras mit falsch gestellter Uhr finden.

Alte Digitalkameras verlieren beim Batteriewechsel ihre Zeit. Im Archiv fällt
das nicht auf, solange man einzelne Bilder ansieht -- erst im Album wird es
sichtbar: 90 Fotos aus 2006, 13 aus 2009, und die 13 stammen alle von
derselben Kamera.

Genau das ist das Erkennungsmerkmal. Ein Ausreißer allein sagt wenig; ein
Ausreißer, den **eine bestimmte Kamera** in **mehreren Alben** produziert,
ist eine falsch gestellte Uhr. Das Album liefert dabei die Wahrheit: die
Mehrheit der Fotos stammt von Geräten, deren Uhr stimmte.

Zwei Fehlerbilder, die unterschiedlich behandelt werden müssen:

* **Konstanter Versatz.** Die Uhr lief, war aber falsch gestellt. Alle Fotos
  der Kamera liegen um dieselbe Spanne daneben. Korrigierbar, ohne die
  Reihenfolge oder die Tageszeit zu verlieren.
* **Zurückgefallen.** Die Uhr sprang beim Batteriewechsel auf einen
  Werksstand. Die Abstände sind unbrauchbar; erhalten bleibt nur die
  Tageszeit. Hier lässt sich das Datum aus dem Album übernehmen -- mehr nicht,
  und das sollte man wissen, bevor man es tut.

Dieses Modul stellt nur fest. Geschrieben wird woanders.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

logger = logging.getLogger(__name__)

RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")

#: Ab welcher Abweichung ein Foto als Ausreißer gilt. Ein Jahr ist grob
#: gewählt: Alben ziehen sich über Wochen, und ein Silvesterfoto darf über den
#: Jahreswechsel rutschen, ohne verdächtig zu sein.
OUTLIER = timedelta(days=365)

#: So viel des Albums muss zusammenpassen, damit die Mehrheit als Wahrheit
#: taugt. Darunter ist es kein Album mit einem Ausreißer, sondern eine
#: Sammlung ohne gemeinsames Datum.
MAJORITY = 0.5

#: Unter so vielen Fotos ist "Mehrheit" kein belastbarer Begriff.
MIN_ALBUM = 8

#: Wie eng die Versätze beieinander liegen müssen, damit die Uhr als
#: durchgehend falsch gestellt gilt statt als zurückgefallen.
OFFSET_SPREAD = timedelta(days=2)

#: Kleinere Gruppen werden nicht gemeldet. Ein einzelnes Foto mit abweichendem
#: Datum ist meist ein falsch einsortiertes Bild, keine falsch gehende Uhr --
#: und der Bericht soll Entscheidungen ermöglichen, nicht Arbeit erzeugen.
MIN_GROUP = 3


@dataclass
class Suspicion:
    """Eine Kamera, die in einem Album aus der Reihe fällt."""

    album: str
    camera: str
    photo_ids: list[str] = field(default_factory=list)
    reference: datetime | None = None
    observed: list[datetime] = field(default_factory=list)
    #: Gesetzt, wenn alle Fotos um dieselbe Spanne danebenliegen.
    offset: timedelta | None = None

    @property
    def count(self) -> int:
        return len(self.photo_ids)

    @property
    def kind(self) -> str:
        return "versatz" if self.offset is not None else "zurueckgefallen"

    def proposal(self) -> str:
        if self.offset is not None:
            days = self.offset.total_seconds() / 86400
            return f"alle um {days:+.1f} Tage verschieben (Uhrzeit bleibt stimmig)"
        return ("Datum aus dem Album uebernehmen, Uhrzeit belassen "
                "-- die absolute Zeit ist verloren")


def _year_of(album: str) -> int | None:
    m = RE_YEAR.search(album or "")
    return int(m.group(1)) if m else None


def find(photos: list[dict]) -> list[Suspicion]:
    """Verdachtsfälle über alle Alben.

    `photos` sind Payload-Dicts mit `photo_id`, `folder_name`, `taken_at` und
    optional `exif.Model`.
    """
    by_album: dict[str, list[dict]] = defaultdict(list)
    for p in photos:
        album, stamp = p.get("folder_name"), p.get("taken_at")
        if album and stamp:
            by_album[album].append(p)

    out: list[Suspicion] = []
    for album, rows in sorted(by_album.items()):
        if len(rows) < MIN_ALBUM:
            continue
        parsed = []
        for p in rows:
            try:
                parsed.append((datetime.strptime(p["taken_at"][:19], "%Y-%m-%dT%H:%M:%S"), p))
            except (ValueError, TypeError):
                continue
        if len(parsed) < MIN_ALBUM:
            continue

        years = Counter(dt.year for dt, _ in parsed)
        top_year, n_top = years.most_common(1)[0]
        if n_top / len(parsed) < MAJORITY:
            # Kein gemeinsames Datum -- hier gibt es keine Mehrheit, gegen die
            # sich ein Ausreisser abheben koennte.
            continue
        # Der Albumname schlaegt die Mehrheit, wenn er ein Jahr nennt.
        named = _year_of(album)
        if named and years.get(named):
            top_year = named
        reference = median_time([dt for dt, _ in parsed if dt.year == top_year])

        strays: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
        for dt, p in parsed:
            if abs(dt - reference) <= OUTLIER:
                continue
            model = ((p.get("exif") or {}).get("Model") or "unbekannt").strip("\x00 ")
            strays[model].append((dt, p))

        for model, items in strays.items():
            if len(items) < MIN_GROUP:
                continue
            offsets = [reference - dt for dt, _ in items]
            spread = max(offsets) - min(offsets) if len(offsets) > 1 else timedelta(0)
            out.append(Suspicion(
                album=album,
                camera=model,
                photo_ids=[p.get("photo_id") or p.get("id") for _, p in items],
                reference=reference,
                observed=[dt for dt, _ in items],
                offset=median_offset(offsets) if spread <= OFFSET_SPREAD else None,
            ))
    out.sort(key=lambda s: -s.count)
    return out


def median_time(values: list[datetime]) -> datetime:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def median_offset(values: list[timedelta]) -> timedelta:
    return timedelta(seconds=median(v.total_seconds() for v in values))


def by_camera(suspicions: list[Suspicion]) -> dict[str, list[Suspicion]]:
    """Nach Kamera bündeln.

    Dieselbe Kamera in mehreren Alben ist das eigentliche Argument: ein
    einzelner Ausreißer kann ein falsch einsortiertes Foto sein, ein Gerät mit
    demselben Fehler über drei Alben hinweg nicht.
    """
    grouped: dict[str, list[Suspicion]] = defaultdict(list)
    for s in suspicions:
        grouped[s.camera].append(s)
    return dict(sorted(grouped.items(), key=lambda kv: -sum(s.count for s in kv[1])))
