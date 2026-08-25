"""EXIF-Extraktion via Pillow. Optional."""
from __future__ import annotations
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

#: DateTimeOriginal, DateTimeDigitized, DateTime (IFD0). 306 ist oft die
#: letzte Aenderung — ein Import auf den PC, nicht die Aufnahme.
_DT_ORIGINAL = 36867
_DT_DIGITIZED = 36868
_DT_IMAGE = 306


def exif_capture_stamp(exif) -> str | None:
    """Aufnahmezeit: Original vor Digitalisiert vor DateTime.

    Sonst clustert eine Importnacht (ein DateTime fuer hunderte Dateien)
    Geburtstage und Abistreiche zu einer Serie.
    """
    if not exif:
        return None
    try:
        ifd = exif.get_ifd(0x8769) or {}
    except Exception:
        ifd = {}
    for raw in (ifd.get(_DT_ORIGINAL), ifd.get(_DT_DIGITIZED), exif.get(_DT_IMAGE)):
        if not raw:
            continue
        text = raw.decode("ascii", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
        text = text.strip("\x00 ").replace("-", ":")[:19]
        try:
            datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        return text
    return None


class ExifExtractor:
    def extract(self, file_path: str, image=None) -> dict:
        """`image` ist ein bereits geoeffnetes PIL-Image -- spart einen
        weiteren Lesezugriff auf das NAS."""
        result = {"date": None, "date_source": None, "date_confidence": 0.0,
                  "gps": None, "raw": None, "datetime": None,
                  "date_written_by_photovault": False}
        try:
            from PIL import Image
            img = image if image is not None else Image.open(file_path)
            exif = img.getexif()
            if not exif:
                return result
            date_str = exif_capture_stamp(exif)
            # Steht dort ein Wert, den *wir* geschrieben haben? Dann ist er
            # abgeleitet, nicht gemessen. Ohne diese Pruefung befoerdert der
            # naechste Lauf unsere Schaetzung zu EXIF mit Vertrauen 1.0, und
            # die Herkunft waere danach nicht mehr feststellbar.
            written_by_us = _own_note(exif)

            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    result["date"] = dt.strftime("%Y-%m-%d")
                    # Die Uhrzeit ist kein Beiwerk: sie traegt die
                    # Ereigniserkennung. Fotos derselben Gelegenheit liegen
                    # Minuten auseinander, die naechste Gelegenheit Stunden.
                    result["datetime"] = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if written_by_us:
                        # Herkunft und Vertrauen des urspruenglichen Verfahrens.
                        result["date_source"] = written_by_us
                        result["date_confidence"] = DERIVED_CONFIDENCE.get(written_by_us, 0.7)
                        result["date_written_by_photovault"] = True
                    else:
                        result["date_source"] = "exif"
                        result["date_confidence"] = 1.0
                except ValueError:
                    pass
            # GPS-IFD: 1/3 sind N/S und E/W, 2/4 die Grad-Tripel.
            # Frueher standen Ref und Koordinaten vertauscht — dann blieb
            # jedes Smartphone-Foto ohne Karte, obwohl die Datei GPS hat.
            gps_info = None
            try:
                gps_info = exif.get_ifd(0x8825)
            except Exception:
                gps_info = None
            if gps_info:
                lat = self._gps_to_decimal(gps_info.get(2), gps_info.get(1))
                lon = self._gps_to_decimal(gps_info.get(4), gps_info.get(3))
                if lat is not None and lon is not None:
                    result["gps"] = [round(lat, 6), round(lon, 6)]
            raw = {}
            from PIL.ExifTags import TAGS
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag in ("Make", "Model", "LensModel", "FocalLength", "FNumber", "ExposureTime"):
                    raw[tag] = str(value)
            result["raw"] = raw if raw else None
        except Exception as e:
            logger.debug("EXIF failed for %s: %s", file_path, e)
        return result

    @staticmethod
    def _gps_to_decimal(value, ref) -> float | None:
        try:
            from fractions import Fraction

            def _frac(part):
                if isinstance(part, (tuple, list)) and len(part) == 2:
                    return Fraction(part[0], part[1] or 1)
                return Fraction(part)

            d, m, s = (_frac(x) for x in value)
            decimal = float(d + m / 60 + s / 3600)
            if isinstance(ref, (bytes, bytearray)):
                ref = ref.decode("ascii", "ignore")
            heading = str(ref or "").strip("\x00 ").upper()[:1]
            if heading in ("S", "W"):
                decimal = -decimal
            return decimal
        except (TypeError, ValueError, ZeroDivisionError):
            return None


#: Vertrauen, das ein von uns geschriebener Wert behalten darf -- dasselbe wie
#: beim urspruenglichen Verfahren im Normalizer, nicht das von EXIF.
DERIVED_CONFIDENCE = {
    "filename": 0.7,
    "folder": 0.8,
    "folder_json": 0.9,
    "file_time": 0.3,
    "album": 0.6,
    "offset": 0.9,
    "accepted": 1.0,
}

_MARKER = "photovault:src="


def _own_note(exif) -> str | None:
    """Herkunft, falls PhotoVault diesen Zeitstempel geschrieben hat."""
    try:
        raw = exif.get_ifd(0x8769).get(37510)
    except Exception:
        return None
    if not raw:
        return None
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    if _MARKER not in text:
        return None
    rest = text.split(_MARKER, 1)[1]
    return rest.split(";", 1)[0].strip() or None
