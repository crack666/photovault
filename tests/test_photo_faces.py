"""Die Gesichter eines einzelnen Fotos -- Reihenfolge, Zustand, Auflösung.

Der Streifen unter dem geöffneten Foto ist die Gegenrichtung zu „Gesichter
ohne Namen“: dort fragt der Stapel, hier antwortet das Foto. Damit das
funktioniert, muss dreierlei stimmen -- die Reihenfolge passt zu dem, was man
im Bild sieht; „übersprungen“ und „ignoriert“ bleiben unterscheidbar und
überschreibbar; und eine Punkt-ID wird nicht mit einem Datei-Hash verwechselt.
"""
import uuid

import pytest
from fastapi import HTTPException

from api.routes.faces import face_state, resolve_photo_id, sort_key, suggest_names


class _P:
    def __init__(self, pid, payload=None, vector=None):
        self.id = pid
        self.payload = payload or {}
        self.vector = vector


class TestFaceState:
    def test_ohne_person_ist_offen(self):
        assert face_state(None) == "open"
        assert face_state("") == "open"

    def test_name_ist_benannt(self):
        assert face_state("zelda-erfunden") == "named"

    def test_uebersprungen_und_ignoriert_bleiben_getrennt(self):
        # „Später“ und „nie“ sind nicht dasselbe: das eine ist eine offene
        # Aufgabe, das andere eine Entscheidung.
        assert face_state("_skipped") == "skipped"
        assert face_state("_ignored") == "ignored"


class TestSortKey:
    def test_leserichtung_zeilenweise(self):
        # Zwei Köpfe oben, einer unten -- oben zuerst, darin links zuerst.
        oben_rechts = [600, 100, 700, 200]
        oben_links = [100, 110, 200, 210]
        unten = [300, 800, 400, 900]
        boxen = [oben_rechts, unten, oben_links]
        assert sorted(boxen, key=sort_key) == [oben_links, oben_rechts, unten]

    def test_leicht_tieferer_kopf_bleibt_in_der_zeile(self):
        # 40 px Höhenunterschied ist kein neuer Bildabschnitt.
        links = [100, 100, 200, 200]
        rechts_tiefer = [600, 140, 700, 240]
        assert sorted([rechts_tiefer, links], key=sort_key) == [links, rechts_tiefer]

    def test_kaputte_box_landet_hinten(self):
        gut = [10, 10, 20, 20]
        assert sorted([[], gut], key=sort_key) == [gut, []]


class TestSuggestNames:
    class _Q:
        def __init__(self, hits):
            self.hits = hits
            self.calls = []

        def query_points(self, **kw):
            self.calls.append(kw)
            return type("R", (), {"points": self.hits})()

    def _hit(self, score, pid, name):
        return type("H", (), {"id": f"f-{pid}-{score}", "score": score,
                              "payload": {"person_id": pid, "person_name": name}})()

    def test_bester_treffer_je_person(self):
        q = self._Q([self._hit(0.51, "zelda", "Zelda"), self._hit(0.72, "zelda", "Zelda"),
                     self._hit(0.60, "bodo", "Bodo")])
        out = suggest_names(q, [0.1] * 512)
        assert [(r["id"], r["score"]) for r in out] == [("zelda", 0.72), ("bodo", 0.6)]

    def test_ablagen_sind_keine_vorschlaege(self):
        # „Ist das Übersprungen?“ ist keine sinnvolle Rückfrage.
        q = self._Q([self._hit(0.9, "_skipped", "Übersprungen"),
                     self._hit(0.5, "zelda", "Zelda")])
        assert [r["id"] for r in suggest_names(q, [0.1] * 512)] == ["zelda"]

    def test_ohne_vektor_wird_nicht_gefragt(self):
        q = self._Q([])
        assert suggest_names(q, None) == []
        assert q.calls == []

    def test_fehler_wird_nicht_geschluckt(self):
        # Ein kaputter Index darf nicht aussehen wie „niemand ähnelt dieser
        # Person“ -- sonst tippt man Namen, die vorgeschlagen worden wären.
        class _Broken:
            def query_points(self, **kw):
                raise RuntimeError("Index weg")

        with pytest.raises(RuntimeError):
            suggest_names(_Broken(), [0.1] * 512)


class TestResolvePhotoId:
    class _Q:
        def __init__(self, points):
            self.points = points
            self.asked = []

        def retrieve(self, collection_name, ids, **kw):
            self.asked.append(ids)
            return self.points

    def test_hash_wird_nicht_nachgeschlagen(self):
        # Ein sha256 ist für Qdrant keine gültige Punkt-ID; ein Nachschlagen
        # damit wäre kein leeres Ergebnis, sondern ein 400.
        q = self._Q([])
        h = "a" * 64
        assert resolve_photo_id(q, h) == h
        assert q.asked == []

    def test_punkt_id_wird_aufgeloest(self):
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "hash-xyz"))
        q = self._Q([_P(pid, {"photo_id": "hash-xyz"})])
        assert resolve_photo_id(q, pid) == "hash-xyz"
        assert q.asked == [[pid]]

    def test_unbekannte_punkt_id_ist_ein_fehler(self):
        q = self._Q([])
        with pytest.raises(HTTPException) as e:
            resolve_photo_id(q, "00000000-0000-0000-0000-000000000000")
        assert e.value.status_code == 404

    def test_punkt_ohne_photo_id_ist_ein_fehler(self):
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "x"))
        q = self._Q([_P(pid, {"file_path": "/x.jpg"})])
        with pytest.raises(HTTPException) as e:
            resolve_photo_id(q, pid)
        assert e.value.status_code == 500
