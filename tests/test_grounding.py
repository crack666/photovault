"""Kopfzeile und eingebettetes Dokument."""
from ingest.grounding import (
    caption_display,
    event_name,
    format_date,
    grounded_document,
    season,
)


class TestSeason:
    def test_meteorological_seasons(self):
        assert season("2015-08-23") == "Sommer"
        assert season("2015-01-01") == "Winter"
        assert season("2015-12-31") == "Winter"
        assert season("2015-04-23") == "Frühling"
        assert season("2015-10-31") == "Herbst"

    def test_missing_or_short_date(self):
        assert season(None) is None
        assert season("2015") is None


class TestFormatDate:
    def test_exif_gets_full_precision(self):
        assert format_date("2015-08-23", 1.0) == "23. August 2015"

    def test_medium_confidence_drops_the_day(self):
        assert format_date("2015-08-23", 0.75) == "August 2015"

    def test_guessed_date_is_marked_as_approximate(self):
        """Ein aus der Dateizeit geratenes Datum darf keine Praezision vortaeuschen."""
        assert format_date("2018-10-31", 0.3) == "um Oktober 2018"

    def test_year_only(self):
        assert format_date("2015", 0.8) == "2015"

    def test_missing(self):
        assert format_date(None, 1.0) is None


class TestEventName:
    def test_leading_date_is_stripped(self):
        assert event_name({"folder_name": "2016_04_23 Junggesellenabschied"}) == "Junggesellenabschied"

    def test_trailing_year_is_stripped(self):
        assert event_name({"folder_name": "Weihnachtsfeier 2010"}) == "Weihnachtsfeier"

    def test_camera_folder_is_not_an_event(self):
        assert event_name({"folder_name": "100MSDCF"}) is None
        assert event_name({"folder_name": "DCIM"}) is None

    def test_generic_folder_is_not_an_event(self):
        assert event_name({"folder_name": "Fotos"}) is None

    def test_plain_name_survives(self):
        assert event_name({"folder_name": "groemitz"}) == "groemitz"


class TestCaptionDisplay:
    def test_full_header(self):
        head = caption_display({
            "date": "2015-08-23", "date_confidence": 1.0,
            "folder_name": "2016_04_23 Junggesellenabschied", "location": "Berlin",
        })
        assert head == "23. August 2015 · Sommer · Junggesellenabschied · Berlin"

    def test_location_is_not_repeated_when_it_equals_the_event(self):
        head = caption_display({
            "date": "2011-07-02", "date_confidence": 1.0,
            "folder_name": "groemitz", "location": "groemitz",
        })
        assert head == "2. Juli 2011 · Sommer · groemitz"

    def test_empty_payload(self):
        assert caption_display({}) is None


class TestGroundedDocument:
    def _payload(self, **kw):
        base = {
            "date": "2015-08-23", "date_confidence": 1.0,
            "folder_name": "Junggesellenabschied", "location": "Berlin",
            "caption_de": "Fünf Männer in Piratenkostümen vor gelben Fahrrad-Taxis.",
        }
        base.update(kw)
        return base

    def test_names_and_notes_come_before_the_shared_context(self):
        """Album-Kontext ist bei allen Fotos gleich und darf den Vektor nicht dominieren."""
        doc = grounded_document(self._payload(
            person_names=["Michael Braun", "Jonas Meyer"],
            annotations=["Stripclub"],
        ))
        lines = doc.split("\n")
        assert lines[0] == "Personen: Michael Braun, Jonas Meyer"
        assert lines[1] == "Stripclub"
        assert "Piratenkostümen" in lines[2]
        assert lines[-1].startswith("23. August 2015")

    def test_annotations_reach_the_document(self):
        doc = grounded_document(self._payload(annotations=["Stripclub", "Bootstour"]))
        assert "Stripclub, Bootstour" in doc

    def test_suggestions_only_when_nothing_is_confirmed(self):
        confirmed = grounded_document(
            self._payload(person_names=["Michael Braun"], person_suggestions=["max"])
        )
        assert "Vermutlich" not in confirmed
        unconfirmed = grounded_document(self._payload(person_suggestions=["max"]))
        assert "Vermutlich: max" in unconfirmed

    def test_works_without_caption(self):
        doc = grounded_document(self._payload(caption_de=None, scene_tags=["party"]))
        assert "party" in doc
        assert "Junggesellenabschied" in doc
