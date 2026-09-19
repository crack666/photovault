"""Welche Ordner eingelesen werden -- aus der Oberfläche heraus.

`sources.txt` entscheidet, was überhaupt existiert: was nicht drinsteht,
kommt nicht in den Index, wird nicht gefunden und liegt auf keiner Karte. Bis
jetzt ging das nur im Editor, und das ist für eine Entscheidung dieser
Tragweite zu versteckt.

Drei Dinge sind dabei wichtiger als die Bequemlichkeit:

*Die Datei bleibt die Wahrheit.* Geschrieben wird zeilenweise, ein Haken
setzt oder entfernt genau ein `#`. Wer sie von Hand pflegt -- und in dieser
steht hinter jeder Zeile die Bilderzahl -- findet sie unverändert wieder.

*Vor dem Speichern der Blick.* Ein Haken kann einen stundenlangen Lauf
auslösen. Was er bedeutet, muss vorher dastehen, nicht hinterher.

*Abwählen löscht nichts.* Es heißt „nicht mehr nachladen", nicht „aus dem
Index nehmen". Beides zu verwechseln wäre die teuerste Verwechslung, die
diese Oberfläche anbieten kann -- deshalb sagt die Antwort, wie viele Fotos
der abgewählte Ordner im Index noch hat, und wie man sie los wird.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import PHOTOS, client, visible
from ingest import sources as src

logger = logging.getLogger(__name__)
router = APIRouter()

FILE = os.environ.get("PHOTOVAULT_SOURCES", "sources.txt")


def _in_der_bibliothek(pfad: str) -> str:
    """Den Pfad zurueckgeben -- oder abweisen, weil er draussen liegt.

    `browse` nahm jeden absoluten Pfad. Die Brotkrumen im Waehler bieten
    ausdruecklich `/` an, also war von der Jobs-Seite aus die ganze Maschine
    durchsuchbar: `/home/<name>/.ssh` listet sich genauso wie ein
    Fotoordner. Und `add` nahm ebenso jeden Pfad -- damit haette der
    naechste Lauf die ganze Platte eingelesen.

    Die Schranke ist nicht neu, sie wurde hier nur nicht benutzt:
    `photo_root()` gibt es fuer das endgueltige Loeschen und fuer die
    Albumliste, und ihr Docstring nennt `/etc/passwd` als das, was nicht
    dazugehoert. Erreichbar ueber CORS ist die Schnittstelle fuer
    `localhost:3000` mit -- ein Lesezugriff von aussen ist also nicht bloss
    theoretisch.

    Ist keine Wurzel bestimmbar, wird durchgelassen. Anders als beim
    Loeschen waere fail-closed hier der Einrichtungstod: ohne Quellen gibt
    es keine Wurzel, und ohne Waehler kaeme man nie zur ersten Quelle.
    Wer eine zweite Bibliothek woanders hat, setzt `PHOTOVAULT_PHOTO_ROOT`
    -- die Variable gibt es schon. Im Docker-Verbund gilt
    `PHOTOVAULT_BROWSE_ROOT` (`/host`, die Laufwerke, nichts vom Container
    selbst) -- und zwar *vor* der Bibliothekswurzel: die ist das gemeinsame
    Elternverzeichnis der Quellen, und nach der ersten Quelle auf `D:` liesse
    sie keine zweite auf `C:` mehr zu. Der Waehler darf ueberall auf den
    Laufwerken hin; was geloescht werden darf, entscheidet weiter die Wurzel.
    """
    from ingest.spaces import browse_root, photo_root, under_root

    root = browse_root() or photo_root()
    if not root:
        return pfad
    if not under_root(pfad, root):
        raise HTTPException(
            403, f"Ausserhalb der Bibliothek ({root}): {pfad}. "
                 f"PHOTOVAULT_PHOTO_ROOT verschiebt die Grenze.")
    return pfad

#: Obergrenze fuer den Trockenlauf. Ein Zaehllauf ueber ein NAS kostet Zeit;
#: mehr als das braucht niemand, um eine Entscheidung zu treffen.
PREVIEW_CAP = 200_000


class ToggleRequest(BaseModel):
    line: int
    enabled: bool


class AddRequest(BaseModel):
    path: str
    exclude: bool = False


def _count_in_index(q, prefix: str) -> int:
    """Wie viele Fotos aus diesem Ordner schon im Index stehen.

    Ueber das Bereichsfeld geht das nicht: `space` ist nur die erste Ebene.
    Also gezaehlt, was unter dem Pfad liegt -- einmal je Zeile, und die Zahl
    steht danach in der Oberflaeche, damit „abwaehlen" nicht wie „loeschen"
    aussieht.
    """
    from qdrant_client.models import Filter, MatchText

    try:
        return q.count(
            collection_name=PHOTOS, exact=True,
            count_filter=visible(Filter(must=[
                {"key": "file_path", "match": MatchText(text=prefix)}
            ])),
        ).count
    except Exception:
        # Ohne Volltextindex auf file_path kann Qdrant das ablehnen. Dann
        # lieber keine Zahl als eine erfundene.
        return -1


@router.get("")
def list_sources() -> dict:
    """Die Datei, wie sie ist -- Zeile für Zeile."""
    if not Path(FILE).exists():
        raise HTTPException(404, f"{FILE} gibt es nicht")
    s = src.read(FILE)
    q = client()
    out = []
    for e in s.entries:
        out.append({
            "line": e.line,
            "path": e.path,
            "exclude": e.exclude,
            "enabled": e.enabled,
            "note": e.note,
            "exists": Path(e.path).is_dir(),
            "photos": _count_in_index(q, e.path),
        })
    return {
        "file": FILE,
        "entries": out,
        "active": len(s.active),
        "hint": "Abwählen heißt „nicht mehr nachladen“ — im Index bleiben die "
                "Fotos, bis sie aufgeräumt werden.",
    }


@router.post("/toggle")
def toggle_source(req: ToggleRequest) -> dict:
    s = src.read(FILE)
    if not any(e.line == req.line for e in s.entries):
        raise HTTPException(400, f"Zeile {req.line + 1} nennt keinen Pfad")
    try:
        lines = src.toggle(s.lines, req.line, req.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    src.write(FILE, lines)
    return {"ok": True, "line": req.line, "enabled": req.enabled}


#: Was in einer frisch angelegten Datei ueber den Eintraegen steht. Der
#: Setup-Wizard legt sie mit dem ersten Haken an -- vorher gab es die Datei
#: nur, wenn jemand sie im Editor geschrieben hatte.
NEW_FILE_HEAD = [
    "# Welche Verzeichnisse eingelesen werden. Eine Zeile je Ordner,",
    "# ein '-' davor schliesst aus, ein '#' davor legt still.",
    "",
]


def _read_or_new() -> src.SourcesFile:
    """Die Datei, oder eine leere mit Kopfzeilen, wenn es sie noch nicht gibt."""
    if Path(FILE).exists():
        return src.read(FILE)
    return src.SourcesFile(path=FILE, lines=list(NEW_FILE_HEAD))


@router.post("/add")
def add_source(req: AddRequest) -> dict:
    p = _in_der_bibliothek(req.path.rstrip("/"))
    if not Path(p).is_dir():
        raise HTTPException(400, f"Kein Verzeichnis: {p}")
    s = _read_or_new()
    if any(e.path.rstrip("/") == p and e.exclude == req.exclude for e in s.entries):
        raise HTTPException(409, f"Steht schon drin: {p}")
    try:
        lines = src.add(s.lines, p, exclude=req.exclude)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    src.write(FILE, lines)
    return {"ok": True, "path": p, "exclude": req.exclude}


class RemoveRequest(BaseModel):
    line: int


@router.post("/remove")
def remove_source(req: RemoveRequest) -> dict:
    """Eine Zeile ganz herausnehmen.

    Vor allem fuer Zeilen, die auf einen Ordner zeigen, den es nicht gibt:
    stilllegen aendert dort nichts, sie bleiben Muell, den man bei jedem
    Blick wieder lesen und wieder verwerfen muss.

    Gemeldet wird, was in der Zeile stand -- damit ein Fehlgriff nicht
    heisst, dass der Pfad verloren ist. Wiederherstellen geht ueber
    "Ordner hinzufuegen".
    """
    s = src.read(FILE)
    treffer = next((e for e in s.entries if e.line == req.line), None)
    if treffer is None:
        raise HTTPException(400, f"Zeile {req.line + 1} nennt keinen Pfad")
    try:
        lines = src.remove(s.lines, req.line)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    src.write(FILE, lines)
    return {
        "ok": True,
        "removed": treffer.path,
        "exclude": treffer.exclude,
        "was_enabled": treffer.enabled,
        "note": treffer.note,
    }


#: Wieviele Dateien je Ordner hoechstens gezaehlt werden. Ueber ein
#: Netzlaufwerk kostet jeder Eintrag Zeit; fuer die Frage "liegen hier die
#: Fotos?" genuegt "mindestens so viele".
BROWSE_CAP = 400


@router.get("/browse")
def browse(path: str = "") -> dict:
    """Unterordner eines Pfades -- damit man nicht tippen muss, was existiert.

    Der eigentliche Grund ist nicht Bequemlichkeit: von 34 Zeilen dieser
    Datei zeigen zwölf auf Ordner, die es nicht (mehr) gibt. Getippte Pfade
    veralten, und eine tote Zeile sieht im Editor wie eine wirksame aus. Wer
    nur auswählen kann, was da ist, legt diese Zeilen nicht an.

    Je Ordner steht dabei, wieviele Bilder direkt darin liegen und ob es
    Unterordner gibt -- sonst klickt man sich blind durch einen Baum.
    """
    from ingest.spaces import browse_root, photo_root

    # Ohne Pfad nicht `/`, sondern die Bibliothekswurzel: das ist der Ort,
    # an dem der Waehler anfangen soll, und `/` war nie eine sinnvolle
    # Antwort auf "welchen Fotoordner meinst du". Im Verbund: die Laufwerke
    # unter /host -- auch mit Quellen, sonst kaeme man nie auf die zweite Platte.
    p = Path(_in_der_bibliothek(path) if path else (browse_root() or photo_root() or "/"))
    if not p.is_absolute():
        raise HTTPException(400, "Absoluter Pfad erwartet")
    if not p.is_dir():
        raise HTTPException(404, f"Kein Verzeichnis: {p}")

    from ingest.scanner import _is_image

    dirs = []
    try:
        for kind in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            if not kind.is_dir() or kind.name.startswith("."):
                continue
            bilder, unter, angeschnitten = 0, False, False
            try:
                for i, e in enumerate(kind.iterdir()):
                    if i >= BROWSE_CAP:
                        angeschnitten = True
                        break
                    if e.is_dir():
                        unter = True
                    elif _is_image(e):
                        bilder += 1
            except OSError:
                pass
            dirs.append({
                "name": kind.name,
                "path": str(kind),
                "images": bilder,
                "truncated": angeschnitten,
                "has_subdirs": unter,
            })
    except OSError as e:
        raise HTTPException(502, f"Nicht lesbar: {e}") from e

    s = _read_or_new()
    drin = {e.path.rstrip("/"): e for e in s.entries}
    for d in dirs:
        e = drin.get(d["path"].rstrip("/"))
        d["listed"] = None if e is None else ("exclude" if e.exclude else "include")
        d["enabled"] = None if e is None else e.enabled

    # `root` mitgeben und `parent` an der Wurzel abschneiden: sonst zeigt
    # die Oberflaeche einen Weg nach oben, den die Schranke gleich mit 403
    # beantwortet. Ein Knopf, der zuverlaessig scheitert, ist schlimmer als
    # keiner.
    root = browse_root() or photo_root()
    oben = None if p.parent == p else str(p.parent)
    if root and str(p).rstrip("/") == root.rstrip("/"):
        oben = None

    return {
        "path": str(p),
        "parent": oben,
        "root": root or None,
        "dirs": dirs,
        "cap": BROWSE_CAP,
    }


@router.get("/preview")
def preview() -> dict:
    """Was ein Lauf mit dem aktuellen Stand finden würde.

    Zählt Dateien, schreibt nichts. Über ein Netzlaufwerk dauert das je nach
    Menge Sekunden bis Minuten -- deshalb steht es hinter einem eigenen
    Knopf und läuft nicht bei jedem Blick auf die Liste mit.
    """
    from collections import Counter

    from ingest.scanner import NASScanner

    s = _read_or_new()
    include = [e.path for e in s.active if not e.exclude]
    exclude = [e.path for e in s.active if e.exclude]
    if not include:
        return {"total": 0, "per_source": [], "include": [], "exclude": exclude,
                "note": "Keine Quelle aktiv — ein Lauf würde nichts finden."}

    fehlend = [p for p in include if not Path(p).is_dir()]
    try:
        files = NASScanner(include, exclude=exclude).scan()
    except Exception as e:
        logger.exception("Trockenlauf fehlgeschlagen")
        raise HTTPException(502, f"Zählen fehlgeschlagen: {type(e).__name__}: {e}") from e

    per: Counter = Counter()
    roots = [p.rstrip("/") for p in include]
    for f in files[:PREVIEW_CAP]:
        root = max((r for r in roots if f.startswith(r + "/")), key=len, default="(außerhalb)")
        per[root] += 1

    q = client()
    return {
        "total": len(files),
        "per_source": [
            {"path": r, "found": per.get(r, 0), "in_index": _count_in_index(q, r)}
            for r in roots
        ],
        "include": include,
        "exclude": exclude,
        "missing": fehlend,
        "note": "Nichts geschrieben. „gefunden“ sind Dateien auf der Platte, "
                "„im Index“ ist, was PhotoVault davon schon kennt.",
    }
