"""Fehlende clip-Vektoren nachziehen.

Ohne clip-Vektor ist ein Foto nicht auf der Karte -- und der Atlas ist die
Ansicht, um die es geht. Behoben werden konnte diese Luecke bisher gar
nicht: ein voller Neu-Ingest ueberspringt genau diese Fotos, weil sie schon
indiziert sind, und `reembed_all` baut Text-Vektoren.

Der Hinweis daneben lautete "Datei pruefen: meist beschaedigt oder nicht
lesbar" und zeigte damit in die falsche Richtung. Von 59 Faellen waren 57
in voller Auflaesung dekodierbar; sie fehlten, weil sie aufgenommen wurden,
bevor der Lader abgeschnittene Bilder tolerierte. Deshalb prueft dieser
Test vor allem die Trennung: was nachgezogen wird und was wirklich Muell
ist.
"""
from __future__ import annotations

import pytest

from tools import backfill_clip


class _Point:
    def __init__(self, pid, payload, vector=None):
        self.id = pid
        self.payload = payload
        self.vector = vector if vector is not None else {}


class FakeClient:
    def __init__(self, punkte):
        self.punkte = list(punkte)
        self.vectors = {}
        self.payloads = {}

    def scroll(self, collection_name, limit=512, offset=None, **kw):
        return list(self.punkte), None

    def update_vectors(self, collection_name, points, wait=True):
        for p in points:
            self.vectors[str(p.id)] = p.vector

    def set_payload(self, collection_name, payload, points, wait=True):
        for i in points:
            self.payloads.setdefault(str(i), {}).update(payload)


class TestCollect:
    def test_nimmt_nur_punkte_ohne_clip(self):
        c = FakeClient([
            _Point("a", {"file_path": "/a.jpg"}, {"clip": [0.1]}),
            _Point("b", {"file_path": "/b.jpg"}, {}),
            _Point("c", {"file_path": "/c.jpg"}, {"clip": None}),
        ])
        # Hinter Kennung und Pfad: Video? Beschreibung? -- beides hier nein.
        assert backfill_clip.collect(c, "photos", None) == [
            ("b", "/b.jpg", False, False, None), ("c", "/c.jpg", False, False, None)]

    def test_prefix_grenzt_ein(self):
        c = FakeClient([
            _Point("b", {"file_path": "/mnt/photo/Fotos/x.jpg"}, {}),
            _Point("c", {"file_path": "/mnt/photo/Handys/y.jpg"}, {}),
        ])
        got = backfill_clip.collect(c, "photos", "/mnt/photo/Fotos")
        assert got == [("b", "/mnt/photo/Fotos/x.jpg", False, False, None)]

    def test_punkt_ohne_pfad_wird_uebersprungen(self):
        c = FakeClient([_Point("b", {}, {})])
        assert backfill_clip.collect(c, "photos", None) == []


class TestKlassifiziere:
    def test_trennt_fehlend_winzig_und_brauchbar(self, tmp_path):
        gut = tmp_path / "gut.jpg"
        gut.write_bytes(b"x" * 5000)
        muell = tmp_path / "muell.png"
        muell.write_bytes(b"86e0aa15-9196-415d-b9ce-71ce639db644")  # 36 Byte, echter Fall

        aus = backfill_clip.klassifiziere([
            ("a", str(gut)), ("b", str(muell)), ("c", str(tmp_path / "gibtsnicht.jpg")),
        ])

        assert [x[1] for x in aus["geht"]] == [str(gut)]
        assert [x[1] for x in aus["winzig"]] == [str(muell)]
        assert aus["winzig"][0][2] == 36
        assert [x[1] for x in aus["fehlt"]] == [str(tmp_path / "gibtsnicht.jpg")]


class TestRun:
    """Der Lauf selbst -- mit einem Tagger, der nichts laedt."""

    def _patch(self, monkeypatch, *, embedding=None, tags=None, rgb=object()):
        class FakeTagger:
            def __init__(self, *a, **kw):
                pass

            def process_image(self, image):
                return {"embedding": embedding, "tags": tags or []}

        monkeypatch.setattr("ingest.scene_tagger.SceneTagger", FakeTagger)
        monkeypatch.setattr("ingest.pipeline._load_image",
                            lambda p: (object(), rgb, None) if rgb is not None
                            else (None, None, "unreadable"))

    def test_schreibt_vektor_und_etiketten(self, monkeypatch):
        self._patch(monkeypatch, embedding=[0.5] * 4, tags=["party"])
        c = FakeClient([])
        got = backfill_clip.run(c, "photos", [("a", "/a.jpg")], "/models", 0.2)
        assert got["done"] == 1 and got["skipped"] == 0
        assert c.vectors["a"] == {"clip": [0.5] * 4}
        assert c.payloads["a"] == {"scene_tags": ["party"]}

    def test_undekodierbares_wird_uebersprungen_nicht_geschrieben(self, monkeypatch):
        self._patch(monkeypatch, embedding=[0.5], rgb=None)
        c = FakeClient([])
        got = backfill_clip.run(c, "photos", [("a", "/a.jpg")], "/models", 0.2)
        assert got == {"done": 0, "skipped": 1, "seconds": got["seconds"]}
        assert c.vectors == {}

    def test_leerer_vektor_gilt_nicht_als_erfolg(self, monkeypatch):
        """Ein Punkt mit leerem clip-Vektor waere schlimmer als keiner.

        Er zaehlt nicht mehr als Luecke, taucht aber auch nicht auf der
        Karte auf -- ein Fehler, der sich selbst unsichtbar macht.
        """
        self._patch(monkeypatch, embedding=None)
        c = FakeClient([])
        got = backfill_clip.run(c, "photos", [("a", "/a.jpg")], "/models", 0.2)
        assert got["done"] == 0 and got["skipped"] == 1
        assert c.vectors == {}

    def test_ohne_etiketten_kein_payload_schreiben(self, monkeypatch):
        self._patch(monkeypatch, embedding=[0.5], tags=[])
        c = FakeClient([])
        backfill_clip.run(c, "photos", [("a", "/a.jpg")], "/models", 0.2)
        assert c.vectors["a"] == {"clip": [0.5]}
        assert c.payloads == {}

    def test_stapel_werden_zusammen_geschrieben(self, monkeypatch):
        self._patch(monkeypatch, embedding=[0.5])
        c = FakeClient([])
        aufgaben = [(str(i), f"/{i}.jpg") for i in range(5)]
        got = backfill_clip.run(c, "photos", aufgaben, "/models", 0.2, batch=2)
        assert got["done"] == 5 and len(c.vectors) == 5


class TestCollectKennzeichnet:
    def test_video_und_beschreibung_werden_mitgegeben(self):
        c = FakeClient([
            _Point("v", {"file_path": "/v.mp4", "kind": "video"}, {}),
            _Point("b", {"file_path": "/b.jpg", "caption_de": "Ein Hund."}, {}),
        ])
        assert backfill_clip.collect(c, "photos", None) == [
            ("v", "/v.mp4", True, False, None), ("b", "/b.jpg", False, True, None)]
