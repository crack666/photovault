"""Ereignisbildung auf der Zeitachse."""
from __future__ import annotations

from datetime import timedelta

from ingest.events import DEFAULT_GAP, cluster, describe, parse_stamp


def _stamp(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00Z"


class TestParsing:
    def test_full_timestamp(self):
        dt, day_only = parse_stamp("2008-06-29T16:20:08Z")
        assert (dt.hour, dt.minute) == (16, 20)
        assert day_only is False

    def test_midnight_counts_as_day_only(self):
        """Mitternacht heisst hier: Uhrzeit unbekannt, nicht 0 Uhr."""
        dt, day_only = parse_stamp("2008-06-29T00:00:00Z")
        assert day_only is True

    def test_bare_date(self):
        dt, day_only = parse_stamp("2008-06-29")
        assert day_only is True

    def test_garbage(self):
        assert parse_stamp("keine Ahnung") == (None, False)
        assert parse_stamp(None) == (None, False)


class TestClustering:
    def test_a_burst_stays_together(self):
        items = [(f"p{i}", _stamp("2011-10-23", f"18:{30+i:02d}")) for i in range(8)]
        events = cluster(items)
        assert len(events) == 1
        assert events[0].size == 8

    def test_a_long_gap_splits(self):
        items = [
            ("morgens1", _stamp("2011-10-23", "09:00")),
            ("morgens2", _stamp("2011-10-23", "09:20")),
            ("abends1", _stamp("2011-10-23", "20:00")),
            ("abends2", _stamp("2011-10-23", "20:10")),
        ]
        events = cluster(items, gap=timedelta(hours=3))
        assert [e.size for e in events] == [2, 2]

    def test_the_gap_decides(self):
        items = [("a", _stamp("2011-10-23", "09:00")), ("b", _stamp("2011-10-23", "13:00"))]
        assert len(cluster(items, gap=timedelta(hours=3))) == 2
        assert len(cluster(items, gap=timedelta(hours=6))) == 1

    def test_a_chain_of_small_steps_stays_one_event(self):
        """Silvester lief 10 Stunden -- durchgehend fotografiert, ein Ereignis."""
        items = [(f"p{i}", _stamp("2012-12-31", f"{17+i//2:02d}:{(i % 2) * 30:02d}"))
                 for i in range(12)]
        assert len(cluster(items, gap=timedelta(hours=3))) == 1

    def test_events_span_midnight(self):
        items = [("a", _stamp("2012-12-31", "23:30")), ("b", _stamp("2013-01-01", "00:30"))]
        assert len(cluster(items, gap=timedelta(hours=3))) == 1

    def test_different_folders_may_share_an_event(self):
        """Zwei Handys auf derselben Feier gehoeren zusammen."""
        items = [("handyA", _stamp("2011-10-23", "18:30")),
                 ("handyB", _stamp("2011-10-23", "18:32"))]
        assert len(cluster(items)) == 1


class TestDayLevelStaysSeparate:
    def test_day_level_photos_group_per_day(self):
        items = [("a", "2018-10-20T00:00:00Z"), ("b", "2018-10-20T00:00:00Z"),
                 ("c", "2018-10-21T00:00:00Z")]
        events = cluster(items)
        assert sorted(e.size for e in events) == [1, 2]
        assert all(e.day_level for e in events)

    def test_day_level_does_not_swallow_timed_photos(self):
        """Sonst zieht ein Bestand voller Mitternachts-Stempel die echten
        Serien in einen Tagesklumpen."""
        items = [("grob", "2011-10-23T00:00:00Z"),
                 ("genau1", _stamp("2011-10-23", "18:30")),
                 ("genau2", _stamp("2011-10-23", "18:35"))]
        events = cluster(items)
        assert len(events) == 2
        timed = [e for e in events if not e.day_level]
        assert len(timed) == 1 and timed[0].size == 2

    def test_undated_photos_get_no_event(self):
        events = cluster([("ohne", None), ("auch_ohne", "")])
        assert events == []


class TestKeys:
    def test_key_is_stable_for_the_same_input(self):
        items = [("a", _stamp("2011-10-23", "18:30")), ("b", _stamp("2011-10-23", "18:35"))]
        assert cluster(items)[0].key() == cluster(items)[0].key()

    def test_key_changes_when_the_event_grows(self):
        base = [("a", _stamp("2011-10-23", "18:30"))]
        grown = base + [("b", _stamp("2011-10-23", "18:35"))]
        assert cluster(base)[0].key() != cluster(grown)[0].key()


class TestDescribe:
    def test_counts_add_up(self):
        items = [(f"p{i}", _stamp("2011-10-23", f"18:{30+i:02d}")) for i in range(5)]
        items.append(("grob", "2018-10-20T00:00:00Z"))
        d = describe(cluster(items))
        assert d["photos"] == 6
        assert d["day_level"] == 1

    def test_empty_is_not_an_error(self):
        assert describe([])["events"] == 0


def test_default_gap_is_three_hours():
    """Am echten Bestand gemessen: haelt Silvester (10,5 h) zusammen und
    trennt trotzdem Vormittag von Abend."""
    assert DEFAULT_GAP == timedelta(hours=3)


class TestChannelsDoNotMix:
    """Zeitliche Naehe allein reicht bei Handy-Material nicht.

    Am echten Bestand entstand ohne diese Trennung eine „Serie" ueber neun
    Stunden aus HandyPics, Screenshots und gesendeten WhatsApp-Bildern —
    zusammengehalten nur vom Kalender.
    """

    def test_a_screenshot_does_not_join_a_party(self):
        items = [("foto1", _stamp("2024-09-15", "14:00"), "camera"),
                 ("shot", _stamp("2024-09-15", "14:02"), "screenshot"),
                 ("foto2", _stamp("2024-09-15", "14:05"), "camera")]
        events = cluster(items)
        assert len(events) == 2
        by_chan = {e.channel: e.size for e in events}
        assert by_chan == {"camera": 2, "screenshot": 1}

    def test_sent_and_received_stay_apart(self):
        items = [("empf", _stamp("2018-10-21", "12:00"), "whatsapp"),
                 ("gesendet", _stamp("2018-10-21", "12:01"), "whatsapp-sent")]
        assert len(cluster(items)) == 2

    def test_two_phones_at_one_party_still_merge(self):
        """Der Kanal trennt Herkunftsarten, nicht Geraete."""
        items = [("handyA", _stamp("2011-10-23", "18:30"), "camera"),
                 ("handyB", _stamp("2011-10-23", "18:32"), "camera")]
        assert len(cluster(items)) == 1

    def test_day_level_photos_also_respect_the_channel(self):
        items = [("a", "2018-10-20T00:00:00Z", "whatsapp"),
                 ("b", "2018-10-20T00:00:00Z", "screenshot")]
        assert len(cluster(items)) == 2

    def test_the_key_names_the_channel(self):
        """Sonst kollidieren zwei Ereignisse, die zur selben Sekunde beginnen."""
        items = [("a", _stamp("2024-09-15", "14:00"), "camera"),
                 ("b", _stamp("2024-09-15", "14:00"), "screenshot")]
        keys = {e.key() for e in cluster(items)}
        assert len(keys) == 2

    def test_two_element_tuples_still_work(self):
        """Bestehende Aufrufer geben keinen Kanal an."""
        items = [("a", _stamp("2011-10-23", "18:30")), ("b", _stamp("2011-10-23", "18:35"))]
        events = cluster(items)
        assert len(events) == 1 and events[0].channel == "camera"
