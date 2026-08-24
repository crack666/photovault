from types import SimpleNamespace

from api.main import app
from api.routes.persons import _timeline_from_points


def _photo(pid, date, folder, seq=1):
    return SimpleNamespace(
        id=pid,
        payload={
            "date": date,
            "folder_name": folder,
            "caption_de": None,
            "caption_display": None,
            "person_names": [],
            "annotations": [],
            "sequence_in_folder": seq,
        },
    )


def test_groups_by_year_and_album():
    photos = [
        _photo("b", "2015-07-02", "Griechenland 2015", 2),
        _photo("a", "2015-07-01", "Griechenland 2015", 1),
        _photo("c", "2016-01-01", "Silvester", 1),
    ]
    out = _timeline_from_points(photos, name="Unbekannt")
    assert out["total"] == 3
    assert [y["year"] for y in out["years"]] == ["2015", "2016"]
    assert out["years"][0]["count"] == 2
    assert out["years"][0]["events"][0]["photos"][0]["id"] == "a"


def test_gallery_route_is_registered():
    """POST /gallery darf nicht von DELETE /{person_id} als 405 verschluckt werden."""
    spec = app.openapi()
    gallery = spec["paths"].get("/api/persons/gallery") or {}
    assert "post" in gallery, sorted(spec["paths"])


# --- Ereignisse statt (Ordner, Datum) -------------------------------------

def _p(pid, taken_at, folder="Album", chan="camera", date=None, people=None):
    from types import SimpleNamespace
    return SimpleNamespace(id=pid, payload={
        "date": date if date is not None else (taken_at or "")[:10],
        "taken_at": taken_at, "channel": chan, "folder_name": folder,
        "person_names": people or [], "annotations": [], "sequence_in_folder": None,
        "caption_display": None, "caption_de": None, "file_path": f"/p/{pid}.jpg",
    })


class TestEventsInTheTimeline:
    def test_an_evening_is_one_event_across_midnight(self):
        """Nach (Ordner, Datum) zerfiel ein durchgehender Abend um Mitternacht."""
        photos = [_p("a", "2012-12-31T23:30:00Z"), _p("b", "2013-01-01T00:30:00Z")]
        out = _timeline_from_points(photos)
        events = [e for y in out["years"] for e in y["events"]]
        assert len(events) == 1
        assert events[0]["span_minutes"] == 60

    def test_morning_and_evening_are_separate_events(self):
        photos = [_p("a", "2011-10-23T09:00:00Z"), _p("b", "2011-10-23T20:00:00Z")]
        events = [e for y in _timeline_from_points(photos)["years"] for e in y["events"]]
        assert len(events) == 2

    def test_a_screenshot_does_not_join_the_party(self):
        photos = [_p("foto", "2024-09-15T14:00:00Z"),
                  _p("shot", "2024-09-15T14:02:00Z", chan="screenshot")]
        events = [e for y in _timeline_from_points(photos)["years"] for e in y["events"]]
        assert len(events) == 2
        assert {e["channel"] for e in events} == {"camera", "screenshot"}

    def test_the_year_counts_photos_per_channel(self):
        """Damit der Zeitstrahl zeigen kann, woraus ein Jahr besteht."""
        photos = [_p("a", "2024-09-15T14:00:00Z"),
                  _p("b", "2024-09-15T14:01:00Z"),
                  _p("c", "2024-09-15T18:00:00Z", chan="whatsapp")]
        year = _timeline_from_points(photos)["years"][0]
        assert year["channels"] == {"camera": 2, "whatsapp": 1}
        assert year["count"] == 3

    def test_day_level_events_report_no_span(self):
        """Bei Mitternachts-Stempeln waere jede Uhrzeitspanne erfunden."""
        photos = [_p("a", "2018-10-20T00:00:00Z"), _p("b", "2018-10-20T00:00:00Z")]
        event = _timeline_from_points(photos)["years"][0]["events"][0]
        assert event["day_level"] is True
        assert event["span_minutes"] is None

    def test_photos_without_a_timestamp_still_appear(self):
        """Aeltere Datensaetze haben kein taken_at -- sie duerfen nicht
        aus dem Zeitstrahl fallen."""
        photos = [_p("alt", None, date="2015-05-05")]
        out = _timeline_from_points(photos)
        assert out["total"] == 1
        assert out["years"][0]["year"] == "2015"

    def test_people_of_an_event_are_collected(self):
        photos = [_p("a", "2011-10-23T18:30:00Z", people=["Marco"]),
                  _p("b", "2011-10-23T18:35:00Z", people=["Mareike", "Marco"])]
        event = _timeline_from_points(photos)["years"][0]["events"][0]
        assert event["person_names"] == ["Marco", "Mareike"]

    def test_folders_of_a_merged_event_are_all_listed(self):
        """Abiball, Abistreich und Abiverleihung sind eine Gelegenheit."""
        photos = [_p("a", "2008-06-27T16:31:00Z", folder="Abiball"),
                  _p("b", "2008-06-27T17:00:00Z", folder="Abistreich")]
        event = _timeline_from_points(photos)["years"][0]["events"][0]
        assert event["folders"] == ["Abiball", "Abistreich"]


class TestMonths:
    """Ein Jahr mit 3000 Fotos ist als Block unbrauchbar; drei Monatsbaender
    machen daraus etwas Greifbares. Bei wenigen Fotos entfaellt die Ebene."""

    def _many(self, n, month="09"):
        return [_p(f"p{month}{i}", f"2024-{month}-{(i % 28) + 1:02d}T1{i % 8}:00:00Z")
                for i in range(n)]

    def test_a_big_year_gets_month_bands(self):
        from api.routes.persons import MONTHS_FROM
        photos = self._many(MONTHS_FROM, "03") + self._many(MONTHS_FROM, "09")
        year = _timeline_from_points(photos)["years"][0]
        assert [m["month"] for m in year["months"]] == ["2024-03", "2024-09"]
        assert sum(m["count"] for m in year["months"]) == year["count"]

    def test_a_small_year_gets_none(self):
        year = _timeline_from_points(self._many(4))["years"][0]
        assert year["months"] == []

    def test_a_single_month_gets_none(self):
        """Ein Band ueber dem ganzen Jahr gliedert nichts."""
        from api.routes.persons import MONTHS_FROM
        year = _timeline_from_points(self._many(MONTHS_FROM + 10))["years"][0]
        assert year["months"] == []

    def test_events_stay_reachable_without_months(self):
        year = _timeline_from_points(self._many(4))["years"][0]
        assert year["events"]


class TestSuggestedName:
    """Der Ordnername benennt die Gelegenheit meist schon richtig. Ein
    Vorschlag macht aus dem Benennen ein Bestätigen."""

    def test_folder_name_is_proposed(self):
        from api.routes.events import suggest_name
        assert suggest_name(["18. Geburtstag"], "2007-09-30") == \
            "18. Geburtstag 2007"

    def test_an_existing_year_is_not_doubled(self):
        from api.routes.events import suggest_name
        assert suggest_name(["Silvester 2012-2013"], "2012-12-31") == "Silvester 2012-2013"

    def test_the_year_disambiguates_recurring_occasions(self):
        """„Weihnachtsfeier" gibt es jedes Jahr."""
        from api.routes.events import suggest_name
        a = suggest_name(["Weihnachtsfeier"], "2009-12-11")
        b = suggest_name(["Weihnachtsfeier"], "2010-12-08")
        assert a != b

    def test_generic_folders_propose_nothing(self):
        """Sonst heissen zwanzig Serien „Fotos"."""
        from api.routes.events import suggest_name
        for folder in ("Fotos", "Handyfotos", "WhatsApp Images", "DCIM",
                       "Screenshots", "Neuer Ordner", "Sent"):
            assert suggest_name([folder], "2020-01-01") == "", folder

    def test_no_folder_no_proposal(self):
        from api.routes.events import suggest_name
        assert suggest_name([], "2020-01-01") == ""

    def test_the_first_folder_wins_when_several_merged(self):
        from api.routes.events import suggest_name
        assert suggest_name(["Abiball", "Abistreich"], "2008-06-27") == "Abiball 2008"
