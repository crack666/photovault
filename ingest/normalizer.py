"""Merge: EXIF > Folder-JSON > Filename. Kein CLIP-Datum. Location persistieren."""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
RE_FULL_DATE = re.compile(r"(19\d{2}|20\d{2})[-_](\d{2})[-_](\d{2})")


class Normalizer:
    def normalize(self, record) -> None:
        self._normalize_date(record)
        self._normalize_location(record)
        self._folder_event_tags(record)
        self._set_taken_at(record)

    def _normalize_date(self, record) -> None:
        if record.date and record.date_source == "exif":
            return

        hint = getattr(record, "date_hint", None)
        hint_source = getattr(record, "date_hint_source", None)
        if hint and not record.date:
            record.date = _as_iso_date(hint)
            record.date_source = hint_source or "folder"
            record.date_confidence = 0.9 if hint_source == "folder_json" else 0.75
            return

        if record.date:
            return

        folder_name = record.folder_name or ""
        file_path = record.file_path or ""
        full = RE_FULL_DATE.search(file_path.replace("\\", "/").rsplit("/", 1)[-1])
        if full:
            record.date = f"{full.group(1)}-{full.group(2)}-{full.group(3)}"
            record.date_source = "filename"
            record.date_confidence = 0.7
            return
        year = RE_YEAR.search(folder_name)
        if year:
            record.date = f"{year.group(1)}-01-01"
            record.date_source = "folder"
            record.date_confidence = 0.8
            return
        self._date_from_file_times(record)

    def _date_from_file_times(self, record) -> None:
        """Letzter Ausweg: Dateizeit. Besser ein grob datiertes Foto als eines,
        das durch jeden Zeitfilter faellt.

        Kopieren schiebt Zeitstempel nur nach vorne, nie zurueck -- also ist der
        aeltere der beiden der bessere Schaetzer. Unter Linux ist st_ctime die
        Inode-Aenderungszeit (= Kopierzeitpunkt), taugt also allein nicht.
        """
        candidates = [
            getattr(record, "file_mtime", None),
            getattr(record, "file_ctime", None),
        ]
        stamps = [c[:10] for c in candidates if c and len(c) >= 10]
        if not stamps:
            return
        oldest = min(stamps)
        if not RE_YEAR.match(oldest[:4]):
            return
        record.date = oldest
        record.date_source = "file_time"
        record.date_confidence = 0.3

    def _normalize_location(self, record) -> None:
        if getattr(record, "location", None):
            self._ensure_location_key(record)
            return
        hint = getattr(record, "location_hint", None)
        if hint:
            record.location = hint
            record.location_source = "folder"
            self._ensure_location_key(record)
            return
        if record.gps:
            record.location_source = "exif_gps"
            return

    def _ensure_location_key(self, record) -> None:
        key = getattr(record, "location_key", None)
        loc = getattr(record, "location", None) or ""
        if not key and loc:
            record.location_key = loc.lower().strip()
        if getattr(record, "location_key", None):
            record.location_lc = record.location_key

    def _folder_event_tags(self, record) -> None:
        blob = " ".join(
            x
            for x in (record.folder_name, Path(record.file_path).stem if record.file_path else "")
            if x
        ).lower()
        mapping = {
            "geburtstag": "geburtstag",
            "birthday": "geburtstag",
            "hochzeit": "hochzeit",
            "silvester": "silvester",
            "weihnacht": "weihnachten",
            "taufe": "taufe",
            "jga": "party",
            "abschluss": "abschluss",
        }
        tags = list(record.scene_tags or [])
        for needle, tag in mapping.items():
            if needle in blob and tag not in tags:
                tags.append(tag)
        record.scene_tags = tags

    def _set_taken_at(self, record) -> None:
        """Zeitstempel setzen -- mit Uhrzeit, wo eine belegbar ist.

        Die Uhrzeit ist nicht Zierrat: Fotos derselben Gelegenheit liegen
        Minuten auseinander, die naechste Gelegenheit Stunden. Ohne sie laesst
        sich kein Ereignis abgrenzen, und "das Bild um 11:59 gehoert zu dem um
        12:00" ist nicht entscheidbar.

        Reihenfolge: EXIF-Aufnahmezeit, sonst die Dateizeit -- letztere aber
        nur, wenn ihr Tag zum ermittelten Datum passt. Sonst ist sie der
        Kopierzeitpunkt und wuerde eine Praezision vortaeuschen, die es nicht
        gibt; dann bleibt es bei Mitternacht.
        """
        if not record.date:
            record.taken_at = None
            return
        exif_dt = getattr(record, "exif_datetime", None)
        if exif_dt and exif_dt[:10] == record.date:
            record.taken_at = exif_dt
            return
        if len(record.date) == 4 and record.date.isdigit():
            record.taken_at = f"{record.date}-01-01T00:00:00Z"
            return
        if len(record.date) != 10:
            record.taken_at = None
            return
        mtime = getattr(record, "file_mtime", None)
        if mtime and str(mtime)[:10] == record.date and len(str(mtime)) >= 19:
            record.taken_at = f"{str(mtime)[:19]}Z"
            return
        record.taken_at = f"{record.date}T00:00:00Z"


def _as_iso_date(hint: str) -> str:
    hint = hint.strip()
    if re.fullmatch(r"\d{4}", hint):
        return f"{hint}-01-01"
    m = RE_FULL_DATE.search(hint)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", hint):
        return hint
    return hint[:10]
