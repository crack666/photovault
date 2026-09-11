"""CLIP-Etiketten aus beschriebenen Fotos entfernen -- ohne die zu treffen,
die die Beschreibung stuetzt.

Der Fehler in beide Richtungen kostet: bleibt `skifahren` an einer Wunde,
ist sie unter Skifahren filterbar; geht `hund` von "Ein Hund liegt auf der
Wiese", verliert der Filter ein richtiges Foto. Gemessen am Bestand: von
3.392 `urlaub` stuetzte die Beschreibung 34. Die Regel muss das eine
nehmen und das andere lassen.
"""
from __future__ import annotations

from tools import prune_clip_tags as pct


class _P:
    def __init__(self, pid, payload):
        self.id, self.payload = pid, payload


class FakeQ:
    def __init__(self, punkte):
        self.punkte = punkte
        self.geschrieben = {}

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        return list(self.punkte), None

    def set_payload(self, collection_name, payload, points, wait):
        for p in points:
            self.geschrieben[p] = payload["scene_tags"]


class TestGestuetzt:
    def test_woertlich(self):
        assert pct.gestuetzt("hund", pct.falte("Ein Hund liegt auf der Wiese"))

    def test_im_kompositum(self):
        assert pct.gestuetzt("hund", pct.falte("Eine Hundeleine am Haken"))

    def test_umlaut_gefaltet(self):
        assert pct.gestuetzt("getraenke", pct.falte("Getränke auf dem Tisch"))

    def test_verbendung_faellt_weg(self):
        # `grillen` -> Stamm `grill` trifft "Grillabend"
        assert pct.gestuetzt("grillen", pct.falte("Ein Grillabend im Garten"))

    def test_kurzer_stamm_traegt_nicht(self):
        """`rad` in "Grad" waere ein Treffer, wenn der Stamm zu kurz sein duerfte."""
        assert not pct.gestuetzt("radfahren", pct.falte("Es hat 30 Grad"))

    def test_nicht_gestuetzt(self):
        assert not pct.gestuetzt("skifahren", pct.falte("Nahaufnahme eines verletzten Knies"))


class TestPlan:
    def test_wundfoto(self):
        q = FakeQ([_P("w", {
            "caption_de": "Nahaufnahme eines verletzten Kniegelenks mit Nähten.",
            "scene_tags": ["radfahren", "skifahren", "screenshot", "hund", "kinder",
                           "knie", "wunde", "naht"],
        })])
        aend, zus = pct.plan(q)
        assert aend == [("w", ["knie", "wunde", "naht"])]
        assert zus["weg"]["skifahren"] == 1 and zus["weg"]["hund"] == 1
        assert zus["leer_danach"] == 0

    def test_gestuetzter_clip_begriff_bleibt(self):
        q = FakeQ([_P("h", {"caption_de": "Ein Hund liegt auf der Wiese.",
                            "scene_tags": ["hund", "urlaub", "wiese"]})])
        aend, zus = pct.plan(q)
        assert aend == [("h", ["hund", "wiese"])]
        assert zus["behalten"]["hund"] == 1 and zus["weg"]["urlaub"] == 1

    def test_ohne_beschreibung_bleibt_alles(self):
        """Fuer Fotos ohne Beschreibung sind die CLIP-Etiketten der einzige
        Rueckfall. Sie werden nicht angefasst."""
        q = FakeQ([_P("o", {"caption_de": "", "scene_tags": ["party", "nacht"]})])
        aend, zus = pct.plan(q)
        assert aend == [] and zus["mit_caption"] == 0

    def test_unveraendertes_wird_nicht_geschrieben(self):
        q = FakeQ([_P("l", {"caption_de": "Kinder spielen.", "scene_tags": ["kinder", "spielen"]})])
        aend, _ = pct.plan(q)
        assert aend == []

    def test_leer_danach_wird_gezaehlt_aber_geschrieben(self):
        """Eine Beschreibung ohne eigene Etiketten und nur CLIP-Rauschen:
        leer ist ehrlicher als falsch, aber die Zahl soll dastehen."""
        q = FakeQ([_P("e", {"caption_de": "Ein Foto.", "scene_tags": ["urlaub", "paar"]})])
        aend, zus = pct.plan(q)
        assert aend == [("e", [])] and zus["leer_danach"] == 1


class TestApply:
    def test_schreibt_genau_die_aenderungen(self):
        q = FakeQ([])
        n = pct.apply(q, [("a", ["x"]), ("b", [])], batch=1)
        assert n == 2
        assert q.geschrieben == {"a": ["x"], "b": []}
