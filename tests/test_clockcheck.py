"""Kameras mit falsch gestellter Uhr erkennen — und nicht zu viel behaupten."""
from __future__ import annotations

from datetime import timedelta

from ingest.clockcheck import MIN_ALBUM, by_camera, find


def _photo(pid: str, album: str, stamp: str, model: str | None = None) -> dict:
    return {"photo_id": pid, "folder_name": album, "taken_at": stamp,
            "exif": {"Model": model} if model else {}}


def _album(album: str, day: str, n: int, model: str = "GOOD CAM", start: int = 0) -> list[dict]:
    return [_photo(f"{album}-{i}", album, f"{day}T1{i % 8}:00:00Z", model)
            for i in range(start, start + n)]


class TestDetection:
    def test_a_camera_that_is_years_off_is_found(self):
        photos = _album("18. Geburtstag (2006)", "2006-12-17", 20)
        photos += [_photo(f"bad{i}", "18. Geburtstag (2006)", "2009-01-20T14:00:00Z", "FinePix A202")
                   for i in range(5)]
        found = find(photos)
        assert len(found) == 1
        assert found[0].camera == "FinePix A202"
        assert found[0].count == 5

    def test_photos_within_a_year_are_not_suspicious(self):
        """Alben ziehen sich ueber Wochen, Silvester ueber den Jahreswechsel."""
        photos = _album("Silvester", "2012-12-31", 20)
        photos += [_photo(f"n{i}", "Silvester", "2013-01-01T02:00:00Z", "GOOD CAM")
                   for i in range(5)]
        assert find(photos) == []

    def test_a_lone_outlier_is_ignored(self):
        """Ein einzelnes Foto mit falschem Datum ist meist falsch einsortiert."""
        photos = _album("Abi 08", "2008-06-27", 20)
        photos += [_photo("einzeln", "Abi 08", "2014-02-02T12:00:00Z", "NIKON D300")]
        assert find(photos) == []

    def test_small_albums_are_skipped(self):
        """Unter acht Fotos *insgesamt* gibt es keine belastbare Mehrheit."""
        photos = _album("winzig", "2008-06-27", MIN_ALBUM - 4)
        photos += [_photo(f"b{i}", "winzig", "2015-01-01T12:00:00Z", "X") for i in range(3)]
        assert len(photos) < MIN_ALBUM
        assert find(photos) == []

    def test_an_album_without_a_majority_is_left_alone(self):
        """Eine Sammlung ohne gemeinsames Datum hat keine Wahrheit, gegen die
        sich ein Ausreisser abheben koennte."""
        photos = (_album("Sammelsurium", "2005-01-01", 5)
                  + _album("Sammelsurium", "2012-01-01", 5, start=100)
                  + _album("Sammelsurium", "2019-01-01", 5, start=200))
        assert find(photos) == []


class TestTwoFailureModes:
    def test_a_constant_offset_is_recognised_and_quantified(self):
        """Werksstand: alle Fotos auf demselben Tag -- exakt korrigierbar."""
        photos = _album("Videoabend", "2008-11-01", 20)
        photos += [_photo(f"k{i}", "Videoabend", f"2005-01-01T0{i}:00:00Z", "KODAK V530")
                   for i in range(4)]
        s = find(photos)[0]
        assert s.kind == "versatz"
        assert s.offset is not None
        assert abs(s.offset - timedelta(days=1400)) < timedelta(days=3)
        assert "verschieben" in s.proposal()

    def test_scattered_dates_are_flagged_as_unrecoverable(self):
        """Mehrfach zurueckgefallen: die absolute Zeit ist weg."""
        photos = _album("18. Geburtstag (2007)", "2007-06-02", 20)
        for i, day in enumerate(["2009-01-20", "2012-05-05", "2015-11-26"]):
            photos.append(_photo(f"s{i}", "18. Geburtstag (2007)", f"{day}T12:00:00Z", "FinePix A202"))
        s = find(photos)[0]
        assert s.kind == "zurueckgefallen"
        assert s.offset is None
        assert "verloren" in s.proposal()


class TestEvidence:
    def test_the_same_camera_across_albums_is_grouped(self):
        """Das eigentliche Argument: ein Geraet, derselbe Fehler, mehrere Alben."""
        photos = []
        for album, day in (("18. Geburtstag (2006)", "2006-12-17"), ("18. Geburtstag (2007)", "2007-06-02")):
            photos += _album(album, day, 20)
            photos += [_photo(f"{album}-b{i}", album, "2009-01-20T14:00:00Z", "FinePix A202")
                       for i in range(3)]
        grouped = by_camera(find(photos))
        assert list(grouped)[0] == "FinePix A202"
        assert len(grouped["FinePix A202"]) == 2

    def test_the_album_year_in_the_name_wins(self):
        """Steht das Jahr im Albumnamen, ist es verlaesslicher als die Mehrheit."""
        photos = _album("Abi 08", "2008-06-27", 12)
        photos += [_photo(f"x{i}", "Abi 08", "2011-03-03T12:00:00Z", "ANDERE") for i in range(4)]
        s = find(photos)[0]
        assert s.reference.year == 2008

    def test_a_camera_without_a_model_still_groups(self):
        photos = _album("Fun", "2008-09-15", 20)
        photos += [_photo(f"u{i}", "Fun", "2018-12-06T12:00:00Z") for i in range(4)]
        assert find(photos)[0].camera == "unbekannt"


def test_nothing_to_report_is_not_an_error():
    assert find([]) == []
    assert by_camera([]) == {}
