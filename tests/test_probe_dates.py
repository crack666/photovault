"""Probe liest Original vor der Kopierzeit — dieselbe Regel wie der Ingest."""
from datetime import datetime

import pytest

piexif = pytest.importorskip("piexif")

from tools.probe_dates import inspect_file  # noqa: E402


def _jpeg(path, original=None, copied=None):
    from PIL import Image

    Image.new("RGB", (32, 24), (10, 20, 30)).save(path, format="JPEG")
    exif = piexif.load(str(path))
    if copied:
        exif["0th"][piexif.ImageIFD.DateTime] = copied.strftime("%Y:%m:%d %H:%M:%S").encode()
    if original:
        exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = (
            original.strftime("%Y:%m:%d %H:%M:%S").encode()
        )
    piexif.insert(piexif.dump(exif), str(path))
    return str(path)


def test_probe_reports_both_tags_and_prefers_original(tmp_path):
    p = _jpeg(
        tmp_path / "party.jpg",
        original=datetime(2006, 12, 17, 11, 58, 58),
        copied=datetime(2009, 1, 20, 2, 2, 3),
    )
    info = inspect_file(p)
    assert info["original_36867"].startswith("2006:12:17")
    assert info["datetime_306"].startswith("2009:01:20")
    assert info["chosen"] == "2006:12:17 11:58:58"


def test_probe_survives_a_file_without_exif(tmp_path):
    from PIL import Image

    p = tmp_path / "wa.jpg"
    Image.new("RGB", (16, 12)).save(p, format="JPEG")
    info = inspect_file(str(p))
    assert info["error"] is None
    assert info["chosen"] is None
    assert info["original_36867"] is None
