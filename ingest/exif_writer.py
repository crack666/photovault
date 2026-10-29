"""Aufnahmezeit und Captions ins EXIF zurückschreiben.

Drei Anlässe:

1. **Datum fehlt.** WhatsApp und die meisten Weiterleitungen strippen EXIF. Das
   Datum steckt dann nur im Dateinamen (`IMG-20181021-WA0081.jpg`), und jeder
   Betrachter zeigt die Datei als undatiert. Wer es einmal hineinschreibt,
   repariert das Archiv selbst -- nicht nur den Index.
2. **Datum ist falsch.** Alte Kameras verlieren beim Batteriewechsel ihre Uhr.
3. **Caption.** Der Satz lebt sonst nur im Index. Ohne ihn in der Datei ist er
   nach einem Umzug auf die nächste Platte weg, und Explorer/Lightroom zeigen
   weiter nichts. Der Text liegt in `ImageDescription` und `XPComment`
   (Windows); `UserComment` bleibt die Herkunftsnotiz, nicht der Satz.

Warum jeder geschriebene Wert eine Herkunftsnotiz bekommt:

Schreiben wir ein aus dem Dateinamen abgeleitetes Datum als
`DateTimeOriginal`, liest der nächste Lauf es als EXIF und vergibt Vertrauen
1.0 -- unsere Schätzung wäre damit zur Messung befördert. Dieselbe Falle
droht bei Captions: ein LLM-Satz ohne Marker sähe aus wie eine
Kamerabeschreibung. Die Notiz in `UserComment` hält fest, woher Datum und
Satz kamen.

Zeitzone: `DateTimeOriginal` hat keine und gilt als **Ortszeit**. Der Index
haelt Dateizeiten in UTC. Eine Zeit mit Zone wird deshalb vor dem Schreiben in
die Ortszeit (`ingest.localtime.zone`) umgerechnet, und die Abweichung von UTC
landet in `OffsetTimeOriginal` -- dann ist der Zeitpunkt auch fuer Programme
eindeutig, die die Zone kennen wollen (Issue #43).

Geschrieben wird ohne Neukodierung: `piexif` tauscht nur den EXIF-Block aus,
die Bilddaten bleiben Byte für Byte dieselben.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: EXIF-Tags fuer Zeitstempel (piexif.ExifIFD / ImageIFD).
_DT_ORIGINAL = 36867
_DT_DIGITIZED = 36868
_DT_IMAGE = 306
_USER_COMMENT = 37510
_IMAGE_DESCRIPTION = 270
_XP_COMMENT = 0x9C9C  # 40092, Windows-Kommentar (UTF-16LE)
#: OffsetTime (zu 306), OffsetTimeOriginal, OffsetTimeDigitized -- EXIF 2.31.
_OFFSET_IMAGE = 36880
_OFFSET_ORIGINAL = 36881
_OFFSET_DIGITIZED = 36882

MARKER = "photovault"
#: photovault:src=filename+mtime;prev=2009:01:16 01:46:18;mtime=2013-07-13T15:07:22Z;cap=llm
#:
#: `src=filename` heisst: Tag *und* Uhrzeit aus dem Namen. `src=filename+mtime`:
#: Tag aus dem Namen, Uhrzeit aus der Dateizeit -- der Name kennt keine Uhr.
#: `mtime` ist UTC (aeltere Notizen ohne `Z`, gemeint ist dasselbe).
_RE_NOTE = re.compile(r"photovault:src=([a-z_+]+)(?:;prev=([^;]*?))?(?:;mtime=([^;]*))?(?:;|$)")
_RE_FIELD = re.compile(r"([a-z_]+)=([^;]*)")
_NOTE_ORDER = ("src", "prev", "mtime", "cap")

#: Nur diese Formate koennen wir verlustfrei zurueckschreiben.
WRITABLE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}


class ExifWriteError(RuntimeError):
    pass


_RE_BAD_TAG = re.compile(r"(\d+) in (\w+) IFD")


def _safe_dump(exif: dict) -> bytes:
    """piexif.dump, kaputte Kameratags nacheinander entfernen.

    Manche Kameras schreiben SceneType (41729) als int statt als Byte. Ein
    einzelner solcher Wert darf das Zurueckschreiben nicht verhindern.
    """
    import piexif

    data = {}
    for key, value in exif.items():
        data[key] = dict(value) if isinstance(value, dict) else value
    data.pop("thumbnail", None)
    data["1st"] = {}
    last: Exception | None = None
    for _ in range(12):
        try:
            return piexif.dump(data)
        except ValueError as e:
            last = e
            match = _RE_BAD_TAG.search(str(e))
            if not match:
                raise
            tag, ifd = int(match.group(1)), match.group(2)
            bucket = data.get(ifd)
            if isinstance(bucket, dict) and tag in bucket:
                del bucket[tag]
                continue
            raise
    assert last is not None
    raise last


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def _fmt_offset(dt: datetime) -> str:
    """`+02:00` -- das Format von OffsetTimeOriginal."""
    off = dt.utcoffset()
    assert off is not None
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_offset(raw) -> timedelta | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    m = re.fullmatch(r"\s*([+-])(\d{2}):(\d{2})\s*", str(raw).strip("\x00"))
    if not m:
        return None
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return -delta if m.group(1) == "-" else delta


def local_stamp(when: datetime) -> datetime:
    """Was in `DateTimeOriginal` gehoert: Ortszeit, bewusst (mit Zone).

    Ohne Zone gilt `when` schon als Ortszeit -- von Hand eingegeben oder aus
    einer Kamera -- und bleibt, wie es ist.
    """
    if when.tzinfo is None:
        return when
    from ingest.localtime import to_local

    return to_local(when)


def mtime_note(ts: float) -> str:
    """Dateizeit fuer die Notiz: UTC, mit `Z`, damit es niemand verwechselt."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    try:
        return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _decode_comment(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, bytes):
        if raw[:8] in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00"):
            raw = raw[8:]
        text = raw.decode("utf-8", "ignore")
    else:
        text = str(raw)
    return text.strip("\x00 ")


def note_fields(raw) -> dict[str, str]:
    """Schlüssel aus `photovault:k=v;k=v`. Ohne Marker: leeres Dict."""
    text = _decode_comment(raw)
    if f"{MARKER}:" not in text:
        return {}
    body = text.split(f"{MARKER}:", 1)[1]
    return {m.group(1): m.group(2) for m in _RE_FIELD.finditer(body)}


def note_text(fields: dict[str, str]) -> str:
    parts = []
    seen = set()
    for key in _NOTE_ORDER:
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        parts.append(f"{key}={value}")
        seen.add(key)
    for key, value in fields.items():
        if key in seen or not (value or "").strip():
            continue
        parts.append(f"{key}={value.strip()}")
    return f"{MARKER}:" + ";".join(parts) if parts else ""


def _merge_user_comment(raw, updates: dict[str, str]) -> bytes:
    """Photovault-Felder setzen, fremden UserComment-Text behalten."""
    text = _decode_comment(raw)
    prefix = ""
    fields: dict[str, str] = {}
    if f"{MARKER}:" in text:
        prefix, rest = text.split(f"{MARKER}:", 1)
        fields = note_fields(f"{MARKER}:{rest}")
        prefix = prefix.strip()
    elif text:
        prefix = text
    for key, value in updates.items():
        if value:
            fields[key] = value
        elif key in fields:
            del fields[key]
    note = note_text(fields)
    combined = " ".join(p for p in (prefix, note) if p)
    return combined.encode("utf-8")


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
    fields = note_fields(raw)
    if not fields.get("src"):
        text = _decode_comment(raw)
        m = _RE_NOTE.search(text)
        return (m.group(1), m.group(2) or "", m.group(3) or "") if m else None
    return (fields.get("src") or "", fields.get("prev") or "", fields.get("mtime") or "")


def read_capture_instant(file_path: str) -> datetime | None:
    """`DateTimeOriginal` plus `OffsetTimeOriginal` als bewusster Zeitpunkt.

    Ohne Offset-Tag: `None` -- welcher Zeitpunkt gemeint ist, sagt die Datei
    dann nicht.
    """
    import piexif

    try:
        exif = piexif.load(file_path)
    except Exception:
        return None
    ifd = exif.get("Exif", {})
    dt = _parse(ifd.get(_DT_ORIGINAL))
    off = _parse_offset(ifd.get(_OFFSET_ORIGINAL))
    if dt is None or off is None:
        return None
    return dt.replace(tzinfo=timezone(off))


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
    keep_prev: bool = False,
) -> dict:
    """Aufnahmezeit setzen.

    `source` nennt die Herkunft (`filename`, `filename+mtime`, `album`,
    `offset`, …) und landet in der Notiz. Ein vorhandener Wert wird nur mit
    `overwrite=True` ersetzt -- und dann in der Notiz aufbewahrt, damit die
    Änderung umkehrbar bleibt. `keep_prev=True` korrigiert einen Wert, den wir
    selbst geschrieben haben: dann bleibt der `prev` der vorhandenen Notiz
    stehen, statt unseren eigenen Fehler als Ausgangszustand festzuhalten.

    **Zeitzone.** `when` mit Zone (der Index haelt UTC) wird in Ortszeit
    geschrieben, und `OffsetTimeOriginal` nennt die Abweichung. `when` ohne
    Zone gilt schon als Ortszeit. Die Gegenprobe liest beides zurueck und
    verlangt denselben Zeitpunkt, nicht nur dieselben Ziffern.

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

    local = local_stamp(when)
    naive = local.replace(tzinfo=None)
    offset = _fmt_offset(local) if local.tzinfo is not None else None
    result = {"path": file_path, "written": False, "reason": "", "previous": None,
              "new": _fmt(naive), "offset": offset}
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
        had_offset = _parse_offset(exif.get("Exif", {}).get(_OFFSET_ORIGINAL))
        if existing == naive and (offset is None or (
                had_offset is not None and had_offset == local.utcoffset())):
            result["reason"] = "Wert ist schon korrekt"
            return result

    stamp = _fmt(naive).encode("ascii")
    ifd = exif.setdefault("Exif", {})
    ifd[_DT_ORIGINAL] = stamp
    digitized_ours = _DT_DIGITIZED not in ifd
    ifd.setdefault(_DT_DIGITIZED, stamp)
    exif.setdefault("0th", {})[_DT_IMAGE] = stamp
    # Offset gehoert zum Wert, den wir gerade setzen. Ohne bekannte Zone wird
    # ein alter Offset entfernt -- er wuerde sonst eine andere Uhr behaupten.
    for tag, ours in ((_OFFSET_ORIGINAL, True), (_OFFSET_IMAGE, True),
                      (_OFFSET_DIGITIZED, digitized_ours)):
        if not ours:
            continue
        if offset:
            ifd[tag] = offset.encode("ascii")
        else:
            ifd.pop(tag, None)

    snap = None
    if preserve_mtime:
        try:
            from ingest.filetimes import snapshot

            snap = snapshot(file_path)
            result["mtime"] = mtime_note(snap["mtime"])
        except OSError as e:
            logger.warning("Aenderungszeit von %s nicht lesbar: %s", file_path, e)

    existing_note = exif.get("Exif", {}).get(_USER_COMMENT)
    old_fields = note_fields(existing_note)
    updates = {"src": source}
    if keep_prev:
        updates["prev"] = old_fields.get("prev", "")
    elif result["previous"]:
        updates["prev"] = result["previous"]
    # Die erste festgehaltene Dateizeit bleibt: sie ist die Quelle der Uhr.
    # Ein spaeterer Lauf nach einem Kopieren saehe nur noch die Kopierzeit.
    if result.get("mtime") and not old_fields.get("mtime"):
        updates["mtime"] = result["mtime"]
    exif.setdefault("Exif", {})[_USER_COMMENT] = _merge_user_comment(existing_note, updates)
    if dry_run:
        result["reason"] = "Trockenlauf"
        return result

    try:
        piexif.insert(_safe_dump(exif), file_path)
    except Exception as e:
        raise ExifWriteError(f"{file_path}: {e}") from e

    if snap is not None:
        from ingest.filetimes import restore

        restored = restore(file_path, snap)
        result["mtime_restored"] = restored["mtime"]
        result["birth_restored"] = restored["birth"]

    check = read_capture_time(file_path)
    if check != naive:
        raise ExifWriteError(
            f"{file_path}: geschrieben, aber Gegenprobe ergibt {check!r} statt {naive!r}"
        )
    if when.tzinfo is not None:
        # Ziffern allein reichen nicht: 17:51 statt 19:51 waere auch
        # "derselbe Text". Der Zeitpunkt aus Ortszeit + Offset muss stimmen.
        instant = read_capture_instant(file_path)
        if instant is None or instant != when:
            raise ExifWriteError(
                f"{file_path}: geschrieben, aber Gegenprobe ergibt Zeitpunkt "
                f"{instant!r} statt {when!r}"
            )
    result["written"] = True
    result["reason"] = "geschrieben"
    return result


def _as_bytes(raw) -> bytes:
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, (tuple, list)):
        if raw and isinstance(raw[0], int) and max(raw) > 255:
            return b"".join(int(x).to_bytes(2, "little") for x in raw)
        return bytes(raw)
    return str(raw).encode("utf-8")


def _decode_description(raw) -> str:
    if not raw:
        return ""
    data = _as_bytes(raw)
    for enc in ("utf-8", "utf-16le", "latin-1"):
        try:
            text = data.decode(enc)
            if enc != "utf-16le" and "\x00" in text[1:]:
                continue
            return text.strip("\x00 ")
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "ignore").strip("\x00 ")


def _decode_xp(raw) -> str:
    if not raw:
        return ""
    return _as_bytes(raw).decode("utf-16le", "ignore").strip("\x00 ")


def read_caption(file_path: str) -> str | None:
    """Bildbeschreibung aus ImageDescription, sonst Windows-XPComment."""
    import piexif

    try:
        exif = piexif.load(file_path)
    except Exception:
        return None
    zeroth = exif.get("0th") or {}
    for tag, decoder in (
        (_IMAGE_DESCRIPTION, _decode_description),
        (_XP_COMMENT, _decode_xp),
    ):
        text = decoder(zeroth.get(tag))
        if text:
            return text
    return None


def write_caption(
    file_path: str,
    caption: str,
    source: str = "llm",
    dry_run: bool = True,
    overwrite: bool = False,
    preserve_mtime: bool = True,
) -> dict:
    """Caption in ImageDescription + XPComment schreiben.

    Eine fremde Beschreibung (Kamera, Lightroom) bleibt, solange `overwrite`
    nicht gesetzt ist. Haben *wir* den Satz geschrieben (`photovault:cap=`),
    darf ein erneuter Lauf ihn ersetzen — sonst würde ein besserer Prompt
    die Datei nie mehr erreichen.

    `UserComment` bekommt nur die Herkunft (`cap=llm`), nicht den Satz.
    Eine vorhandene Datumsnotiz (`src=filename`) bleibt stehen.
    """
    from pathlib import Path

    import piexif

    caption = (caption or "").strip()
    result = {"path": file_path, "written": False, "reason": "", "previous": None}
    if not caption:
        result["reason"] = "Caption ist leer"
        return result
    if Path(file_path).suffix.lower() not in WRITABLE_SUFFIXES:
        result["reason"] = "Format nicht verlustfrei beschreibbar"
        return result

    try:
        exif = piexif.load(file_path)
    except Exception as e:
        result["reason"] = f"EXIF nicht lesbar: {e}"
        return result

    zeroth = exif.setdefault("0th", {})
    existing = _decode_description(zeroth.get(_IMAGE_DESCRIPTION)) or _decode_xp(zeroth.get(_XP_COMMENT))
    result["previous"] = existing or None

    fields = note_fields(exif.get("Exif", {}).get(_USER_COMMENT))
    ours = bool(fields.get("cap"))
    if existing and not ours and not overwrite:
        result["reason"] = "hat bereits eine Bildbeschreibung"
        return result
    if existing == caption and fields.get("cap") == source:
        result["reason"] = "Wert ist schon korrekt"
        return result

    snap = None
    if preserve_mtime:
        try:
            from ingest.filetimes import snapshot

            snap = snapshot(file_path)
            result["mtime"] = mtime_note(snap["mtime"])
        except OSError as e:
            logger.warning("Aenderungszeit von %s nicht lesbar: %s", file_path, e)

    zeroth[_IMAGE_DESCRIPTION] = caption.encode("utf-8")
    zeroth[_XP_COMMENT] = caption.encode("utf-16le")
    existing_note = exif.get("Exif", {}).get(_USER_COMMENT)
    cap_fields = {"cap": source}
    if result.get("mtime") and "mtime" not in note_fields(existing_note):
        cap_fields["mtime"] = result["mtime"]
    exif.setdefault("Exif", {})[_USER_COMMENT] = _merge_user_comment(existing_note, cap_fields)

    if dry_run:
        result["reason"] = "Trockenlauf"
        return result

    try:
        piexif.insert(_safe_dump(exif), file_path)
    except Exception as e:
        raise ExifWriteError(f"{file_path}: {e}") from e

    if snap is not None:
        from ingest.filetimes import restore

        restored = restore(file_path, snap)
        result["mtime_restored"] = restored["mtime"]
        result["birth_restored"] = restored["birth"]

    check = read_caption(file_path)
    if check != caption:
        raise ExifWriteError(
            f"{file_path}: geschrieben, aber Gegenprobe ergibt {check!r} statt {caption!r}"
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
