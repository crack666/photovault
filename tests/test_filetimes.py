"""Dateizeiten: rename bleibt auf dem Volume, mtime kommt zurück."""
from pathlib import Path

import pytest

from ingest.filetimes import rename_same_volume, restore, snapshot


def test_snapshot_and_restore_mtime(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"hi")
    snap = snapshot(p)
    p.write_bytes(b"changed")
    restore(p, snap)
    assert abs(p.stat().st_mtime - snap["mtime"]) < 1.0


def test_rename_same_volume(tmp_path):
    src = tmp_path / "old"
    src.mkdir()
    (src / "f.txt").write_text("x")
    dst = tmp_path / "new"
    rename_same_volume(src, dst)
    assert dst.is_dir()
    assert (dst / "f.txt").read_text() == "x"
    assert not src.exists()


def test_rename_refuses_existing_target(tmp_path):
    src = tmp_path / "a"
    dst = tmp_path / "b"
    src.mkdir()
    dst.mkdir()
    with pytest.raises(FileExistsError):
        rename_same_volume(src, dst)
