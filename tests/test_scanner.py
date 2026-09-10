from pathlib import Path

from ingest.scanner import _is_image


def test_skips_appledouble(tmp_path: Path):
    junk = tmp_path / "._DSCF0001.JPG"
    junk.write_bytes(b"x" * 100)
    real = tmp_path / "DSCF0001.JPG"
    real.write_bytes(b"x" * 100)
    assert not _is_image(junk)
    assert _is_image(real)


def test_skips_tiny(tmp_path: Path):
    tiny = tmp_path / "empty.jpg"
    tiny.write_bytes(b"")
    assert not _is_image(tiny)


def test_accepts_mp4(tmp_path: Path):
    clip = tmp_path / "VID-20181021-WA0001.mp4"
    clip.write_bytes(b"x" * 100)
    assert _is_image(clip)
