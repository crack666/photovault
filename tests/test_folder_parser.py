from ingest.folder_parser import FolderParser

class TestFolderParser:
    def setup_method(self):
        self.parser = FolderParser()

    def test_sequence_from_img_name(self):
        result = self.parser.parse("/photos/Griechenland 2015/IMG_0042.jpg")
        assert result["sequence"] == 42

    def test_year_from_folder_name(self):
        result = self.parser.parse("/photos/Griechenland 2015/IMG_0042.jpg")
        assert result["date_hint"] == "2015"
        assert result["date_hint_source"] == "folder_name"

    def test_location_from_folder_name(self):
        result = self.parser.parse("/photos/Griechenland 2015/IMG_0042.jpg")
        assert result["location_hint"] is not None
        assert "Griechenland" in result["location_hint"]
        assert result["location_key"] == "griechenland"

    def test_dsc_sequence(self):
        result = self.parser.parse("/photos/Urlaub/DSC_1234.jpg")
        assert result["sequence"] == 1234

    def test_dscf_fujifilm_sequence(self):
        result = self.parser.parse("/photos/18. Geburtstag/DSCF0042.JPG")
        assert result["sequence"] == 42

    def test_date_in_filename(self):
        """Voller Tag, nicht nur das Jahr - sonst landet alles auf dem 1. Januar."""
        result = self.parser.parse("/photos/Misc/2019-03-15_Birthday.jpg")
        assert result["date_hint"] == "2019-03-15"
        assert result["date_hint_source"] == "filename"

    def test_no_metadata(self):
        result = self.parser.parse("/photos/DCIM/100CANON/IMG_0001.jpg")
        assert result["sequence"] == 1
        assert result["date_hint"] is None

    def test_whatsapp_name_gives_date_and_sequence(self):
        result = self.parser.parse("/photos/Junggesellenabschied/IMG-20181021-WA0120.jpg")
        assert result["date_hint"] == "2018-10-21"
        assert result["date_hint_source"] == "filename"
        assert result["sequence"] == 120

    def test_compact_date_in_filename(self):
        result = self.parser.parse("/photos/Handyfotos/20130515_223527 Basti Kino.jpg")
        assert result["date_hint"] == "2013-05-15"
        assert result["date_hint_source"] == "filename"

    def test_long_digit_run_is_not_a_date(self):
        """30052010002.jpg darf kein Datum aus der Mitte einer Ziffernkette ziehen."""
        result = self.parser.parse("/photos/Handyfotos/30052010002.jpg")
        assert result["date_hint"] is None

    def test_folder_year_beats_no_filename_date(self):
        result = self.parser.parse("/photos/Silvester 2012-2013/DSCF0042.jpg")
        assert result["date_hint"] == "2012"
        assert result["date_hint_source"] == "folder_name"
