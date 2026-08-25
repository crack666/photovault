"""Kopfzeile und eingebettetes Dokument."""
from ingest.grounding import (
    caption_display,
    caption_for_vector,
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

    def test_a_given_series_name_beats_the_dump_folder(self):
        assert event_name({
            "folder_name": "HandyPics",
            "event_name": "Games Convention 2007",
        }) == "Games Convention 2007"


class TestCaptionDisplay:
    def test_full_header(self):
        head = caption_display({
            "date": "2015-08-23", "date_confidence": 1.0,
            "folder_name": "2016_04_23 Junggesellenabschied", "location": "Berlin",
        })
        assert head == "23. August 2015 · Sommer · Junggesellenabschied · Berlin"

    def test_named_series_beats_dump_folder_in_the_header(self):
        head = caption_display({
            "date": "2007-08-23", "date_confidence": 1.0,
            "folder_name": "HandyPics",
            "event_name": "Games Convention 2007",
        })
        assert "Games Convention 2007" in head
        assert "HandyPics" not in head

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
            person_names=["Ada Lovelace", "Alan Turing"],
            annotations=["Stripclub"],
        ))
        lines = doc.split("\n")
        assert lines[0] == "Personen: Ada Lovelace, Alan Turing"
        assert lines[1] == "Stripclub"
        assert "Piratenkostümen" in lines[2]
        assert lines[-1].startswith("23. August 2015")

    def test_annotations_reach_the_document(self):
        doc = grounded_document(self._payload(annotations=["Stripclub", "Bootstour"]))
        assert "Stripclub, Bootstour" in doc

    def test_suggestions_only_when_nothing_is_confirmed(self):
        confirmed = grounded_document(
            self._payload(person_names=["Ada Lovelace"], person_suggestions=["max"])
        )
        assert "Vermutlich" not in confirmed
        unconfirmed = grounded_document(self._payload(person_suggestions=["max"]))
        assert "Vermutlich: max" in unconfirmed

    def test_works_without_caption(self):
        doc = grounded_document(self._payload(caption_de=None, scene_tags=["party"]))
        assert "party" in doc
        assert "Junggesellenabschied" in doc

    def test_written_date_leaves_the_vector(self):
        """Das Datum steht schon in der Kopfzeile und im Payload — im Satz ist
        es die dritte Kopie. An 200 Fotos gemessen kostet sie Trennschärfe."""
        doc = grounded_document(self._payload(
            caption_de="Fünf Männer in Piratenkostümen, aufgenommen am 23. August 2015."
        ))
        assert "Piratenkostümen" in doc
        assert "23. August 2015." not in doc.split("\n")[0]
        # Die Kopfzeile behält das Datum — dort gehört es hin.
        assert doc.split("\n")[-1].startswith("23. August 2015")

    def test_caption_itself_is_untouched(self):
        """Für Menschen liest sich die Caption mit Datum besser. Nur der Vektor
        verzichtet darauf, also darf `caption_de` nicht angefasst werden."""
        payload = self._payload(caption_de="Ein Fest im August 2015.")
        grounded_document(payload)
        assert payload["caption_de"] == "Ein Fest im August 2015."


class TestCaptionForVector:
    def test_removes_written_month_and_year(self):
        assert caption_for_vector(
            "Ada Lovelace klettert im Juni 2024 an einer Seilbahn.", "2024-06-15"
        ) == "Ada Lovelace klettert an einer Seilbahn."

    def test_removes_the_trailing_shot_date(self):
        assert caption_for_vector(
            "Eine leere Autobahn, aufgenommen am 26. Juli 2025.", "2025-07-26"
        ) == "Eine leere Autobahn."

    def test_removes_the_boilerplate_sentence_entirely(self):
        """„Das Foto wurde am 3. Mai 2019 aufgenommen." lässt sich nicht in
        einem Zug fassen — der Punkt in „3." beendet jede Zeichenstrecke."""
        assert caption_for_vector(
            "Zwei Personen im Garten. Das Foto wurde am 3. Mai 2019 aufgenommen.",
            "2019-05-03",
        ) == "Zwei Personen im Garten."

    def test_removes_a_numeric_date(self):
        assert caption_for_vector(
            "Screenshot eines Artikels vom 30.11.2022 über Fahrverbote.", "2022-12-01"
        ) == "Screenshot eines Artikels über Fahrverbote."

    def test_a_foreign_year_is_content_and_stays(self):
        """„WM 2014" auf einem Foto von 2018 ist keine Datumsangabe."""
        text = "Ein Mann mit WM 2014 Trikot auf einer Feier."
        assert caption_for_vector(text, "2018-10-21") == text

    def test_a_year_in_a_name_survives_when_it_is_not_the_photo_year(self):
        text = "Vier Freunde beim Abi 08 auf dem Schulhof, aufgenommen im Juni 2008."
        assert caption_for_vector(text, "2008-06-27") == "Vier Freunde beim Abi 08 auf dem Schulhof."

    def test_caption_without_a_date_is_returned_as_is(self):
        text = "Eine Person hält ein Bierglas."
        assert caption_for_vector(text, "2020-01-01") == text

    def test_missing_date_only_strips_the_phrases(self):
        assert caption_for_vector("Ein Fest im August 2015.", None) == "Ein Fest."
