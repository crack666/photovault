"""Ortserkennung und Album-statt-Kameraordner."""
from pathlib import Path

from ingest.folder_parser import FolderParser, album_dir
from ingest.locations import detect


class TestAlbumDir:
    def test_camera_folder_is_skipped(self):
        """Abi 08/100MSDCF/foto.jpg gehört zum Album 'Abi 08'."""
        assert album_dir(Path("/p/Abi 08/100MSDCF/foto.jpg")).name == "Abi 08"

    def test_dcim_is_skipped(self):
        assert album_dir(Path("/p/Griechenland 2015/DCIM/x.jpg")).name == "Griechenland 2015"

    def test_generic_folder_is_skipped(self):
        assert album_dir(Path("/p/Hochzeit/Bilder/x.jpg")).name == "Hochzeit"

    def test_real_album_is_kept(self):
        assert album_dir(Path("/p/Abi 08/Abiball/x.jpg")).name == "Abiball"

    def test_does_not_climb_forever(self):
        """Mehrere generische Ebenen: irgendwo muss Schluss sein."""
        got = album_dir(Path("/p/DCIM/Fotos/Bilder/x.jpg"), max_up=2)
        assert got.name in ("Fotos", "DCIM", "p")


class TestFolderParserAlbum:
    def setup_method(self):
        self.parser = FolderParser()

    def test_album_wins_over_camera_folder(self):
        r = self.parser.parse("/p/Abi 08/100MSDCF/k-100_0610.JPG")
        assert r["folder_name"] == "Abi 08"
        assert r["subfolder"] == "100MSDCF"

    def test_year_hint_comes_back_with_the_album(self):
        """Der Kameraordner kostete das Album seinen Jahreshinweis."""
        r = self.parser.parse("/p/Griechenland 2015/DCIM/IMG_0042.jpg")
        assert r["folder_name"] == "Griechenland 2015"
        assert r["date_hint"] == "2015"

    def test_normal_album_has_no_subfolder(self):
        r = self.parser.parse("/p/Abiball/IMG_0042.jpg")
        assert r["folder_name"] == "Abiball"
        assert r["subfolder"] is None


class TestLocations:
    def test_country_in_folder(self):
        assert detect("Griechenland 2015")[0] == "Griechenland"

    def test_german_town(self):
        """Der Fall aus dem Archiv: 'groemitz' wurde nie erkannt."""
        assert detect("groemitz")[0] == "Grömitz"

    def test_umlaut_spelling_matches(self):
        assert detect("Grömitz Urlaub")[0] == "Grömitz"

    def test_city(self):
        assert detect("Berlin Trip 2016")[0] == "Berlin"

    def test_whole_words_only(self):
        """'Kastenlauf' darf nicht auf 'Kasten' o.ä. anspringen."""
        assert detect("Kastenlauf 2008") is None

    def test_birthday_album_is_not_a_place(self):
        assert detect("20. Geburtstag") is None
        assert detect("18. Geburtstag (2006)") is None

    def test_ambiguous_city_is_excluded(self):
        """'Essen' ist häufiger die Mahlzeit als die Stadt."""
        assert detect("Essen 2010") is None

    def test_first_hit_wins_over_later_text(self):
        assert detect(None, "Urlaub Italien")[0] == "Italien"

    def test_nothing_found(self):
        assert detect("Papertec Bowling") is None
        assert detect(None) is None
        assert detect("") is None


class TestAlbumRoot:
    """Über die Scan-Wurzel hinaus gibt es kein Album.

    Lose Dateien in „…/photo/Fotos“ bekamen sonst das Share-Verzeichnis
    „photo“ als Album, weil „Fotos“ als generisch gilt.
    """

    def test_does_not_climb_above_root(self):
        root = Path("/mnt/photo/Fotos")
        got = album_dir(Path("/mnt/photo/Fotos/lose.jpg"), root=root)
        assert got.name == "Fotos"

    def test_climbs_within_root(self):
        root = Path("/mnt/photo/Fotos")
        got = album_dir(Path("/mnt/photo/Fotos/Abi 08/100MSDCF/x.jpg"), root=root)
        assert got.name == "Abi 08"

    def test_parser_keeps_root_folder(self):
        p = FolderParser(root="/mnt/photo/Fotos")
        r = p.parse("/mnt/photo/Fotos/lose.jpg")
        assert r["folder_name"] == "Fotos"
        assert r["subfolder"] is None

    def test_parser_still_skips_camera_dir(self):
        p = FolderParser(root="/mnt/photo/Fotos")
        r = p.parse("/mnt/photo/Fotos/Handyfotos/100MSDCF/x.jpg")
        assert r["folder_name"] == "Handyfotos"
        assert r["subfolder"] == "100MSDCF"

    def test_without_root_behaves_as_before(self):
        assert album_dir(Path("/p/Abi 08/DCIM/x.jpg")).name == "Abi 08"


class TestSidecarRobustness:
    """Ein toter Netzpfad darf keinen Datensatz kosten.

    `Path.exists()` liefert bei einem abgestandenen SMB-Mount nicht `False`,
    sondern wirft `OSError: Host is down` — das riss frueher die ganze
    Verarbeitung mit.
    """

    def test_stale_mount_does_not_raise(self, monkeypatch):
        import builtins

        from ingest.folder_parser import FolderParser

        def dead(*a, **kw):
            raise OSError(112, "Host is down")

        monkeypatch.setattr(builtins, "open", dead)
        p = FolderParser(root="/mnt/photo/Fotos")
        r = p.parse("/mnt/photo/Fotos/Handyfotos/100MSDCF/x.jpg")
        assert r["folder_name"] == "Handyfotos"
        assert r["subfolder"] == "100MSDCF"

    def test_missing_sidecar_is_silent(self, tmp_path):
        from ingest.folder_parser import FolderParser

        (tmp_path / "Abi 08").mkdir()
        f = tmp_path / "Abi 08" / "x.jpg"
        f.touch()
        r = FolderParser(root=str(tmp_path)).parse(str(f))
        assert r["folder_name"] == "Abi 08"

    def test_broken_sidecar_is_survivable(self, tmp_path):
        from ingest.folder_parser import FolderParser

        d = tmp_path / "Abi 08"
        d.mkdir()
        (d / "_photovault.json").write_text("{kaputt", encoding="utf-8")
        f = d / "x.jpg"
        f.touch()
        r = FolderParser(root=str(tmp_path)).parse(str(f))
        assert r["folder_name"] == "Abi 08"


class TestAlbumNeverEscapesRoot:
    """Das Album muss echt unterhalb der Scan-Wurzel liegen.

    Mit `--source /mnt/photo` (statt `/mnt/photo/Fotos`) bekamen lose Dateien
    in „Fotos" das Album „photo" — der Freigabename. Die Pruefung hatte ein
    `and parent != root` zu viel, das sie genau im entscheidenden Fall aushebelte.
    """

    def test_generic_folder_directly_under_root_stays_the_album(self):
        root = Path("/mnt/photo")
        assert album_dir(Path("/mnt/photo/Fotos/lose.jpg"), root=root).name == "Fotos"

    def test_same_result_regardless_of_root_depth(self):
        deep = album_dir(Path("/mnt/photo/Fotos/lose.jpg"), root=Path("/mnt/photo/Fotos"))
        shallow = album_dir(Path("/mnt/photo/Fotos/lose.jpg"), root=Path("/mnt/photo"))
        assert deep.name == shallow.name == "Fotos"

    def test_camera_dir_still_climbs_within_root(self):
        root = Path("/mnt/photo")
        got = album_dir(Path("/mnt/photo/Zweithandy/DCIM/Camera/x.jpg"), root=root)
        assert got.name in ("Zweithandy", "DCIM")

    def test_file_directly_at_root_has_no_better_name(self):
        # 12 Stueck im echten Archiv — „photo" ist hier korrekt, es gibt nichts anderes.
        root = Path("/mnt/photo")
        assert album_dir(Path("/mnt/photo/lose.jpg"), root=root).name == "photo"
