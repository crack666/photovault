"""DateTimeOriginal ist Ortszeit, der Index ist UTC (Issue #43).

Gemessen: `IMG-20220704-WA0005.jpg`, mtime 19:51:19 Ortszeit, im EXIF stand
17:51:19 -- der UTC-Wert aus dem Index, unveraendert hineingeschrieben. Jeder
Betrachter ausserhalb von PhotoVault zeigte die Datei zwei Stunden zu frueh.

Die Zeitzone wird hier ausdruecklich gesetzt (`Europe/Berlin`). Ein Test,
der in einem UTC-Container laeuft, saehe den Fehler sonst nie: dort sind
UTC und Ortszeit dieselbe Uhr.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

piexif = pytest.importorskip("piexif")

from ingest import exif_writer  # noqa: E402
from ingest.exif_extractor import ExifExtractor  # noqa: E402
from ingest.exif_writer import (  # noqa: E402
    ExifWriteError,
    note_fields,
    read_capture_instant,
    read_capture_time,
    read_note,
    write_capture_time,
)
from ingest.folder_parser import FolderParser  # noqa: E402
from ingest.localtime import local_day, zone  # noqa: E402
from ingest.normalizer import Normalizer  # noqa: E402

UTC = timezone.utc


@pytest.fixture(autouse=True)
def berlin(monkeypatch):
    monkeypatch.setenv("PHOTOVAULT_TZ", "Europe/Berlin")


def _jpeg(path, mtime_utc: datetime | None = None):
    from PIL import Image

    Image.new("RGB", (32, 24), (10, 20, 30)).save(path, format="JPEG")
    if mtime_utc is not None:
        ts = mtime_utc.timestamp()
        os.utime(path, (ts, ts))
    return str(path)


def _note(path) -> dict:
    return note_fields(piexif.load(path)["Exif"][37510])


def _offset(path) -> str | None:
    raw = piexif.load(path)["Exif"].get(36881)
    return raw.decode() if raw else None


# Die drei Dateien aus dem Issue: (mtime UTC, erwartete Ortszeit, Offset)
SUMMER = (datetime(2022, 7, 4, 17, 51, 19, tzinfo=UTC),
          datetime(2022, 7, 4, 19, 51, 19), "+02:00")
WINTER = (datetime(2022, 11, 18, 7, 30, 44, tzinfo=UTC),
          datetime(2022, 11, 18, 8, 30, 44), "+01:00")
SUMMER_2025 = (datetime(2025, 8, 5, 18, 38, 36, tzinfo=UTC),
               datetime(2025, 8, 5, 20, 38, 36), "+02:00")


class TestWriterSchreibtOrtszeit:
    @pytest.mark.parametrize("utc,local,offset", [SUMMER, WINTER, SUMMER_2025])
    def test_utc_wird_in_ortszeit_geschrieben(self, tmp_path, utc, local, offset):
        p = _jpeg(tmp_path / "wa.jpg", utc)
        out = write_capture_time(p, utc, source="filename+mtime", dry_run=False)
        assert out["written"] is True
        assert read_capture_time(p) == local, "DateTimeOriginal ist Ortszeit"
        assert _offset(p) == offset
        assert read_capture_instant(p) == utc

    def test_ortszeit_steht_auch_in_digitized_und_datetime(self, tmp_path):
        utc, local, offset = SUMMER
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, utc, source="filename+mtime", dry_run=False)
        exif = piexif.load(p)
        stamp = local.strftime("%Y:%m:%d %H:%M:%S").encode()
        assert exif["Exif"][36868] == stamp
        assert exif["0th"][306] == stamp
        assert exif["Exif"][36880] == offset.encode()

    def test_ohne_zone_bleibt_der_wert_wie_er_ist(self, tmp_path):
        """Von Hand gesetzte Zeiten sind schon Ortszeit -- nichts umrechnen."""
        p = _jpeg(tmp_path / "wa.jpg")
        when = datetime(2022, 7, 4, 19, 51, 19)
        write_capture_time(p, when, source="accepted", dry_run=False)
        assert read_capture_time(p) == when
        assert _offset(p) is None

    def test_ueberschreiben_ohne_zone_entfernt_einen_alten_offset(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)
        write_capture_time(p, datetime(2022, 7, 4, 12, 0, 0), source="accepted",
                           dry_run=False, overwrite=True)
        assert _offset(p) is None, "der alte Offset wuerde eine andere Uhr behaupten"

    def test_derselbe_zeitpunkt_ist_ein_noop(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)
        out = write_capture_time(p, SUMMER[0], source="filename+mtime",
                                 dry_run=False, overwrite=True)
        assert out["reason"] == "Wert ist schon korrekt"

    def test_dateizeit_bleibt(self, tmp_path):
        utc = SUMMER[0]
        p = _jpeg(tmp_path / "wa.jpg", utc)
        write_capture_time(p, utc, source="filename+mtime", dry_run=False)
        assert abs(os.path.getmtime(p) - utc.timestamp()) < 1


class TestGegenprobe:
    def test_gegenprobe_prueft_den_zeitpunkt_nicht_nur_die_ziffern(self, tmp_path, monkeypatch):
        """Liest die Datei den UTC-Wert als Ortszeit zurueck, muss das auffallen."""
        utc = SUMMER[0]
        p = _jpeg(tmp_path / "wa.jpg")
        # 17:51:19 als Ortszeit (+02:00) gelesen -- genau der Fehler aus #43.
        wrong = utc.replace(tzinfo=timezone(timedelta(hours=2)))
        monkeypatch.setattr(exif_writer, "read_capture_instant", lambda _p: wrong)
        with pytest.raises(ExifWriteError, match="Zeitpunkt"):
            write_capture_time(p, utc, source="filename+mtime", dry_run=False)

    def test_gegenprobe_ohne_offset_schlaegt_an(self, tmp_path, monkeypatch):
        p = _jpeg(tmp_path / "wa.jpg")
        monkeypatch.setattr(exif_writer, "read_capture_instant", lambda _p: None)
        with pytest.raises(ExifWriteError):
            write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)


class TestTagesuebergang:
    """mtime 00:30 Ortszeit, der Name traegt diesen Tag. In UTC ist es noch
    der Vortag -- im EXIF darf es das nicht sein."""

    MTIME = datetime(2022, 7, 4, 22, 30, 0, tzinfo=UTC)  # 2022-07-05 00:30 Berlin
    NAME = "IMG-20220705-WA0001.jpg"

    def _record(self, path):
        class _Rec:
            pass

        r = _Rec()
        r.file_path = path
        r.folder_name = "WhatsApp Images"
        r.date = None
        r.date_source = None
        r.date_confidence = 0.0
        fd = FolderParser().parse(path)  # wie in der Pipeline
        r.date_hint = fd.get("date_hint")
        r.date_hint_source = fd.get("date_hint_source")
        r.exif_datetime = None
        r.file_mtime = self.MTIME.isoformat()
        r.file_ctime = None
        r.taken_at = None
        r.location = None
        r.location_hint = None
        r.gps = None
        r.scene_tags = []
        return r

    def test_index_behaelt_die_uhr_in_utc(self, tmp_path):
        r = self._record(str(tmp_path / self.NAME))
        Normalizer().normalize(r)
        assert r.date == "2022-07-05"
        assert r.date_source == "filename"
        assert r.taken_at == "2022-07-04T22:30:00Z", "Index bleibt UTC mit Z"

    def test_exif_traegt_den_tag_aus_dem_namen(self, tmp_path):
        from tools.exif_repair import candidate, note_source

        p = _jpeg(tmp_path / self.NAME, self.MTIME)
        r = self._record(p)
        Normalizer().normalize(r)
        hit = candidate({"file_path": p, "taken_at": r.taken_at, "date": r.date,
                         "date_source": r.date_source})
        assert hit is not None
        write_capture_time(p, hit[1], source=note_source(r.date_source), dry_run=False)
        assert read_capture_time(p) == datetime(2022, 7, 5, 0, 30, 0)
        assert read_capture_time(p).date().isoformat() == "2022-07-05", "nicht der Vortag"

    def test_winter_tagesuebergang(self, tmp_path):
        from tools.exif_repair import candidate

        # 2022-11-18 00:15 Berlin = 2022-11-17 23:15 UTC
        mtime = datetime(2022, 11, 17, 23, 15, 0, tzinfo=UTC)
        hit = candidate({"file_path": "/x/IMG-20221118-WA0001.jpg",
                         "taken_at": "2022-11-17T23:15:00Z", "date": "2022-11-18",
                         "date_source": "filename"})
        assert hit is not None and hit[1] == mtime
        p = _jpeg(tmp_path / "IMG-20221118-WA0001.jpg", mtime)
        write_capture_time(p, hit[1], source="filename+mtime", dry_run=False)
        assert read_capture_time(p) == datetime(2022, 11, 18, 0, 15, 0)

    def test_tag_der_nicht_zum_namen_passt_wird_nicht_geschrieben(self):
        from tools.exif_repair import candidate

        # Ortszeit 2022-07-05 00:30, Name sagt 2022-07-04: Uhr gehoert nicht dazu.
        assert candidate({"file_path": "/x/IMG-20220704-WA0001.jpg",
                          "taken_at": "2022-07-04T22:30:00Z", "date": "2022-07-04",
                          "date_source": "filename"}) is None

    def test_utc_tag_passt_ortszeit_nicht_dann_bleibt_mitternacht(self, tmp_path):
        """Name 2022-07-04, mtime 2022-07-04 22:30 UTC = 07-05 00:30 Berlin."""
        r = self._record(str(tmp_path / "IMG-20220704-WA0001.jpg"))
        Normalizer().normalize(r)
        assert r.date == "2022-07-04"
        assert r.taken_at == "2022-07-04T00:00:00Z", "keine erfundene Uhr"

    def test_file_time_tag_in_ortszeit(self, tmp_path):
        r = self._record(str(tmp_path / "scan.jpg"))
        Normalizer().normalize(r)
        assert r.date_source == "file_time"
        assert r.date == "2022-07-05"
        assert r.taken_at == "2022-07-04T22:30:00Z"


class TestHerkunftsnotiz:
    def test_repair_nennt_die_uhr_aus_mtime(self):
        from tools.exif_repair import note_source

        assert note_source("filename") == "filename+mtime"
        assert note_source("folder") == "folder+mtime"
        assert note_source("file_time") == "file_time", "nennt die Dateizeit schon"
        assert note_source("accepted") == "accepted"

    def test_notiz_unterscheidet_tag_und_uhr(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg", SUMMER[0])
        write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)
        fields = _note(p)
        assert fields["src"] == "filename+mtime"
        assert fields["mtime"] == "2022-07-04T17:51:19Z", "mtime in der Notiz traegt Z"
        assert read_note(p)[0] == "filename+mtime"

    def test_extractor_behaelt_date_source_filename(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg", SUMMER[0])
        write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)
        got = ExifExtractor().extract(p)
        assert got["date_source"] == "filename"
        assert got["date_confidence"] == 0.7
        assert got["date_written_by_photovault"] is True
        assert got["date"] == "2022-07-04"

    def test_erste_dateizeit_bleibt_in_der_notiz(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg", SUMMER[0])
        write_capture_time(p, SUMMER[0], source="filename+mtime", dry_run=False)
        later = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
        os.utime(p, (later, later))  # kopiert
        write_capture_time(p, datetime(2022, 7, 4, 20, 0, 0), source="accepted",
                           dry_run=False, overwrite=True)
        assert _note(p)["mtime"] == "2022-07-04T17:51:19Z"


class TestKorrekturSkript:
    """`tools/exif_tz_fix.py`: Bestand, der vor dem Fix geschrieben wurde."""

    def _alt_geschrieben(self, path, utc: datetime, src="filename", extra=""):
        """So sah eine Datei nach dem alten `exif_repair` aus."""
        p = _jpeg(path, utc)
        exif = piexif.load(p)
        stamp = utc.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif["Exif"][36867] = stamp
        exif["Exif"][36868] = stamp
        exif["0th"][306] = stamp
        note = f"photovault:src={src};mtime={utc.strftime('%Y-%m-%dT%H:%M:%S')}{extra}"
        exif["Exif"][37510] = note.encode()
        piexif.insert(piexif.dump(exif), p)
        os.utime(p, (utc.timestamp(), utc.timestamp()))
        return p

    @pytest.mark.parametrize("utc,local,offset", [SUMMER, WINTER])
    def test_trockenlauf_aendert_nichts_apply_korrigiert(self, tmp_path, utc, local, offset):
        from tools.exif_tz_fix import DONE, FIX, run

        p = self._alt_geschrieben(tmp_path / "IMG-20220704-WA0005.jpg", utc, extra=";cap=llm")
        before = open(p, "rb").read()
        tally = run([str(tmp_path)], apply=False, workers=1)
        assert tally[FIX] == 1
        assert open(p, "rb").read() == before

        tally = run([str(tmp_path)], apply=True, workers=1)
        assert tally[DONE] == 1
        assert read_capture_time(p) == local
        assert _offset(p) == offset
        fields = _note(p)
        assert fields["src"] == "filename+mtime"
        assert fields.get("prev") is None, "unser eigener Fehler ist kein Ausgangszustand"
        assert fields["cap"] == "llm"
        assert abs(os.path.getmtime(p) - utc.timestamp()) < 1

        again = run([str(tmp_path)], apply=True, workers=1)
        assert again["schon im neuen Format"] == 1

    def test_tageswechsel_bleibt_fuer_den_menschen(self, tmp_path):
        from tools.exif_tz_fix import classify

        p = self._alt_geschrieben(tmp_path / "IMG-20220704-WA0001.jpg",
                                  datetime(2022, 7, 4, 22, 30, 0, tzinfo=UTC))
        assert classify(p)[0] == "Tag wuerde wechseln (von Hand)"

    def test_kamera_und_fremde_dateien_bleiben(self, tmp_path):
        from tools.exif_tz_fix import classify

        cam = _jpeg(tmp_path / "IMG_20251104_065219.jpg")
        exif = piexif.load(cam)
        exif["Exif"][36867] = b"2025:11:04 06:52:21"
        piexif.insert(piexif.dump(exif), cam)
        assert classify(cam)[0] == "keine Datumsnotiz"

        acc = self._alt_geschrieben(tmp_path / "acc.jpg", SUMMER[0], src="accepted")
        assert classify(acc)[0] == "Herkunft accepted"

    def test_uhr_nicht_aus_mtime_wird_nicht_angefasst(self, tmp_path):
        from tools.exif_tz_fix import classify

        p = self._alt_geschrieben(tmp_path / "a.jpg", SUMMER[0])
        exif = piexif.load(p)
        exif["Exif"][36867] = b"2022:07:04 12:00:00"
        piexif.insert(piexif.dump(exif), p)
        assert classify(p)[0] == "Uhr nicht aus der Notiz-mtime"

    def test_in_utc_gibt_es_nichts_zu_tun(self, tmp_path, monkeypatch):
        from tools.exif_tz_fix import classify

        monkeypatch.setenv("PHOTOVAULT_TZ", "UTC")
        p = self._alt_geschrieben(tmp_path / "a.jpg", SUMMER[0])
        assert classify(p)[0] == "Ortszeit = UTC"


class TestAcceptRoute:
    def test_utc_aus_dem_index_kommt_mit_zone(self):
        from api.routes.photos import _when_from_payload

        got = _when_from_payload({"taken_at": "2022-07-04T17:51:19Z", "date": "2022-07-04"})
        assert got == SUMMER[0]

    def test_mitternacht_bleibt_der_blanke_tag(self):
        from api.routes.photos import _when_from_payload

        got = _when_from_payload({"taken_at": "2022-07-04T00:00:00Z"})
        assert got == datetime(2022, 7, 4)
        assert got.tzinfo is None


class TestZone:
    def test_zone_aus_der_umgebung(self):
        assert str(zone()) == "Europe/Berlin"

    def test_tz_variable_reicht(self, monkeypatch):
        monkeypatch.delenv("PHOTOVAULT_TZ")
        monkeypatch.setenv("TZ", "Europe/Berlin")
        assert str(zone()) == "Europe/Berlin"

    def test_local_day(self):
        assert local_day("2022-07-04T22:30:00+00:00").isoformat() == "2022-07-05"
        assert local_day("2022-07-04T22:30:00Z").isoformat() == "2022-07-05"
        assert local_day("2022-07-04").isoformat() == "2022-07-04"
