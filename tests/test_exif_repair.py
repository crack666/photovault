"""Nur Dateien ohne echte Aufnahmezeit werden beschrieben — Kamera bleibt."""
from datetime import datetime, timezone

from tools.exif_repair import _parse_taken_at, candidate


def test_filename_derived_whatsapp_is_a_candidate():
    hit = candidate({
        "file_path": "/mnt/photo/WhatsApp Images/IMG-20181021-WA0081.jpg",
        "taken_at": "2018-10-21T17:11:11Z",
        "date_source": "filename",
    })
    assert hit is not None
    assert hit[1] == datetime(2018, 10, 21, 17, 11, 11, tzinfo=timezone.utc), "UTC, mit Zone"


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


def test_folder_filter_limits_to_named_folders():
    """Ohne Angabe gilt alles; mit Angabe nur der genannte Ordner."""
    from tools.exif_repair import in_folders

    wa = {"folder_name": "WhatsApp Images"}
    other = {"folder_name": "Fotos"}

    assert in_folders(wa, None) is True
    assert in_folders(other, None) is True

    only_wa = {"WhatsApp Images"}
    assert in_folders(wa, only_wa) is True
    assert in_folders(other, only_wa) is False


def test_missing_folder_name_is_excluded_when_filtering():
    """Kein Ordnername heisst: nicht belegt, also nicht anfassen."""
    from tools.exif_repair import in_folders

    assert in_folders({}, {"WhatsApp Images"}) is False


# --- #43: der Name kennt nur den Tag -> 12:00:00 Ortszeit statt nichts ------

def test_tag_aus_dem_namen_ohne_uhr_wird_mittag():
    hit = candidate({
        "file_path": "/mnt/photo/WhatsApp Images/IMG-20181021-WA0081.jpg",
        "taken_at": "2018-10-21T00:00:00Z", "date": "2018-10-21",
        "date_source": "filename",
    })
    assert hit is not None
    assert hit[1] == datetime(2018, 10, 21, 12, 0, 0), "Ortszeit, ohne Zone"
    assert hit[1].tzinfo is None
    assert hit[2] == "filename+noon"


def test_uhr_aus_mtime_traegt_mtime_in_der_notiz():
    hit = candidate({
        "file_path": "/mnt/photo/WhatsApp Images/IMG-20181021-WA0081.jpg",
        "taken_at": "2018-10-21T17:11:11Z", "date": "2018-10-21",
        "date_source": "filename",
    })
    assert hit[2] == "filename+mtime"


def test_ordnerdatum_bekommt_keinen_mittag():
    """Ein Ordner wie "Urlaub 2009" ergibt 2009-01-01 -- da ist schon der Tag geraten."""
    assert candidate({
        "file_path": "/mnt/photo/Urlaub 2009/a.jpg",
        "taken_at": "2009-01-01T00:00:00Z", "date": "2009-01-01",
        "date_source": "folder",
    }) is None


def test_name_mit_eigener_uhr_bekommt_keinen_mittag():
    assert candidate({
        "file_path": "/mnt/photo/Handy/IMG_20160423_101500.jpg",
        "taken_at": "2016-04-23T00:00:00Z", "date": "2016-04-23",
        "date_source": "filename",
    }) is None


class _Point:
    def __init__(self, payload):
        self.payload = payload


class _Client:
    """Qdrant-Doppel: eine Seite, dann Ende."""

    def __init__(self, payloads):
        self.payloads = payloads

    def scroll(self, **kw):
        return [_Point(p) for p in self.payloads], None


def test_lauf_mit_temporaerer_datei_trockenlauf_dann_apply(tmp_path, monkeypatch):
    import os

    import pytest

    piexif = pytest.importorskip("piexif")
    from PIL import Image

    from ingest.exif_writer import note_fields, read_capture_time
    from tools.exif_repair import run

    monkeypatch.setenv("PHOTOVAULT_TZ", "Europe/Berlin")
    p = tmp_path / "IMG-20220704-WA0001.jpg"
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, format="JPEG")
    # Dateizeit 2022-07-05 00:30 Berlin -- nicht der Tag des Namens.
    ts = datetime(2022, 7, 4, 22, 30, 0, tzinfo=timezone.utc).timestamp()
    os.utime(p, (ts, ts))
    client = _Client([{"file_path": str(p), "taken_at": "2022-07-04T00:00:00Z",
                       "date": "2022-07-04", "date_source": "filename",
                       "folder_name": "WhatsApp Images"}])

    before = p.read_bytes()
    tally = run(client, apply=False, workers=1)
    assert tally["waere geschrieben"] == 1
    assert p.read_bytes() == before

    tally = run(client, apply=True, workers=1)
    assert tally["geschrieben"] == 1
    assert read_capture_time(str(p)) == datetime(2022, 7, 4, 12, 0, 0)
    assert note_fields(piexif.load(str(p))["Exif"][37510])["src"] == "filename+noon"
    assert abs(os.path.getmtime(p) - ts) < 1
