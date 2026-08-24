"""Kanal aus Pfad und Dateiname — an echten Pfaden aus dem Archiv."""
from __future__ import annotations

import pytest

from ingest.provenance import (
    CAMERA, DOCUMENT, DOWNLOAD, SCREENSHOT, WHATSAPP, WHATSAPP_SENT,
    WORTH_KEEPING, channel,
)


class TestRealPaths:
    """Pfade so, wie sie im Bestand vorkommen."""

    @pytest.mark.parametrize("path,expected", [
        ("/mnt/photo/Handys/Handy A/DCIM/Camera/IMG20231009175803.jpg", CAMERA),
        ("/mnt/photo/Handys/HandyPics/IMG20231009175803.jpg", CAMERA),
        ("/mnt/photo/Fotos/Abi 08/DSCF0044.JPG", CAMERA),
        ("/mnt/photo/Handys/Whatsapp/WhatsApp Images/IMG-20130713-WA0000.jpg", WHATSAPP),
        ("/mnt/photo/Handys/Handy A/Pictures/WhatsApp/IMG-20240729-WA0005.jpg", WHATSAPP),
        ("/mnt/photo/Handys/Whatsapp/WhatsApp Images/Sent/IMG-20130713-WA0000.jpg",
         WHATSAPP_SENT),
        ("/mnt/photo/Handys/Handy A/Pictures/Screenshots/Screenshot_2024.png",
         SCREENSHOT),
        ("/mnt/photo/Handys/Screenshots/irgendwas.png", SCREENSHOT),
        ("/mnt/photo/Handys/Handy A/Pictures/Office Lens/scan.jpg", DOCUMENT),
        ("/mnt/photo/Handys/Download/bild.jpg", DOWNLOAD),
        ("/mnt/photo/Handys/Handy A/Pictures/eBay/artikel.jpg", DOWNLOAD),
        ("/mnt/photo/Handys/Instagram/foo.jpg", DOWNLOAD),
    ])
    def test_channel(self, path, expected):
        assert channel(path) == expected


class TestFilenameBeatsFolder:
    def test_a_whatsapp_file_stays_whatsapp_in_an_album(self):
        """Wer eine WA-Datei ins Album einsortiert, aendert nicht ihre Herkunft."""
        assert channel("/mnt/photo/Fotos/Junggesellenabschied/IMG-20181020-WA0007.jpg") == WHATSAPP

    def test_a_screenshot_name_wins_anywhere(self):
        assert channel("/mnt/photo/Fotos/Abi 08/Screenshot_20200101.png") == SCREENSHOT

    def test_sent_folder_beats_the_whatsapp_name(self):
        """Empfangen und gesendet heissen gleich -- nur der Ordner unterscheidet."""
        p = "/mnt/photo/Handys/Whatsapp/WhatsApp Images/Sent/IMG-20130713-WA0000.jpg"
        assert channel(p) == WHATSAPP_SENT


class TestNotDeviceButOrigin:
    def test_two_different_phones_share_the_camera_channel(self):
        """Der Kanal trennt Herkunftsarten, nicht Geraete -- sonst faenden
        zwei Handys auf derselben Feier nie zusammen."""
        a = channel("/mnt/photo/Handys/Handy A/DCIM/Camera/IMG_1.jpg")
        b = channel("/mnt/photo/Handys/Handy B/DCIM/IMG_2.jpg")
        assert a == b == CAMERA


class TestEdges:
    def test_unknown_paths_default_to_camera(self):
        """Im Zweifel eine echte Aufnahme -- lieber pruefen als verwerfen."""
        assert channel("/mnt/photo/Urlaub/Japan 2019/DSC_0001.JPG") == CAMERA

    def test_empty_path(self):
        assert channel("") == CAMERA
        assert channel(None) == CAMERA

    def test_windows_separators(self):
        assert channel(r"D:\photo\Handys\Screenshots\x.png") == SCREENSHOT

    def test_partial_name_matches_do_not_count(self):
        """Ein Ordner „Downloadhelfer" ist kein Download-Ordner."""
        assert channel("/mnt/photo/Fotos/Downloadhelfer/x.jpg") == CAMERA


def test_only_real_photos_are_worth_keeping():
    assert CAMERA in WORTH_KEEPING
    assert WHATSAPP in WORTH_KEEPING
    assert SCREENSHOT not in WORTH_KEEPING
    assert DOCUMENT not in WORTH_KEEPING
    assert DOWNLOAD not in WORTH_KEEPING
