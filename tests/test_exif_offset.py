"""Der Versatz-Lauf schreibt nur, was er angekuendigt hat — sonst bricht er ab."""
from datetime import datetime, timedelta

import pytest

from tools.exif_offset import Abbruch, parse_offset, run, sammeln


def test_offset_mit_einheiten():
    assert parse_offset("1400d12h") == timedelta(days=1400, hours=12)
    assert parse_offset("90m") == timedelta(minutes=90)
    assert parse_offset("-3h") == timedelta(hours=-3)


def test_offset_als_blosse_tageszahl():
    assert parse_offset("1400.5") == timedelta(days=1400, hours=12)


def test_offset_null_ist_kein_versatz():
    with pytest.raises(ValueError):
        parse_offset("0d")


def test_offset_unlesbar_faellt_auf():
    """Ein halb erkannter Ausdruck ist gefaehrlicher als gar keiner."""
    with pytest.raises(ValueError):
        parse_offset("1400 Tage")
    with pytest.raises(ValueError):
        parse_offset("1400x")


class FakeClient:
    """Qdrant-Ersatz: eine Seite Punkte, set_payload wird mitgeschrieben."""

    def __init__(self, punkte):
        self._punkte = punkte
        self.gesetzt = []

    def scroll(self, **kw):
        return self._punkte, None

    def set_payload(self, **kw):
        self.gesetzt.append(kw)


class Punkt:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


def _punkte(n=3, model="KODAK V530 ZOOM DIGITAL CAMERA", suffix=".JPG"):
    return [Punkt(i, {
        "file_path": "/mnt/photo/Album/000_006%d%s" % (i, suffix),
        "taken_at": "2005-01-01T12:0%d:07Z" % i,
        "exif": {"Model": model},
    }) for i in range(n)]


def test_kamera_filtert_die_fremde_haelfte():
    punkte = _punkte(2) + [Punkt(9, {
        "file_path": "/mnt/photo/Album/DSC00214.JPG",
        "taken_at": "2008-11-01T21:58:27Z",
        "exif": {"Model": "K750i"},
    })]
    treffer = sammeln(FakeClient(punkte), "Album", "KODAK")
    assert [t[0] for t in treffer] == [0, 1]


def test_abweichende_anzahl_bricht_ab():
    """Der Bestand hat sich seit der Messung geaendert — nicht blind schreiben."""
    with pytest.raises(Abbruch, match="Erwartet waren"):
        run(FakeClient(_punkte(3)), album="Album", offset=timedelta(days=1),
            camera="KODAK", expect=12)


def test_apply_ohne_expect_ist_verboten():
    with pytest.raises(Abbruch, match="--expect"):
        run(FakeClient(_punkte(3)), album="Album", offset=timedelta(days=1),
            camera="KODAK", apply=True)


def test_leere_gruppe_bricht_ab():
    with pytest.raises(Abbruch, match="Keine Fotos"):
        run(FakeClient([]), album="Gibt es nicht", offset=timedelta(days=1))


def test_nicht_beschreibbares_format_reisst_die_gruppe_nicht_auf():
    """Ein PNG mitten in der Serie: lieber gar nicht als die Haelfte."""
    with pytest.raises(Abbruch, match="verlustfrei"):
        run(FakeClient(_punkte(2) + _punkte(1, suffix=".png")),
            album="Album", offset=timedelta(days=1), camera="KODAK")


def test_trockenlauf_schreibt_nichts_in_den_index():
    client = FakeClient(_punkte(3))
    zaehler = run(client, album="Album", offset=timedelta(days=1400, hours=12),
                  camera="KODAK")
    assert client.gesetzt == []
    assert sum(zaehler.values()) == 3


def test_reihenfolge_bleibt_die_der_serie():
    punkte = list(reversed(_punkte(3)))
    treffer = sammeln(FakeClient(punkte), "Album", "KODAK")
    zeiten = [t[2] for t in treffer]
    assert zeiten == sorted(zeiten)
    assert zeiten[0] == datetime(2005, 1, 1, 12, 0, 7)
