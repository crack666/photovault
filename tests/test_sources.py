"""Mehrere Quellen, Ausschlüsse und die Quellenliste."""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest.folder_parser import FolderParser
from ingest.scanner import NASScanner, load_sources


def _jpg(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    # >32 Bytes, sonst filtert der Scanner die Datei als Fragment weg.
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 64)
    return p


@pytest.fixture
def archive(tmp_path):
    for rel in [
        "Fotos/Abi 08/a.jpg",
        "Fotos/lose.jpg",
        "Urlaub/Japan 2019/b.jpg",
        "Urlaub/Scans/scan.jpg",
        "confidential/geheim.jpg",
        "Handys/screenshot.png",
    ]:
        _jpg(tmp_path / rel)
    return tmp_path


class TestSelection:
    def test_only_named_directories_are_scanned(self, archive):
        found = NASScanner([str(archive / "Fotos")]).scan()
        assert len(found) == 2
        assert all("/Fotos/" in f for f in found)

    def test_several_directories_combine(self, archive):
        found = NASScanner([str(archive / "Fotos"), str(archive / "Urlaub")]).scan()
        assert len(found) == 4
        assert not any("confidential" in f for f in found)

    def test_private_folder_stays_out_unless_named(self, archive):
        """Der eigentliche Punkt: nichts landet im Index, nur weil es unter
        demselben Share liegt."""
        found = NASScanner([str(archive / "Fotos")]).scan()
        assert not any("confidential" in f or "Handys" in f for f in found)

    def test_exclude_carves_out_a_subfolder(self, archive):
        found = NASScanner([str(archive / "Urlaub")],
                           exclude=[str(archive / "Urlaub" / "Scans")]).scan()
        assert len(found) == 1
        assert "Japan 2019" in found[0]

    def test_overlapping_roots_do_not_duplicate(self, archive):
        found = NASScanner([str(archive), str(archive / "Fotos")]).scan()
        assert len(found) == len(set(found))

    def test_a_single_string_still_works(self, archive):
        """Bestehende Aufrufer geben einen Pfad, keine Liste."""
        assert len(NASScanner(str(archive / "Fotos")).scan()) == 2

    def test_missing_source_is_reported(self, archive):
        with pytest.raises(FileNotFoundError, match="gibtsnicht"):
            NASScanner([str(archive / "Fotos"), str(archive / "gibtsnicht")]).scan()


class TestSourcesFile:
    def test_reads_includes_and_excludes(self, tmp_path):
        f = tmp_path / "sources.txt"
        f.write_text(
            "# Kommentar\n"
            "\n"
            "/mnt/photo/Fotos      # nachgestellter Kommentar\n"
            "/mnt/photo/Urlaub\n"
            "-/mnt/photo/Urlaub/Scans\n",
            encoding="utf-8",
        )
        inc, exc = load_sources(str(f))
        assert inc == ["/mnt/photo/Fotos", "/mnt/photo/Urlaub"]
        assert exc == ["/mnt/photo/Urlaub/Scans"]

    def test_a_file_without_entries_is_an_error(self, tmp_path):
        """Sonst laeuft ein Ingest ueber nichts und meldet klaglos Erfolg."""
        f = tmp_path / "leer.txt"
        f.write_text("# alles auskommentiert\n#/mnt/photo/Fotos\n", encoding="utf-8")
        with pytest.raises(ValueError, match="kein einziges Verzeichnis"):
            load_sources(str(f))


class TestAlbumWithSeveralRoots:
    def test_each_photo_uses_its_own_root(self):
        p = FolderParser(["/mnt/photo/Fotos", "/mnt/photo/Urlaub"])
        assert p.parse("/mnt/photo/Fotos/lose.jpg")["folder_name"] == "Fotos"
        assert p.parse("/mnt/photo/Urlaub/lose.jpg")["folder_name"] == "Urlaub"

    def test_deepest_matching_root_wins(self):
        p = FolderParser(["/mnt/photo", "/mnt/photo/Fotos"])
        # Unter beiden Wurzeln -- die tiefere beschreibt das Archiv genauer.
        assert p.parse("/mnt/photo/Fotos/lose.jpg")["folder_name"] == "Fotos"

    def test_album_still_climbs_over_camera_dirs(self):
        p = FolderParser(["/mnt/photo/Fotos", "/mnt/photo/Urlaub"])
        r = p.parse("/mnt/photo/Urlaub/Japan 2019/DCIM/x.jpg")
        assert r["folder_name"] == "Japan 2019"
        assert r["subfolder"] == "DCIM"

    def test_a_single_root_behaves_as_before(self):
        p = FolderParser("/mnt/photo/Fotos")
        assert p.parse("/mnt/photo/Fotos/Abi 08/x.jpg")["folder_name"] == "Abi 08"


class TestHiddenDirectories:
    """Punkt-Verzeichnisse sind Caches, keine Alben.

    `Pictures/.thumbnails` allein enthaelt im Handy-Ordner 5119 Miniaturbilder.
    Der Dateiname-Filter griff dort nicht, weil nur das Verzeichnis versteckt
    ist, nicht die Datei.
    """

    def test_thumbnail_cache_is_skipped(self, tmp_path):
        _jpg(tmp_path / "DCIM" / "echt.jpg")
        _jpg(tmp_path / "Pictures" / ".thumbnails" / "1234567890.jpg")
        _jpg(tmp_path / "Pictures" / ".gs_fs0" / "x.jpg")
        found = NASScanner([str(tmp_path)]).scan()
        assert len(found) == 1
        assert found[0].endswith("echt.jpg")

    def test_a_hidden_source_root_still_works(self, tmp_path):
        """Wer eine versteckte Wurzel ausdruecklich angibt, meint sie auch."""
        root = tmp_path / ".versteckt"
        _jpg(root / "a.jpg")
        assert len(NASScanner([str(root)]).scan()) == 1
