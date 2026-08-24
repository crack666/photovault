from ingest.normalizer import Normalizer
from ingest.pipeline import PhotoRecord


class TestNormalizer:
    def setup_method(self):
        self.normalizer = Normalizer()

    def test_exif_date_wins_over_folder(self):
        record = PhotoRecord(
            photo_id="test1",
            file_path="/photos/Griechenland 2015/IMG_0042.jpg",
            date="2015-07-14",
            date_source="exif",
            date_confidence=1.0,
            folder_name="Griechenland 2015",
            date_hint="2014",
            date_hint_source="folder_name",
        )
        self.normalizer.normalize(record)
        assert record.date == "2015-07-14"
        assert record.date_source == "exif"
        assert record.taken_at == "2015-07-14T00:00:00Z"

    def test_folder_json_date_hint(self):
        record = PhotoRecord(
            photo_id="test2",
            file_path="/photos/Urlaub Griechenland/IMG_0001.jpg",
            folder_name="Urlaub Griechenland",
            date_hint="2015",
            date_hint_source="folder_json",
        )
        self.normalizer.normalize(record)
        assert record.date == "2015-01-01"
        assert record.date_source == "folder_json"
        assert record.taken_at == "2015-01-01T00:00:00Z"

    def test_year_from_folder_name(self):
        record = PhotoRecord(
            photo_id="test3",
            file_path="/photos/Griechenland 2015/IMG_0042.jpg",
            folder_name="Griechenland 2015",
        )
        self.normalizer.normalize(record)
        assert record.date == "2015-01-01"
        assert record.date_source == "folder"

    def test_filename_date(self):
        record = PhotoRecord(
            photo_id="test4",
            file_path="/photos/Misc/2019-03-15_Birthday.jpg",
            folder_name="Misc",
        )
        self.normalizer.normalize(record)
        assert record.date == "2019-03-15"
        assert record.date_source == "filename"

    def test_no_date(self):
        record = PhotoRecord(photo_id="test5", file_path="/photos/DCIM/IMG_0001.jpg")
        self.normalizer.normalize(record)
        assert record.date is None
        assert record.taken_at is None

    def test_location_from_hint(self):
        record = PhotoRecord(
            photo_id="test6",
            file_path="/photos/Griechenland 2015/IMG_0042.jpg",
            folder_name="Griechenland 2015",
            location_hint="Griechenland 2015",
            location_key="griechenland",
        )
        self.normalizer.normalize(record)
        assert record.location == "Griechenland 2015"
        assert record.location_key == "griechenland"
        assert record.location_lc == "griechenland"
        assert record.location_source == "folder"

    def test_folder_geburtstag_tag(self):
        record = PhotoRecord(
            photo_id="bday",
            file_path="/photos/18. Geburtstag/DSCF0001.JPG",
            folder_name="18. Geburtstag",
        )
        self.normalizer.normalize(record)
        assert "geburtstag" in record.scene_tags

    def test_folder_people_not_scene_tags(self):
        record = PhotoRecord(
            photo_id="test7",
            file_path="/photos/Griechenland 2015/IMG_0042.jpg",
            folder_people=["Jonas", "Max"],
            scene_tags=["strand"],
        )
        self.normalizer.normalize(record)
        assert record.scene_tags == ["strand"]
        assert record.person_ids == []
