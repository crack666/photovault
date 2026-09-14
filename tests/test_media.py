from ingest.media import kind_of
from ingest.video import poster_offset, sample_offsets


def test_kind_of_photo():
    assert kind_of("/a/b.jpg") == "photo"
    assert kind_of("/a/b.HEIC") == "photo"


def test_kind_of_video():
    assert kind_of("/a/VID-20181021-WA0001.mp4") == "video"
    assert kind_of("/a/clip.MOV") == "video"


def test_poster_offset_skips_the_first_instant():
    assert poster_offset(30) == 3.0
    assert poster_offset(1) == 0.0
    assert poster_offset(None) == 0.0


def test_short_clip_is_one_caption_frame():
    assert sample_offsets(1.2) == [0.0]
    assert sample_offsets(None) == [0.0]


def test_longer_clip_is_three_caption_frames():
    assert sample_offsets(30) == [3.0, 15.0, 27.0]
