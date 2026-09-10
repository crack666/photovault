"""Vorschauen auch aus abgebrochenen JPEGs, sonst 500er-Kacheln in der UI."""
from pathlib import Path

from PIL import Image

from api.thumbs import get_thumb, jpeg_truncation_hint, make_thumb


def _jpeg(path: Path, size=(80, 60), color=(20, 80, 140)):
    Image.new("RGB", size, color).save(path, format="JPEG", quality=90)
    return path


def test_a_normal_jpeg_renders(tmp_path):
    p = _jpeg(tmp_path / "ok.jpg")
    data = get_thumb(str(p), size=160)
    assert data[:2] == b"\xff\xd8"


def test_a_truncated_jpeg_still_renders(tmp_path):
    p = _jpeg(tmp_path / "cut.jpg", size=(400, 300))
    raw = p.read_bytes()
    # Das Ende wegschneiden, wie bei den Papertec-Dateien im Log.
    p.write_bytes(raw[: len(raw) // 2])
    data, warn = make_thumb(str(p), size=160)
    assert data[:2] == b"\xff\xd8"
    assert len(data) > 100
    assert warn == "truncated"
    assert jpeg_truncation_hint(str(p)) == "truncated"


def test_a_complete_jpeg_has_no_warning(tmp_path):
    p = _jpeg(tmp_path / "full.jpg", size=(120, 80))
    assert jpeg_truncation_hint(str(p)) is None
    _data, warn = make_thumb(str(p), size=160)
    assert warn is None


def test_host_down_is_not_cached_as_a_thumb(tmp_path, monkeypatch):
    """Ein NAS-Aussetzer darf keine dunkle Kachel hinterlassen."""
    import errno

    import pytest
    from PIL import Image as PilImage

    from api.archive import ArchiveUnavailable

    p = _jpeg(tmp_path / "x.jpg")
    monkeypatch.setattr("api.thumbs.CACHE_DIR", tmp_path / "c")
    real_open = PilImage.open

    def boom(src, *a, **k):
        if Path(src) == p:
            raise OSError(errno.EHOSTDOWN, "host down")
        return real_open(src, *a, **k)

    monkeypatch.setattr(PilImage, "open", boom)
    with pytest.raises(ArchiveUnavailable):
        get_thumb(str(p), size=160)
    assert list((tmp_path / "c").rglob("*.jpg")) == []
