"""Datum aus Dateizeiten: letzter Ausweg, damit nichts durch Zeitfilter faellt."""
from ingest.normalizer import Normalizer
from ingest.pipeline import PhotoRecord


def _record(**kw) -> PhotoRecord:
    base = dict(photo_id="x", file_path="/photos/Handyfotos/foto.jpg", folder_name="Handyfotos")
    base.update(kw)
    return PhotoRecord(**base)


class TestFileTimeFallback:
    def setup_method(self):
        self.n = Normalizer()

    def test_mtime_used_when_nothing_else_known(self):
        rec = _record(
            file_mtime="2018-10-31T14:39:49+00:00",
            file_ctime="2026-08-23T09:24:48+00:00",
        )
        self.n.normalize(rec)
        assert rec.date == "2018-10-31"
        assert rec.date_source == "file_time"
        assert rec.date_confidence == 0.3
        # Seit der Ereigniserkennung traegt taken_at die Uhrzeit, wenn eine
        # belegbar ist -- hier aus der Dateizeit, deren Tag zum Datum passt.
        # Vorher stand hier Mitternacht, weil die Zeit verworfen wurde.
        assert rec.taken_at == "2018-10-31T14:39:49Z"

    def test_older_stamp_wins(self):
        """Kopieren schiebt Zeitstempel nur nach vorne - der aeltere ist ehrlicher."""
        rec = _record(
            file_mtime="2020-01-01T00:00:00+00:00",
            file_ctime="2011-05-05T00:00:00+00:00",
        )
        self.n.normalize(rec)
        assert rec.date == "2011-05-05"

    def test_exif_still_wins(self):
        rec = _record(
            date="2008-06-01",
            date_source="exif",
            file_mtime="2026-01-01T00:00:00+00:00",
        )
        self.n.normalize(rec)
        assert rec.date == "2008-06-01"
        assert rec.date_source == "exif"

    def test_folder_hint_beats_file_time(self):
        rec = _record(
            folder_name="Griechenland 2015",
            date_hint="2015",
            date_hint_source="folder_name",
            file_mtime="2026-01-01T00:00:00+00:00",
        )
        self.n.normalize(rec)
        assert rec.date == "2015-01-01"
        assert rec.date_source == "folder_name"

    def test_no_times_leaves_date_empty(self):
        rec = _record()
        self.n.normalize(rec)
        assert rec.date is None
        assert rec.taken_at is None
