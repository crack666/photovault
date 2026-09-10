"""Fotoarchiv erreichbar oder nicht — und was daraus folgt."""
from pathlib import Path

from api.archive import why_unavailable


class TestWhyUnavailable:
    def test_leerer_unmounted_ordner_ist_weg(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.archive.photo_root", lambda: str(tmp_path))
        monkeypatch.setattr("api.archive.os.path.ismount", lambda p: False)
        assert "nicht gemountet" in (why_unavailable() or "")

    def test_gefuellter_lokaler_ordner_ist_da(self, tmp_path, monkeypatch):
        (tmp_path / "Fotos").mkdir()
        monkeypatch.setattr("api.archive.photo_root", lambda: str(tmp_path))
        monkeypatch.setattr("api.archive.os.path.ismount", lambda p: False)
        assert why_unavailable() is None

    def test_mount_ist_da(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.archive.photo_root", lambda: str(tmp_path))
        monkeypatch.setattr("api.archive.os.path.ismount", lambda p: True)
        assert why_unavailable() is None

    def test_fremde_datei_ist_kein_archivproblem(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.archive.photo_root", lambda: "/mnt/photo")
        assert why_unavailable(str(tmp_path / "x.jpg")) is None

    def test_datei_unter_der_wurzel_erbt_den_befund(self, tmp_path, monkeypatch):
        monkeypatch.setattr("api.archive.photo_root", lambda: str(tmp_path))
        monkeypatch.setattr("api.archive.os.path.ismount", lambda p: False)
        assert why_unavailable(str(tmp_path / "Fotos" / "a.jpg"))
