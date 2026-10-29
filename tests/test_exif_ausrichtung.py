"""Erkennung und Zuschnitt muessen dasselbe Bild sehen.

`api/thumbs.py` dreht ein Foto nach seiner EXIF-Ausrichtung, bevor es den
Gesichtskasten anlegt. Der Ingest tat das nicht: der Kasten entstand auf dem
rohen Bild, der Zuschnitt lag auf dem gedrehten. Bei Ausrichtung 3 (180 Grad)
landete er spiegelverkehrt -- die Benennen-Seite zeigte einen Zaun, wo ein
Kind stand, und der Detektor fand auf demselben Foto ein Gesicht statt zwei
(photovault#37, gemessen 27.10.2026: ~3,2 % der Dateien mit Gesichtern tragen
eine Ausrichtung != 1).

Gemessen wird hier an Pixeln, nicht an Aufrufen: ein Bild mit einer
eindeutigen Ecke, einmal durch beide Wege geschickt. Ein Test, der nur
nachsieht ob `exif_transpose` aufgerufen wurde, haette die Richtung nicht
bemerkt.
"""
from __future__ import annotations



import pytest
from PIL import Image

from ingest.face_embedder import _load_bgr
from ingest.pipeline import _load_image


def _foto_mit_ecke(pfad, ausrichtung: int):
    """Schwarzes Bild, oben links ein weisser Fleck, plus EXIF-Ausrichtung.

    Nach dem Drehen muss der Fleck woanders liegen -- daran laesst sich
    pruefen, ob gedreht wurde, ohne eine Bibliothek zu befragen.
    """
    im = Image.new("RGB", (40, 20), (0, 0, 0))
    for x in range(8):
        for y in range(4):
            im.putpixel((x, y), (255, 255, 255))
    exif = im.getexif()
    exif[274] = ausrichtung
    im.save(pfad, exif=exif)


def _fleck_oben_links(bild) -> bool:
    return bild.getpixel((2, 2))[0] > 200


class TestAusrichtungImIngest:
    def test_pipeline_dreht_die_fassung_fuer_die_modelle(self, tmp_path):
        """Ausrichtung 3 heisst 180 Grad: der Fleck gehoert nach unten rechts."""
        p = tmp_path / "gedreht.jpg"
        _foto_mit_ecke(p, 3)
        raw, rgb, warn = _load_image(str(p))
        assert warn is None
        assert _fleck_oben_links(raw), "das Original bleibt unangetastet"
        assert not _fleck_oben_links(rgb), (
            "die Fassung fuer die Modelle muss gedreht sein -- sonst entsteht "
            "der Gesichtskasten in Koordinaten, die der Zuschnitt nicht teilt"
        )

    def test_ohne_ausrichtung_bleibt_alles_wie_es_war(self, tmp_path):
        p = tmp_path / "normal.jpg"
        _foto_mit_ecke(p, 1)
        raw, rgb, _ = _load_image(str(p))
        assert _fleck_oben_links(raw) and _fleck_oben_links(rgb)

    def test_face_embedder_laedt_genauso(self, tmp_path):
        """Der zweite Ladeweg (face_pass ueber Fotos) darf nicht abweichen."""
        p = tmp_path / "gedreht.jpg"
        _foto_mit_ecke(p, 3)
        bgr = _load_bgr(str(p))
        assert bgr is not None
        # BGR-ndarray: [y][x] -> der Fleck darf oben links nicht mehr stehen.
        assert int(bgr[2][2][2]) < 100, "auch hier muss gedreht werden"

    @pytest.mark.parametrize("ausrichtung", [3, 6, 8])
    def test_beide_wege_sehen_dasselbe(self, tmp_path, ausrichtung):
        """Pipeline und face_embedder muessen Pixel fuer Pixel uebereinstimmen.

        Das ist die eigentliche Bedingung: nicht "beide drehen irgendwie",
        sondern beide kommen beim selben Bild heraus. Sonst sitzt ein Kasten
        aus dem einen Weg im anderen falsch.
        """
        p = tmp_path / f"a{ausrichtung}.jpg"
        _foto_mit_ecke(p, ausrichtung)
        _, rgb, _ = _load_image(str(p))
        bgr = _load_bgr(str(p))
        assert bgr.shape[:2] == (rgb.size[1], rgb.size[0])
        for x, y in ((2, 2), (rgb.size[0] - 3, rgb.size[1] - 3)):
            assert rgb.getpixel((x, y))[0] == int(bgr[y][x][2])
