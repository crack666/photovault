"""Nur Dateien ohne echte Aufnahmezeit werden beschrieben — Kamera bleibt."""
from datetime import datetime

from tools.exif_repair import _parse_taken_at, candidate


def test_filename_derived_whatsapp_is_a_candidate():
    hit = candidate({
        "file_path": "/mnt/photo/WhatsApp Images/IMG-20181021-WA0081.jpg",
        "taken_at": "2018-10-21T17:11:11Z",
        "date_source": "filename",
    })
    assert hit is not None
    assert hit[1] == datetime(2018, 10, 21, 17, 11, 11)


def test_camera_exif_is_never_a_candidate():
    assert candidate({
        "file_path": "/mnt/photo/Fotos/Album/DSCF0001.JPG",
        "taken_at": "2008-06-29T16:20:08Z",
        "date_source": "exif",
    }) is None


def test_midnight_means_unknown_clock_and_is_skipped():
    """Ohne Uhrzeit nichts schreiben — Mitternacht waere eine erfundene Praezision."""
    assert candidate({
        "file_path": "/mnt/photo/a.jpg",
        "taken_at": "2009-01-01T00:00:00Z",
        "date_source": "folder",
    }) is None


def test_png_is_not_writable():
    assert candidate({
        "file_path": "/mnt/photo/a.png",
        "taken_at": "2018-10-21T17:11:11Z",
        "date_source": "filename",
    }) is None


def test_parse_rejects_short_stamps():
    assert _parse_taken_at("2018-10-21") is None
    assert _parse_taken_at(None) is None
