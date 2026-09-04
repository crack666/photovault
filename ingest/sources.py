"""`sources.txt` lesen und schreiben, ohne sie umzuschreiben.

`load_sources()` im Scanner beantwortet die Frage „was wird eingelesen" --
sie wirft alles weg, was auskommentiert ist. Für eine Oberfläche ist das zu
wenig: gerade die stillgelegten Zeilen sind die interessanten, denn sie sind
die Ordner, die man *auch* haben könnte. In der gepflegten Datei dieses
Bestands steht hinter jeder die gefundene Bilderzahl.

Deshalb hier ein zweiter Blick auf dieselbe Datei: nicht „welche Pfade",
sondern „welche Zeilen". Beim Zurückschreiben wird nur das eine Zeichen
gesetzt oder entfernt, das eine Zeile stilllegt -- Reihenfolge, Kommentare,
Einrückung und handgeschriebene Notizen bleiben, wie sie sind.

Das ist kein Umweg, sondern der Punkt: die Datei bleibt von Hand pflegbar,
und wer sie im Editor bearbeitet hat, findet sie nach einem Klick in der
Oberfläche unverändert wieder.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

#: Eine stillgelegte Quelle: '#' direkt vor dem Pfad, mit beliebig viel Luft.
#: `#  /mnt/photo/Urlaub    # 1398 Bilder` ist eine, `# Ueberschrift` nicht.
_DISABLED = re.compile(r"^(\s*)#\s*(-?\s*/[^\s#][^#]*?)\s*(#.*)?$")

#: Eine aktive Zeile: Pfad, optional mit '-' davor und Kommentar dahinter.
_ACTIVE = re.compile(r"^(\s*)(-?\s*/[^\s#][^#]*?)\s*(#.*)?$")


@dataclass
class Entry:
    """Eine Zeile, die einen Pfad nennt."""

    line: int                 #: 0-basiert, wie im Dateiarray
    path: str
    exclude: bool             #: führendes '-' -- schließt aus statt ein
    enabled: bool             #: nicht auskommentiert
    note: str = ""            #: was hinter dem '#' am Zeilenende stand
    exists: bool | None = None
    photos: int | None = None  #: laut Index, wenn jemand nachgezählt hat


@dataclass
class SourcesFile:
    path: str
    lines: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)

    @property
    def active(self) -> list[Entry]:
        return [e for e in self.entries if e.enabled]


def _split(raw: str) -> tuple[str, bool] | None:
    """(Pfadteil, aktiv) oder None, wenn die Zeile keinen Pfad nennt."""
    m = _DISABLED.match(raw)
    if m:
        return m.group(2), False
    m = _ACTIVE.match(raw)
    if m:
        return m.group(2), True
    return None


def read(path: str = "sources.txt") -> SourcesFile:
    """Die Datei zeilenweise lesen -- alles bleibt erhalten."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out = SourcesFile(path=path, lines=lines)
    for i, raw in enumerate(lines):
        teil = _split(raw)
        if teil is None:
            continue
        text, enabled = teil
        exclude = text.startswith("-")
        p = text[1:].strip() if exclude else text.strip()
        if not p.startswith("/"):
            continue
        note = ""
        m = re.search(r"#(.*)$", raw)
        if m and not raw.lstrip().startswith("#"):
            note = m.group(1).strip()
        elif enabled is False:
            # Bei stillgelegten Zeilen steht der Hinweis hinter dem *zweiten* '#'.
            m2 = _DISABLED.match(raw)
            note = (m2.group(3) or "").lstrip("#").strip() if m2 else ""
        out.entries.append(Entry(line=i, path=p, exclude=exclude, enabled=enabled, note=note))
    return out


def toggle(lines: list[str], index: int, enabled: bool) -> list[str]:
    """Eine Zeile stilllegen oder wieder aufnehmen.

    Verändert genau ein Zeichen. Ist die Zeile schon im gewünschten Zustand,
    passiert nichts -- damit ein doppelter Klick nicht zwei '#' stapelt.
    """
    lines = list(lines)
    raw = lines[index]
    ist = _split(raw)
    if ist is None:
        raise ValueError(f"Zeile {index + 1} nennt keinen Pfad: {raw!r}")
    if ist[1] == enabled:
        return lines
    if enabled:
        lines[index] = re.sub(r"^(\s*)#\s*", r"\1", raw, count=1)
    else:
        m = re.match(r"^(\s*)", raw)
        lines[index] = f"{m.group(1)}#{raw[len(m.group(1)):]}"
    return lines


def add(lines: list[str], path: str, exclude: bool = False) -> list[str]:
    """Eine neue Zeile anhängen -- ans Ende, damit nichts verrutscht."""
    p = path.rstrip("/")
    if not p.startswith("/"):
        raise ValueError(f"Absoluter Pfad erwartet, nicht {path!r}")
    lines = list(lines)
    neu = f"-{p}" if exclude else p
    # Am Ende genau eine Leerzeile lassen, sonst waechst die Datei mit jedem
    # Zusatz um eine.
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(neu)
    lines.append("")
    return lines


def remove(lines: list[str], index: int) -> list[str]:
    """Eine Zeile ganz herausnehmen.

    Nur stilllegen genuegt nicht: eine Zeile, die auf einen Ordner zeigt, den
    es nicht gibt, ist kein Vorschlag mehr, sondern Muell. Sie stehenzulassen
    heisst, sie bei jedem Blick wieder zu lesen und wieder zu verwerfen.

    Der Zeilenkommentar geht mit -- er gehoert zur Zeile. Steht ueber ihr eine
    reine Kommentarzeile, bleibt die: sie kann sich auf den ganzen Abschnitt
    beziehen, und eine fremde Zeile zu loeschen waere schlimmer als eine
    stehenzulassen.
    """
    lines = list(lines)
    if not 0 <= index < len(lines):
        raise ValueError(f"Zeile {index + 1} gibt es nicht")
    if _split(lines[index]) is None:
        raise ValueError(f"Zeile {index + 1} nennt keinen Pfad: {lines[index]!r}")
    del lines[index]
    return lines


def write(path: str, lines: list[str]) -> None:
    """Erst daneben schreiben, dann umbenennen.

    Ein abgebrochener Schreibvorgang darf keine halbe Datei hinterlassen: an
    ihr haengt, was ueberhaupt eingelesen wird.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, path)
