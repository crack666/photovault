"""Nachbar-Serien vorschlagen, nicht still zusammenlegen."""
from datetime import timedelta

from ingest.event_suggest import (
    already_one_album,
    coalesce_same_album,
    neighbor_score,
    neighbor_suggestions,
    timestamp_suggestions,
    unify_folder_suggestions,
)
from ingest.events import NEIGHBOR_MAX_GAP, is_generic_album


def _ev(**kw):
    base = {
        "channel": "camera",
        "start": "2011-10-23T09:00:00",
        "end": "2011-10-23T10:00:00",
        "folders": ["HandyPics"],
        "person_names": [],
        "size": 12,
        "day_level": False,
        "photo_ids": ["a"],
        "cover": ["a"],
    }
    base.update(kw)
    return base


class TestGenericAlbum:
    def test_handypics_is_generic(self):
        assert is_generic_album("HandyPics")
        assert is_generic_album("Fotos")

    def test_gc_is_not(self):
        assert not is_generic_album("GC 07")


class TestNeighborScore:
    def test_a_five_hour_lunch_break_in_the_same_album_is_already_done(self):
        """Weihnachtsfeier abends und morgens: derselbe Ordner, nichts zu legen."""
        a = _ev(end="2011-10-23T12:00:00", folders=["GC 07"], person_names=["Annika"],
                album_paths=["/mnt/photo/Fotos/GC 07"])
        b = _ev(start="2011-10-23T17:00:00", folders=["GC 07"], person_names=["Annika"],
                album_paths=["/mnt/photo/Fotos/GC 07"])
        assert already_one_album(a, b) is True
        assert neighbor_score(a, b) is None

    def test_two_named_albums_hours_apart_are_still_a_candidate(self):
        a = _ev(end="2008-06-27T12:00:00", folders=["Abistreich"],
                album_paths=["/mnt/photo/Fotos/Abi 08/Abistreich"])
        b = _ev(start="2008-06-27T18:00:00", folders=["Abiball"],
                album_paths=["/mnt/photo/Fotos/Abi 08/Abiball"])
        assert already_one_album(a, b) is False
        assert neighbor_score(a, b) is not None

    def test_under_three_hours_is_already_one_event(self):
        a = _ev(end="2011-10-23T12:00:00")
        b = _ev(start="2011-10-23T14:00:00")
        assert neighbor_score(a, b) is None

    def test_thirteen_hours_is_another_day(self):
        a = _ev(end="2011-10-23T08:00:00")
        b = _ev(start="2011-10-23T22:00:00")
        assert NEIGHBOR_MAX_GAP < timedelta(hours=14)
        assert neighbor_score(a, b) is None

    def test_dump_without_people_is_not_suggested(self):
        a = _ev(end="2011-10-23T12:00:00", folders=["HandyPics"])
        b = _ev(start="2011-10-23T17:00:00", folders=["HandyPics"])
        assert neighbor_score(a, b) is None

    def test_whatsapp_neighbors_are_not_suggested(self):
        a = _ev(channel="whatsapp", end="2018-10-21T12:00:00", folders=["WhatsApp"])
        b = _ev(channel="whatsapp", start="2018-10-21T17:00:00", folders=["WhatsApp"])
        assert neighbor_score(a, b) is None

    def test_channels_do_not_mix_here(self):
        a = _ev(end="2011-10-23T12:00:00", folders=["GC 07"])
        b = _ev(channel="whatsapp", start="2011-10-23T17:00:00", folders=["WhatsApp"])
        assert neighbor_score(a, b) is None


class TestNeighborList:
    def test_orders_by_score_and_skips_rejects(self):
        morning = _ev(start="2011-10-23T09:00:00", end="2011-10-23T10:00:00",
                      folders=["Abistreich"], album_paths=["/Fotos/Abistreich"],
                      person_names=["Annika"])
        afternoon = _ev(start="2011-10-23T16:00:00", end="2011-10-23T18:00:00",
                        folders=["Abiball"], album_paths=["/Fotos/Abiball"],
                        person_names=["Annika"])
        out = neighbor_suggestions([morning, afternoon])
        assert len(out) == 1
        assert out[0]["kind"] == "neighbor"
        blocked = neighbor_suggestions(
            [morning, afternoon],
            rejected=[(("camera", morning["start"], morning["end"]),
                       ("camera", afternoon["start"], afternoon["end"]))],
        )
        assert blocked == []


class TestCoalesceSameAlbum:
    def test_evening_and_morning_in_one_named_folder_become_one_event(self):
        evening = _ev(
            start="2009-12-18T19:00:00", end="2009-12-19T01:00:00",
            folders=["Weihnachtsfeier 2009"], size=51,
            photo_ids=["e1", "e2"],
            album_paths=["/mnt/photo/Fotos/Weihnachtsfeier 2009"],
        )
        morning = _ev(
            start="2009-12-19T11:00:00", end="2009-12-19T12:00:00",
            folders=["Weihnachtsfeier 2009"], size=14,
            photo_ids=["m1"],
            album_paths=["/mnt/photo/Fotos/Weihnachtsfeier 2009"],
        )
        got = coalesce_same_album([evening, morning])
        assert len(got) == 1
        assert got[0]["size"] == 3
        assert set(got[0]["photo_ids"]) == {"e1", "e2", "m1"}
        assert neighbor_suggestions(got) == []


class TestUnifyFolders:
    def test_dump_plus_named_is_a_card(self):
        ev = _ev(folders=["HandyPics", "GC 07"], size=40, name="Games Convention 2007")
        got = unify_folder_suggestions([ev])
        assert len(got) == 1
        assert got[0]["reason"] == "dump_plus_named"

    def test_single_folder_is_silent(self):
        assert unify_folder_suggestions([_ev(folders=["GC 07"])]) == []


class TestTimestamp:
    def test_same_second_across_channels(self):
        photos = [
            {"id": "cam", "taken_at": "2011-10-23T18:30:05", "channel": "camera",
             "folder_name": "GC 07"},
            {"id": "wa", "taken_at": "2011-10-23T18:30:06", "channel": "whatsapp",
             "folder_name": "WhatsApp"},
        ]
        got = timestamp_suggestions(photos)
        assert len(got) == 1
        assert got[0]["delta_seconds"] == 1

    def test_same_channel_is_not_a_cross_match(self):
        photos = [
            {"id": "a", "taken_at": "2011-10-23T18:30:05", "channel": "camera"},
            {"id": "b", "taken_at": "2011-10-23T18:30:05", "channel": "camera"},
        ]
        assert timestamp_suggestions(photos) == []

    def test_minutes_apart_is_not_enough(self):
        photos = [
            {"id": "cam", "taken_at": "2011-10-23T18:30:00", "channel": "camera"},
            {"id": "wa", "taken_at": "2011-10-23T18:35:00", "channel": "whatsapp"},
        ]
        assert timestamp_suggestions(photos) == []
