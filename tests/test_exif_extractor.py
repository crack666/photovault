from api.routes.photos import _when_from_payload
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

    def test_gps_ref_is_not_the_coordinate(self, tmp_path):
        """Tag 1 ist N/S, Tag 2 das Grad-Tripel — nicht andersherum."""
        import piexif
        from PIL import Image

        p = tmp_path / "geo.jpg"
        Image.new("RGB", (32, 24), (10, 20, 30)).save(p, format="JPEG")
        exif = piexif.load(str(p))
        exif["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((52, 1), (25, 1), (101943, 10000)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((13, 1), (28, 1), (529181, 10000)),
        }
        piexif.insert(piexif.dump(exif), str(p))
        gps = self.extractor.extract(str(p))["gps"]
        assert gps is not None
        lat, lon = gps
        assert 52.4 < lat < 52.5
        assert 13.4 < lon < 13.5

    def test_original_beats_the_copy_timestamp(self, tmp_path):
        """DateTime (306) ist oft der Import auf den PC, nicht die Aufnahme."""
        import piexif
        from PIL import Image

        p = tmp_path / "party.jpg"
        Image.new("RGB", (32, 24), (10, 20, 30)).save(p, format="JPEG")
        exif = piexif.load(str(p))
        exif["0th"][piexif.ImageIFD.DateTime] = b"2009:01:20 02:02:03"
        exif.setdefault("Exif", {})[piexif.ExifIFD.DateTimeOriginal] = b"2006:12:17 11:58:58"
        piexif.insert(piexif.dump(exif), str(p))
        got = self.extractor.extract(str(p))
        assert got["date"] == "2006-12-17"
        assert got["datetime"] == "2006-12-17T11:58:58Z"

    def test_datetime_is_used_when_original_is_missing(self, tmp_path):
        import piexif
        from PIL import Image

        p = tmp_path / "scan.jpg"
        Image.new("RGB", (32, 24), (10, 20, 30)).save(p, format="JPEG")
        exif = piexif.load(str(p))
        exif["0th"][piexif.ImageIFD.DateTime] = b"2009:01:20 01:46:13"
        piexif.insert(piexif.dump(exif), str(p))
        got = self.extractor.extract(str(p))
        assert got["datetime"] == "2009-01-20T01:46:13Z"

    def test_west_and_south_are_negative(self):
        lat = ExifExtractor._gps_to_decimal((10, 0, 0), b"S")
        lon = ExifExtractor._gps_to_decimal((20, 0, 0), b"W")
        assert lat == -10.0
        assert lon == -20.0


def test_estimated_stamp_parses():
    assert _when_from_payload({"taken_at": "2018-10-21T14:30:05Z"}).hour == 14
    assert _when_from_payload({"date": "2009-12-17"}).year == 2009
