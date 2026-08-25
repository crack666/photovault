"""✕ nimmt ein Foto aus der Serie — die Zeitscheibe darf es nicht zurückholen."""
from api.routes.events import _event_from_ids, _named_groups, _named_member_ids, _summarise
from ingest.events import Event


def _row(event_name=None, folder="HandyPics", taken="2013-06-12T19:06:00",
         path="/mnt/photo/Handys/HandyPics/a.jpg", channel="camera",
         excluded=False):
    r = {
        "channel": channel,
        "taken_at": taken,
        "folder_name": folder,
        "file_path": path,
        "person_names": [],
        "event_excluded": excluded,
    }
    if event_name is not None:
        r["event_name"] = event_name
    return r


NASEN = "Nasen OP"


class TestNamedMembers:
    def test_after_shelve_dump_photos_stay_out(self):
        rows = {
            "keep": _row(
                folder=NASEN, path=f"/mnt/photo/Fotos/{NASEN}/a.jpg",
                taken="2013-06-12T19:06:00",
            ),
            "judo": _row(
                folder="HandyPics", path="/mnt/photo/Handys/HandyPics/b.jpg",
                taken="2013-06-12T19:10:00",
            ),
            "dojo": _row(
                folder="ju jutsu", path="/mnt/photo/Fotos/ju jutsu/c.jpg",
                taken="2013-06-12T19:11:00",
            ),
        }
        assert _named_member_ids(["keep", "judo", "dojo"], rows, NASEN) == ["keep"]

    def test_unshelved_dump_keeps_everyone_except_excluded(self):
        rows = {
            "a": _row(taken="2013-06-12T19:06:00"),
            "b": _row(taken="2013-06-12T19:08:00"),
            "c": _row(taken="2013-06-12T19:10:00", excluded=True),
        }
        assert _named_member_ids(["a", "b", "c"], rows, NASEN) == ["a", "b"]

    def test_excluded_flag_drops_even_in_home_folder(self):
        rows = {
            "keep": _row(folder=NASEN, path=f"/mnt/photo/Fotos/{NASEN}/a.jpg"),
            "out": _row(
                folder=NASEN, path=f"/mnt/photo/Fotos/{NASEN}/b.jpg",
                excluded=True,
            ),
        }
        assert _named_member_ids(["keep", "out"], rows, NASEN) == ["keep"]

    def test_other_series_stamp_is_not_claimed(self):
        rows = {
            "keep": _row(event_name=NASEN, folder=NASEN),
            "other": _row(event_name="Judo", folder="HandyPics"),
        }
        assert _named_member_ids(["keep", "other"], rows, NASEN) == ["keep"]


class TestNamedGroups:
    def test_nasen_op_does_not_swallow_judo_after_shelve(self):
        rows = {
            "keep": _row(
                folder=NASEN, path=f"/mnt/photo/Fotos/{NASEN}/a.jpg",
                taken="2013-06-12T19:06:00",
            ),
            "judo": _row(
                folder="HandyPics", path="/mnt/photo/Handys/HandyPics/b.jpg",
                taken="2013-06-12T19:10:00",
            ),
            "dojo": _row(
                folder="ju jutsu", path="/mnt/photo/Fotos/ju jutsu/c.jpg",
                taken="2013-06-12T19:11:00",
            ),
        }
        names = [{
            "name": NASEN,
            "channel": "camera",
            "start": "2013-06-12T19:06:00",
            "end": "2013-06-12T19:12:00",
        }]
        groups, open_rows = _named_groups(rows, names)
        assert groups[("camera", NASEN)] == ["keep"]
        assert "judo" in open_rows
        assert "dojo" in open_rows

    def test_excluded_photo_is_open_even_without_home_folder(self):
        rows = {
            "a": _row(taken="2013-06-12T19:06:00"),
            "b": _row(taken="2013-06-12T19:10:00", excluded=True),
        }
        names = [{
            "name": NASEN,
            "channel": "camera",
            "start": "2013-06-12T19:06:00",
            "end": "2013-06-12T19:12:00",
        }]
        groups, open_rows = _named_groups(rows, names)
        assert groups[("camera", NASEN)] == ["a"]
        assert "b" in open_rows


class TestEventFromIds:
    def test_orders_by_time_and_keeps_span(self):
        rows = {
            "b": _row(event_name=NASEN, taken="2013-06-12T19:12:00"),
            "a": _row(event_name=NASEN, taken="2013-06-12T19:06:00"),
        }
        ev = _event_from_ids(["b", "a"], rows, "camera")
        assert ev.photo_ids == ["a", "b"]
        assert ev.start.hour == 19 and ev.start.minute == 6
        assert ev.end.minute == 12
        assert ev.day_level is False


class TestSummariseMembership:
    def test_forced_name_does_not_pull_in_neighbors(self):
        rows = {
            "keep": _row(
                event_name=NASEN, folder=NASEN,
                path=f"/mnt/photo/Fotos/{NASEN}/a.jpg",
            ),
            "judo": _row(folder="HandyPics"),
        }
        ev = Event(photo_ids=["keep"], channel="camera")
        from datetime import datetime
        ev.start = datetime(2013, 6, 12, 19, 6)
        ev.end = datetime(2013, 6, 12, 19, 12)
        names = [{
            "name": NASEN,
            "channel": "camera",
            "start": "2013-06-12T19:06:00",
            "end": "2013-06-12T19:12:00",
        }]
        out = _summarise(ev, rows, names, forced_name=NASEN)
        assert out["photo_ids"] == ["keep"]
        assert out["folders"] == [NASEN]
        assert "HandyPics" not in out["folders"]
        assert out["name"] == NASEN
        assert out["needs_shelve"] is False
