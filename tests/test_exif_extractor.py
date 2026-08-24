from ingest.exif_extractor import ExifExtractor

class TestExifExtractor:
    def setup_method(self):
        self.extractor = ExifExtractor()

    def test_no_exif(self, tmp_path):
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0fake")
        result = self.extractor.extract(str(test_file))
        assert result["date"] is None
        assert result["gps"] is None

    def test_gps_conversion(self):
        from fractions import Fraction
        lat = ExifExtractor._gps_to_decimal([Fraction(37), Fraction(58), Fraction(48)], "N")
        assert lat is not None
        assert 37.9 < lat < 38.0
        lon = ExifExtractor._gps_to_decimal([Fraction(23), Fraction(43), Fraction(12)], "E")
        assert lon is not None
        assert 23.7 < lon < 23.8
