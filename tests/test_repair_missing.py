from pathlib import Path

from tools.repair_missing import counterpart_path


def test_rename_of_the_parent_finds_the_file(tmp_path):
    fotos = tmp_path / "Fotos"
    (fotos / "Album 2009").mkdir(parents=True)
    dest = fotos / "Album 2009" / "IMG_0037.JPG"
    dest.write_bytes(b"x")
    old = fotos / "album" / "IMG_0037.JPG"
    assert counterpart_path(str(old)) == dest


def test_file_still_there_is_not_a_counterpart(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    f = folder / "a.jpg"
    f.write_bytes(b"x")
    assert counterpart_path(str(f)) is None


def test_deleted_from_existing_folder_is_gone(tmp_path):
    folder = tmp_path / "Album"
    folder.mkdir()
    assert counterpart_path(str(folder / "missing.jpg")) is None


def test_two_siblings_with_the_same_name_stay_ambiguous(tmp_path):
    fotos = tmp_path / "Fotos"
    (fotos / "A").mkdir(parents=True)
    (fotos / "B").mkdir()
    (fotos / "A" / "x.jpg").write_bytes(b"1")
    (fotos / "B" / "x.jpg").write_bytes(b"2")
    assert counterpart_path(str(fotos / "old" / "x.jpg")) is None
