"""Aufnahmezeit — die Grundlage für Ereigniserkennung.

`taken_at` stand vorher bei jedem Foto auf Mitternacht: der EXIF-Extraktor
schnitt die Uhrzeit mit `strftime("%Y-%m-%d")` ab, und der Normalizer baute
den Zeitstempel aus dem Tagesdatum neu. Damit war „das Bild um 11:59 gehört
zum selben Ereignis wie das um 12:00" nicht entscheidbar.
"""
from __future__ import annotations

from ingest.normalizer import Normalizer


class _Rec:
    def __init__(self, **kw):
        self.date = None
        self.exif_datetime = None
        self.file_mtime = None
        self.taken_at = None
        self.scene_tags = []
        self.__dict__.update(kw)


def _taken(**kw) -> str | None:
    r = _Rec(**kw)
    Normalizer()._set_taken_at(r)
    return r.taken_at


class TestExifTime:
    def test_exif_time_survives(self):
        assert _taken(date="2008-06-29",
                      exif_datetime="2008-06-29T16:20:08Z") == "2008-06-29T16:20:08Z"

    def test_exif_beats_file_time(self):
        assert _taken(date="2008-06-29",
                      exif_datetime="2008-06-29T16:20:08Z",
                      file_mtime="2008-06-29T23:59:59+00:00") == "2008-06-29T16:20:08Z"

    def test_mismatched_exif_is_ignored(self):
        """Ein EXIF-Datum, das nicht zum ermittelten Datum passt, ist unbrauchbar."""
        got = _taken(date="2010-07-12", exif_datetime="2014-08-10T12:02:20Z")
        assert got == "2010-07-12T00:00:00Z"


class TestFileTimeFallback:
    def test_file_time_fills_in_when_exif_is_absent(self):
        """WhatsApp-Bilder haben kein EXIF; die Dateizeit ist dort das Beste."""
        assert _taken(date="2018-10-20",
                      file_mtime="2018-10-20T17:11:11+00:00") == "2018-10-20T17:11:11Z"

    def test_file_time_from_another_day_is_refused(self):
        """Dann ist es der Kopierzeitpunkt und taeuscht eine Praezision vor."""
        got = _taken(date="2009-01-01", file_mtime="2009-07-02T16:33:10+00:00")
        assert got == "2009-01-01T00:00:00Z"


class TestEdges:
    def test_year_only(self):
        assert _taken(date="2009") == "2009-01-01T00:00:00Z"

    def test_no_date_no_timestamp(self):
        assert _taken(date=None) is None

    def test_garbage_date(self):
        assert _taken(date="irgendwas") is None

    def test_no_precision_is_invented(self):
        """Ohne Beleg bleibt es Mitternacht — nicht irgendeine Uhrzeit."""
        assert _taken(date="2015-05-05") == "2015-05-05T00:00:00Z"
