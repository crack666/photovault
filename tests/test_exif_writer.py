"""EXIF zurückschreiben — mit Herkunftsnotiz und Umkehrbarkeit.

Diese Tests arbeiten auf echten JPEGs im tmp_path, nicht auf Attrappen: der
Punkt der Übung ist, dass die Datei hinterher noch lesbar ist und das
Bildmaterial unverändert bleibt.
"""
from __future__ import annotations

from datetime import datetime

import pytest

piexif = pytest.importorskip("piexif")

from ingest.exif_writer import (  # noqa: E402
    ExifWriteError, read_caption, read_capture_time, read_note, revert,
    write_caption, write_capture_time,
)


def _jpeg(path, when: datetime | None = None):
    from PIL import Image

    Image.new("RGB", (64, 48), (30, 60, 90)).save(path, format="JPEG", quality=90)
    if when:
        exif = piexif.load(str(path))
        stamp = when.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif.setdefault("Exif", {})[36867] = stamp
        piexif.insert(piexif.dump(exif), str(path))
    return str(path)


WHEN = datetime(2018, 10, 21, 14, 30, 5)


class TestWritingWhereNothingWas:
    def test_writes_and_reads_back(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        assert read_capture_time(p) is None
        out = write_capture_time(p, WHEN, source="filename", dry_run=False)
        assert out["written"] is True
        assert read_capture_time(p) == WHEN

    def test_records_where_the_value_came_from(self, tmp_path):
        """Sonst gilt unsere Schaetzung beim naechsten Lauf als Messung."""
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        assert read_note(p)[:2] == ("filename", "")

    def test_dry_run_changes_nothing(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        before = open(p, "rb").read()
        out = write_capture_time(p, WHEN, source="filename", dry_run=True)
        assert out["written"] is False
        assert open(p, "rb").read() == before

    def test_pixels_are_untouched(self, tmp_path):
        """Kein Neukodieren -- sonst verliert jedes Schreiben Bildqualitaet."""
        from PIL import Image

        p = _jpeg(tmp_path / "wa.jpg")
        before = Image.open(p).tobytes()
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        assert Image.open(p).tobytes() == before


class TestNotOverwritingByAccident:
    def test_existing_time_is_kept(self, tmp_path):
        had = datetime(2008, 6, 29, 16, 20, 8)
        p = _jpeg(tmp_path / "cam.jpg", had)
        out = write_capture_time(p, WHEN, source="album", dry_run=False)
        assert out["written"] is False
        assert "bereits" in out["reason"]
        assert read_capture_time(p) == had

    def test_overwrite_keeps_the_old_value_in_the_note(self, tmp_path):
        had = datetime(2009, 1, 16, 1, 46, 18)
        p = _jpeg(tmp_path / "cam.jpg", had)
        write_capture_time(p, WHEN, source="offset", dry_run=False, overwrite=True)
        assert read_capture_time(p) == WHEN
        assert read_note(p)[:2] == ("offset", "2009:01:16 01:46:18")

    def test_writing_the_same_value_is_a_noop(self, tmp_path):
        p = _jpeg(tmp_path / "cam.jpg", WHEN)
        out = write_capture_time(p, WHEN, source="album", dry_run=False, overwrite=True)
        assert out["written"] is False


class TestRevert:
    def test_restores_the_previous_value(self, tmp_path):
        had = datetime(2009, 1, 16, 1, 46, 18)
        p = _jpeg(tmp_path / "cam.jpg", had)
        write_capture_time(p, WHEN, source="offset", dry_run=False, overwrite=True)
        assert revert(p, dry_run=False)["reverted"] is True
        assert read_capture_time(p) == had

    def test_nothing_to_restore_where_nothing_was(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        out = revert(p, dry_run=False)
        assert out["reverted"] is False
        assert read_capture_time(p) == WHEN, "der Wert bleibt, die Notiz erklaert ihn"

    def test_foreign_files_are_left_alone(self, tmp_path):
        p = _jpeg(tmp_path / "fremd.jpg", datetime(2008, 6, 29, 16, 20, 8))
        assert revert(p, dry_run=False)["reverted"] is False


class TestRefusals:
    def test_png_is_refused(self, tmp_path):
        from PIL import Image

        p = tmp_path / "x.png"
        Image.new("RGB", (8, 8)).save(p)
        out = write_capture_time(str(p), WHEN, source="filename", dry_run=False)
        assert out["written"] is False
        assert "verlustfrei" in out["reason"]

    def test_broken_file_reports_instead_of_crashing(self, tmp_path):
        p = tmp_path / "kaputt.jpg"
        p.write_bytes(b"kein JPEG")
        out = write_capture_time(str(p), WHEN, source="filename", dry_run=False)
        assert out["written"] is False
        assert out["reason"]

    def test_missing_file_is_reported(self, tmp_path):
        out = write_capture_time(str(tmp_path / "weg.jpg"), WHEN,
                                 source="filename", dry_run=False)
        assert out["written"] is False


class TestMtimeSurvives:
    """Die Aenderungszeit ist bei WhatsApp-Dateien die einzige Uhrzeitquelle.

    Geht sie beim Schreiben verloren, hat man genau einen Versuch: ein
    Fehler waere danach nicht mehr korrigierbar, weil die Grundlage fehlt.
    """

    def test_writing_alone_would_destroy_it(self, tmp_path):
        """Belegt die Gefahr -- ohne Erhaltung setzt das Schreiben die Zeit neu."""
        import os

        p = _jpeg(tmp_path / "wa.jpg")
        os.utime(p, (1_400_000_000, 1_400_000_000))
        before = os.path.getmtime(p)
        write_capture_time(p, WHEN, source="filename", dry_run=False,
                           preserve_mtime=False)
        assert abs(os.path.getmtime(p) - before) > 1

    def test_it_is_restored_by_default(self, tmp_path):
        import os

        p = _jpeg(tmp_path / "wa.jpg")
        os.utime(p, (1_400_000_000, 1_400_000_000))
        before = os.path.getmtime(p)
        out = write_capture_time(p, WHEN, source="filename", dry_run=False)
        assert out["mtime_restored"] is True
        assert abs(os.path.getmtime(p) - before) < 1

    def test_the_original_also_lands_in_the_note(self, tmp_path):
        """Zweite Absicherung, falls utime auf einem Mount nicht durchgeht."""
        import os

        p = _jpeg(tmp_path / "wa.jpg")
        os.utime(p, (1_400_000_000, 1_400_000_000))
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        source, prev, mtime = read_note(p)
        assert source == "filename"
        assert mtime.startswith("2014-05-13")


CAPTION = "Zwei Personen sitzen im Garten. Auf dem Tisch steht eine Flasche."


class TestCaptionInTheFile:
    def test_writes_description_and_windows_comment(self, tmp_path):
        p = _jpeg(tmp_path / "party.jpg")
        out = write_caption(p, CAPTION, source="llm", dry_run=False)
        assert out["written"] is True
        assert read_caption(p) == CAPTION
        from ingest.exif_writer import _decode_xp
        import piexif
        assert _decode_xp(piexif.load(p)["0th"][0x9C9C]) == CAPTION

    def test_marks_the_sentence_as_ours(self, tmp_path):
        p = _jpeg(tmp_path / "party.jpg")
        write_caption(p, CAPTION, source="llm", dry_run=False)
        from ingest.exif_writer import note_fields
        import piexif
        fields = note_fields(piexif.load(p)["Exif"][37510])
        assert fields["cap"] == "llm"
        assert "src" not in fields

    def test_does_not_overwrite_a_foreign_description(self, tmp_path):
        p = _jpeg(tmp_path / "cam.jpg")
        import piexif
        exif = piexif.load(p)
        exif.setdefault("0th", {})[270] = "Studioaufnahme".encode("utf-8")
        piexif.insert(piexif.dump(exif), p)
        out = write_caption(p, CAPTION, source="llm", dry_run=False)
        assert out["written"] is False
        assert read_caption(p) == "Studioaufnahme"

    def test_replaces_a_sentence_we_wrote(self, tmp_path):
        p = _jpeg(tmp_path / "party.jpg")
        write_caption(p, "Alte Fassung.", source="llm", dry_run=False)
        out = write_caption(p, CAPTION, source="llm", dry_run=False)
        assert out["written"] is True
        assert read_caption(p) == CAPTION

    def test_keeps_a_date_note(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        write_caption(p, CAPTION, source="llm", dry_run=False)
        assert read_note(p)[0] == "filename"
        assert read_capture_time(p) == WHEN
        assert read_caption(p) == CAPTION

    def test_date_write_keeps_the_caption_marker(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        write_caption(p, CAPTION, source="llm", dry_run=False)
        write_capture_time(p, WHEN, source="filename", dry_run=False)
        from ingest.exif_writer import note_fields
        import piexif
        fields = note_fields(piexif.load(p)["Exif"][37510])
        assert fields["src"] == "filename"
        assert fields["cap"] == "llm"
        assert read_caption(p) == CAPTION

    def test_mtime_survives(self, tmp_path):
        import os
        p = _jpeg(tmp_path / "wa.jpg")
        os.utime(p, (1_400_000_000, 1_400_000_000))
        before = os.path.getmtime(p)
        write_caption(p, CAPTION, source="llm", dry_run=False)
        assert abs(os.path.getmtime(p) - before) < 1

    def test_pixels_are_untouched(self, tmp_path):
        from PIL import Image
        p = _jpeg(tmp_path / "wa.jpg")
        before = Image.open(p).tobytes()
        write_caption(p, CAPTION, source="llm", dry_run=False)
        assert Image.open(p).tobytes() == before

    def test_dry_run_changes_nothing(self, tmp_path):
        p = _jpeg(tmp_path / "wa.jpg")
        before = open(p, "rb").read()
        out = write_caption(p, CAPTION, source="llm", dry_run=True)
        assert out["written"] is False
        assert open(p, "rb").read() == before

    def test_a_broken_camera_tag_does_not_block_the_write(self, tmp_path):
        """SceneType muss 1 Byte sein; manche Kameras schreiben ein int."""
        p = _jpeg(tmp_path / "cam.jpg")
        import piexif
        from ingest.exif_writer import _safe_dump
        exif = piexif.load(p)
        exif.setdefault("Exif", {})[41729] = 1
        with pytest.raises(ValueError):
            piexif.dump(exif)
        piexif.insert(_safe_dump(exif), p)
        out = write_caption(p, CAPTION, source="llm", dry_run=False)
        assert out["written"] is True
        assert read_caption(p) == CAPTION
