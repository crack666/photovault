"""Der Nachtlauf (#65): Karte immer zurueckgeben, sauber enden, keine Namen im Log."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from ingest.gpu_tor import GpuBusy
from tools import caption_etappen as ce


class FakeTor:
    def __init__(self, frei=(True,)):
        self.frei = list(frei)
        self.rufe: list[str] = []

    def reservieren(self, grund, minuten):
        self.rufe.append("reservieren")
        ok = self.frei.pop(0) if self.frei else True
        return ok, "frei" if ok else "belegt: render"

    def freigeben(self):
        self.rufe.append("freigeben")


@pytest.fixture
def lauf(monkeypatch):
    """`caption_pass.run` als Doppel: nimmt vom Rest, was in die Etappe passt."""
    box = SimpleNamespace(rest=0, aufrufe=[], fehler=None, ohne_fortschritt=False)

    def run(client, limit, **kw):
        box.aufrufe.append((limit, kw))
        if box.fehler:
            raise box.fehler
        n = 0 if box.ohne_fortschritt else min(limit, box.rest)
        box.rest -= n
        return {"captioned": n, "unreadable": 0, "failed": 0}

    monkeypatch.setattr(ce.caption_pass, "run", run)
    monkeypatch.setattr(ce, "offen", lambda client, kind: box.rest)

    def starten(tor):
        return ce.laufen(None, tor, kind="video", etappe=580, minuten=110, luft=90,
                         schlafen=lambda s: box.aufrufe.append(("luft", s)), waermen=lambda: None)

    box.starten = starten
    return box


def test_alle_etappen_bis_nichts_offen_ist(lauf):
    lauf.rest = 1305
    tor = FakeTor()
    assert lauf.starten(tor) == ce.FERTIG
    assert tor.rufe == ["reservieren", "freigeben"] * 3
    etappen = [a for a in lauf.aufrufe if a[0] != "luft"]
    assert [a[0] for a in etappen] == [580] * 3
    # Nie ins Original, nur was noch fehlt.
    assert all(a[1]["write_exif"] is False and a[1]["missing_only"] for a in etappen)
    assert sum(1 for a in lauf.aufrufe if a[0] == "luft") == 2


def test_tor_zu_rechnet_nichts_und_gibt_nichts_frei(lauf):
    lauf.rest = 10
    tor = FakeTor(frei=[False])
    assert lauf.starten(tor) == ce.TOR_ZU
    assert lauf.aufrufe == [] and tor.rufe == ["reservieren"]


def test_vorrang_zwischen_zwei_etappen_beendet_den_lauf(lauf):
    lauf.rest = 1000
    tor = FakeTor(frei=[True, False])
    assert lauf.starten(tor) == ce.TOR_ZU
    assert lauf.rest == 420 and tor.rufe == ["reservieren", "freigeben", "reservieren"]


def test_tor_geht_mittendrin_zu_gibt_frei(lauf):
    lauf.rest, lauf.fehler = 10, GpuBusy("GPU belegt: spiel")
    tor = FakeTor()
    assert lauf.starten(tor) == ce.TOR_ZU
    assert tor.rufe == ["reservieren", "freigeben"]


def test_fehler_gibt_frei(lauf):
    lauf.rest, lauf.fehler = 10, RuntimeError("/mnt/photo/geheim.mp4")
    tor = FakeTor()
    with pytest.raises(RuntimeError):
        lauf.starten(tor)
    assert tor.rufe == ["reservieren", "freigeben"]


def test_kein_fortschritt_ist_keine_endlosschleife(lauf):
    lauf.rest, lauf.ohne_fortschritt = 7, True
    assert lauf.starten(FakeTor()) == ce.REST


def test_protokoll_nennt_keine_datei():
    f = ce.OhneNamen()

    def rec(name, msg, *args):
        return logging.LogRecord(name, logging.INFO, "x", 1, msg, args, None)

    assert f.filter(rec("ingest.caption_pass", "%d/%d  %.2f s/Foto", 1, 2, 3.0))
    assert f.filter(rec("etappen", "Etappe %d", 1))
    assert not f.filter(rec("ingest.caption_pass", "Unreadable, skipping: %s (%s)", "/p/x.mp4", "e"))
    assert not f.filter(rec("ingest.captioner", "Captioning failed for %s: %s", "/p/x.mp4", "e"))
    assert not f.filter(rec("httpx", "HTTP Request: POST ..."))


def test_tor_kennt_die_exitcodes():
    def mit(code):
        return ce.Tor("x.ps1", "k", run=lambda *a, **kw: SimpleNamespace(
            returncode=code, stdout=b"belegt: spiel\r\n"))

    assert mit(0).reservieren("g", 5)[0] is True
    assert mit(1).reservieren("g", 5) == (False, "belegt: spiel")
    with pytest.raises(RuntimeError):  # Sensorfehler ist nicht "frei"
        mit(2).reservieren("g", 5)


def test_was_vor_der_ablehnung_fertig_war_wird_geschrieben(monkeypatch):
    """Das Tor geht mitten in einer Runde zu: die fertigen Saetze bleiben."""
    from ingest import caption_pass as cp

    fotos = [cp.Photo(i, {"file_path": f"/v/{i}.mp4", "kind": "video"}) for i in range(3)]
    geschrieben = []
    monkeypatch.setattr(cp, "select_photos", lambda *a, **k: fotos)
    monkeypatch.setattr(cp, "_read_images", lambda chunk, n: [setattr(p, "image_b64", "x") for p in chunk])
    monkeypatch.setattr(cp, "_write_captions", lambda c, col, ps: geschrieben.extend(p.point_id for p in ps))
    monkeypatch.setattr(cp, "rebuild_text_vectors", lambda *a, **k: {"updated": 0})
    monkeypatch.setattr(cp, "TextEmbedder", lambda: None)

    class C:
        def __init__(self, **kw): ...

        def caption_structured(self, path, *a, **kw):
            if path.endswith("2.mp4"):
                raise GpuBusy("GPU belegt")
            return {"caption_de": "Satz.", "scene_tags": []}

    monkeypatch.setattr(cp, "Captioner", C)
    with pytest.raises(GpuBusy):
        cp.run(None, workers=1, track=False, write_exif=False, on_busy=ce.aufgeben)
    assert geschrieben == [0, 1]
    # Nach Ablauf der Frist faengt keine Runde mehr an.
    assert cp.run(None, track=False, write_exif=False, deadline=0.0)["captioned"] == 0
