"""Aufnahmezeit ins EXIF zurückschreiben.

Zwei Anlässe:

1. **Fehlt.** WhatsApp und die meisten Weiterleitungen strippen EXIF. Das
   Datum steckt dann nur im Dateinamen (`IMG-20181021-WA0081.jpg`), und jeder
   Betrachter zeigt die Datei als undatiert. Wer es einmal hineinschreibt,
   repariert das Archiv selbst -- nicht nur den Index.
2. **Ist falsch.** Alte Kameras verlieren beim Batteriewechsel ihre Uhr. Im
   Bestand tauchen dieselben Geräte über mehrere Alben hinweg mit versetztem
   Datum auf.

Warum jeder geschriebene Wert eine Herkunftsnotiz bekommt:

Schreiben wir ein aus dem Dateinamen abgeleitetes Datum als
`DateTimeOriginal`, liest der nächste Lauf es als EXIF und vergibt Vertrauen
1.0 -- unsere Schätzung wäre damit zur Messung befördert, und niemand könnte
das später noch unterscheiden. Die Notiz in `UserComment` hält fest, woher der
Wert kam und was vorher dort stand, sodass sich jede Änderung zurückverfolgen
und rückgängig machen lässt.

Geschrieben wird ohne Neukodierung: `piexif` tauscht nur den EXIF-Block aus,
die Bilddaten bleiben Byte für Byte dieselben.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: EXIF-Tags fuer Zeitstempel (piexif.ExifIFD / ImageIFD).
_DT_ORIGINAL = 36867
_DT_DIGITIZED = 36868
_DT_IMAGE = 306
_USER_COMMENT = 37510

MARKER = "photovault"
#: photovault:src=filename;prev=2009:01:16 01:46:18;mtime=2013-07-13T15:07:22
_RE_NOTE = re.compile(r"photovault:src=([a-z_]+)(?:;prev=([^;]*?))?(?:;mtime=([^;]*))?(?:;|$)")

#: Nur diese Formate koennen wir verlustfrei zurueckschreiben.
WRITABLE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}


class ExifWriteError(RuntimeError):
    pass


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def _parse(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    try:
        return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def read_note(file_path: str) -> tuple[str, str, str] | None:
    """Herkunftsnotiz lesen: `(quelle, vorheriger_wert, urspruengliche_mtime)`."""
    import piexif

    try:
        exif = piexif.load(file_path)
    except Exception:
        return None
    raw = exif.get("Exif", {}).get(_USER_COMMENT)
    if not raw:
        return None
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    m = _RE_NOTE.search(text)
    return (m.group(1), m.group(2) or "", m.group(3) or "") if m else None


def read_capture_time(file_path: str) -> datetime | None:
    import piexif

    try:
        exif = piexif.load(file_path)
    except Exception:
        return None
    for ifd, tag in (("Exif", _DT_ORIGINAL), ("Exif", _DT_DIGITIZED), ("0th", _DT_IMAGE)):
        dt = _parse(exif.get(ifd, {}).get(tag))
        if dt:
            return dt
    return None


def write_capture_time(
    file_path: str,
    when: datetime,
    source: str,
    dry_run: bool = True,
    overwrite: bool = False,
    preserve_mtime: bool = True,
) -> dict:
    """Aufnahmezeit setzen.

    `source` nennt die Herkunft (`filename`, `album`, `offset`, …) und landet
    in der Notiz. Ein vorhandener Wert wird nur mit `overwrite=True` ersetzt --
    und dann in der Notiz aufbewahrt, damit die Änderung umkehrbar bleibt.

    **Die Änderungszeit der Datei wird erhalten.** Das ist keine Kosmetik: bei
    WhatsApp-Dateien ist sie die *einzige* Quelle der Uhrzeit -- der Dateiname
    nennt nur den Tag. Wer sie beim Schreiben verliert, hat genau einen
    Versuch und kann einen Fehler nicht mehr korrigieren, weil die Grundlage
    danach fehlt. Sie wird deshalb vorher gelesen, hinterher zurueckgesetzt
    und zusaetzlich in der Notiz festgehalten -- falls `utime` auf einem
    anderen Mount einmal nicht durchgeht.

    Gibt zurueck, was passiert ist (oder passieren wuerde); schreibt bei
    `dry_run` nichts.
    """
    from pathlib import Path

    import piexif

    result = {"path": file_path, "written": False, "reason": "", "previous": None,
              "new": _fmt(when)}
    if Path(file_path).suffix.lower() not in WRITABLE_SUFFIXES:
        result["reason"] = "Format nicht verlustfrei beschreibbar"
        return result

    try:
        exif = piexif.load(file_path)
    except Exception as e:
        result["reason"] = f"EXIF nicht lesbar: {e}"
        return result

    existing = _parse(exif.get("Exif", {}).get(_DT_ORIGINAL))
    if existing:
        result["previous"] = _fmt(existing)
        if not overwrite:
            result["reason"] = "hat bereits eine Aufnahmezeit"
            return result
        if existing == when:
            result["reason"] = "Wert ist schon korrekt"
            return result

    stamp = _fmt(when).encode("ascii")
    exif.setdefault("Exif", {})[_DT_ORIGINAL] = stamp
    exif["Exif"].setdefault(_DT_DIGITIZED, stamp)
    exif.setdefault("0th", {})[_DT_IMAGE] = stamp

    times = None
    if preserve_mtime:
        try:
            st = os.stat(file_path)
            times = (st.st_atime, st.st_mtime)
            result["mtime"] = datetime.fromtimestamp(
                st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        except OSError as e:
            logger.warning("Aenderungszeit von %s nicht lesbar: %s", file_path, e)

    note = f"{MARKER}:src={source}"
    if result["previous"]:
        note += f";prev={result['previous']}"
    if result.get("mtime"):
        note += f";mtime={result['mtime']}"
    exif["Exif"][_USER_COMMENT] = note.encode("utf-8")
    # Thumbnails im EXIF sind oft beschaedigt und lassen piexif scheitern;
    # gebraucht werden sie nicht.
    exif.pop("thumbnail", None)
    exif["1st"] = {}

    if dry_run:
        result["reason"] = "Trockenlauf"
        return result

    try:
        piexif.insert(piexif.dump(exif), file_path)
    except Exception as e:
        raise ExifWriteError(f"{file_path}: {e}") from e

    if times is not None:
        try:
            os.utime(file_path, times)
            result["mtime_restored"] = True
        except OSError as e:
            # Kein Grund, den Wert zu verwerfen -- er steht in der Notiz.
            logger.warning("Aenderungszeit von %s nicht wiederherstellbar: %s", file_path, e)
            result["mtime_restored"] = False

    check = read_capture_time(file_path)
    if check != when:
        raise ExifWriteError(
            f"{file_path}: geschrieben, aber Gegenprobe ergibt {check!r} statt {when!r}"
        )
    result["written"] = True
    result["reason"] = "geschrieben"
    return result


def revert(file_path: str, dry_run: bool = True) -> dict:
    """Eine von uns geschriebene Änderung zurücknehmen.

    Nur moeglich, wenn die Notiz einen vorherigen Wert nennt -- wo vorher
    nichts stand, gibt es nichts wiederherzustellen; dort bleibt der Wert
    stehen, und die Notiz sagt weiterhin, woher er kam.
    """
    result = {"path": file_path, "reverted": False, "reason": ""}
    note = read_note(file_path)
    if not note:
        result["reason"] = "nicht von photovault geschrieben"
        return result
    _, prev, _mtime = note
    if not prev:
        result["reason"] = "vorher stand dort kein Datum"
        return result
    when = _parse(prev)
    if not when:
        result["reason"] = f"vorheriger Wert unlesbar: {prev!r}"
        return result
    if dry_run:
        result["reason"] = f"Trockenlauf -- wuerde auf {prev} zuruecksetzen"
        return result
    out = write_capture_time(file_path, when, source="revert", dry_run=False, overwrite=True)
    result["reverted"] = out["written"]
    result["reason"] = out["reason"]
    return result
