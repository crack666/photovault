"""Ein von uns geschriebener Zeitstempel darf nicht als Messung gelten.

Schreibt PhotoVault ein aus dem Dateinamen abgeleitetes Datum ins EXIF, liest
der nächste Lauf es dort wieder — und würde ihm ohne diese Prüfung Vertrauen
1.0 geben. Die Ableitung wäre damit zur Messung befördert und die Herkunft
für immer verloren.
"""
from __future__ import annotations

from datetime import datetime

import pytest

piexif = pytest.importorskip("piexif")

from ingest.exif_extractor import ExifExtractor  # noqa: E402
from ingest.exif_writer import write_capture_time  # noqa: E402


def _jpeg(path, when: datetime | None = None):
    from PIL import Image

    Image.new("RGB", (32, 24), (10, 20, 30)).save(path, format="JPEG")
    if when:
        exif = piexif.load(str(path))
        exif.setdefault("Exif", {})[36867] = when.strftime("%Y:%m:%d %H:%M:%S").encode()
        piexif.insert(piexif.dump(exif), str(path))
    return str(path)


WHEN = datetime(2018, 10, 21, 14, 30, 5)


def test_camera_timestamp_keeps_full_confidence(tmp_path):
    p = _jpeg(tmp_path / "cam.jpg", WHEN)
    got = ExifExtractor().extract(p)
    assert got["date_source"] == "exif"
    assert got["date_confidence"] == 1.0
    assert got["date_written_by_photovault"] is False


def test_our_own_value_keeps_its_original_provenance(tmp_path):
    p = _jpeg(tmp_path / "wa.jpg")
    write_capture_time(p, WHEN, source="filename", dry_run=False)
    got = ExifExtractor().extract(p)
    assert got["date"] == "2018-10-21"
    assert got["date_source"] == "filename", "nicht 'exif' -- wir haben es selbst geschrieben"
    assert got["date_confidence"] == 0.7
    assert got["date_written_by_photovault"] is True


def test_file_time_origin_stays_weak(tmp_path):
    """Aus der Dateizeit abgeleitet bleibt 0.3, auch wenn es im EXIF steht."""
    p = _jpeg(tmp_path / "wa.jpg")
    write_capture_time(p, WHEN, source="file_time", dry_run=False)
    got = ExifExtractor().extract(p)
    assert got["date_confidence"] == 0.3


def test_a_corrected_camera_time_is_trusted_more(tmp_path):
    """Ein per Offset korrigierter Wert beruht auf echten Kameradaten."""
    p = _jpeg(tmp_path / "cam.jpg", datetime(2009, 1, 16, 1, 46, 18))
    write_capture_time(p, WHEN, source="offset", dry_run=False, overwrite=True)
    got = ExifExtractor().extract(p)
    assert got["date_source"] == "offset"
    assert got["date_confidence"] == 0.9


def test_the_time_itself_is_still_returned(tmp_path):
    """Die Herkunft aendert das Vertrauen, nicht den Wert."""
    p = _jpeg(tmp_path / "wa.jpg")
    write_capture_time(p, WHEN, source="filename", dry_run=False)
    assert ExifExtractor().extract(p)["datetime"] == "2018-10-21T14:30:05Z"


def test_a_foreign_user_comment_is_not_mistaken_for_ours(tmp_path):
    p = _jpeg(tmp_path / "cam.jpg", WHEN)
    exif = piexif.load(p)
    exif.setdefault("Exif", {})[37510] = b"Aufgenommen mit Liebe"
    piexif.insert(piexif.dump(exif), p)
    got = ExifExtractor().extract(p)
    assert got["date_source"] == "exif"
    assert got["date_confidence"] == 1.0
