"""Der neue Bildraum darf nichts zeigen, was der alte nicht zeigt (#66).

PhotoVault kennt keine Anmeldung; jede Installation hat ihren eigenen Index.
Die eine Sichtbarkeitsregel ist `visible()` -- nicht im Papierkorb, und im
Index vorhanden. Die Beistell-Collection traegt diesen Stempel nicht, also
muss „Mehr davon" ihn nachtraeglich anlegen, und zwar gleich streng und ohne
die Trefferliste zu kuerzen. Gegen ein echtes Qdrant im Speicher, nicht gegen
ein Doppel: die Frage ist, was Qdrant liefert.

Dazu die Regel „geschrieben wird, was gelesen wird": sonst verschwinden neue
Fotos auf einer Bestandsinstallation aus Aehnlichkeit und Karte.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from api.routes import photos as route
from ingest import image_space
from ingest.scene_tagger import IMAGE_MODELS

SIG = IMAGE_MODELS["siglip2"]


def _id(n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"foto-{n}"))


def _vec(n: int) -> list[float]:
    # Gleichmaessig wachsender Winkel: kleines n = naeher am Beispiel 0.
    import math

    a = n * 0.002
    return [math.cos(a), math.sin(a), 0.0, 0.0]


def _bestand(anzahl: int = 300, papierkorb=(), verwaist=(), nur_clip=(), nur_siglip=()):
    q = QdrantClient(":memory:")
    q.create_collection("photos", vectors_config={
        "clip": VectorParams(size=4, distance=Distance.COSINE),
        "text": VectorParams(size=4, distance=Distance.COSINE)})
    q.create_collection(SIG.collection, vectors_config={
        SIG.vector: VectorParams(size=4, distance=Distance.COSINE)})
    q.upsert("photos", [PointStruct(
        id=_id(n), vector={} if n in nur_siglip else {"clip": _vec(n)},
        payload={"file_path": f"/f/{n}.jpg",
                 **({"trashed_at": "2026-11-25T00:00:00"} if n in papierkorb else {})},
    ) for n in range(anzahl)])
    q.upsert(SIG.collection, [
        PointStruct(id=_id(n), vector={SIG.vector: _vec(n)}, payload={"kind": "photo"})
        for n in [*range(anzahl), *verwaist] if n not in nur_clip])
    return q


@pytest.fixture(autouse=True)
def _frisch(monkeypatch):
    monkeypatch.delenv("PHOTOVAULT_IMAGE_MODEL", raising=False)
    image_space._cache.clear()
    yield
    image_space._cache.clear()


def _frage(monkeypatch, q, **kw):
    monkeypatch.setattr(route, "client", lambda: q)
    return route.similar(route.SimilarRequest(**kw))


class TestGleichStreng:
    #: 150 naechste Nachbarn liegen im Papierkorb, 40 weitere sind geloescht.
    PAPIERKORB = set(range(1, 151))
    VERWAIST = set(range(1000, 1040))

    def test_papierkorb_und_geloeschte_bleiben_unsichtbar(self, monkeypatch):
        q = _bestand(papierkorb=self.PAPIERKORB, verwaist=self.VERWAIST)
        neu = _frage(monkeypatch, q, photo_ids=[_id(0)], limit=30)
        assert neu["space"] == "siglip2"
        verboten = {_id(n) for n in self.PAPIERKORB | self.VERWAIST}
        assert not verboten & {r["id"] for r in neu["results"]}

    def test_alter_und_neuer_raum_zeigen_dasselbe(self, monkeypatch):
        """Gleiche Vektoren in beiden Raeumen: dann muss auch die Antwort
        gleich sein -- Reihenfolge, Laenge, Sichtbarkeit."""
        q = _bestand(papierkorb=self.PAPIERKORB, verwaist=self.VERWAIST)
        neu = _frage(monkeypatch, q, photo_ids=[_id(0)], limit=30)
        monkeypatch.setenv("PHOTOVAULT_IMAGE_MODEL", "clip")
        alt = _frage(monkeypatch, q, photo_ids=[_id(0)], limit=30)
        assert (alt["space"], neu["space"]) == ("clip", "siglip2")
        assert [r["id"] for r in alt["results"]] == [r["id"] for r in neu["results"]]
        assert len(neu["results"]) == 30  # voller Papierkorb kuerzt nicht

    def test_mehrere_beispiele_mit_gegenbeispiel(self, monkeypatch):
        q = _bestand(papierkorb=self.PAPIERKORB)
        got = _frage(monkeypatch, q, photo_ids=[_id(0), _id(151)], negative_ids=[_id(299)], limit=20)
        assert got["space"] == "siglip2" and len(got["results"]) == 20
        assert not {_id(n) for n in self.PAPIERKORB} & {r["id"] for r in got["results"]}

    def test_alles_im_papierkorb_ist_leer_statt_endlos(self, monkeypatch):
        q = _bestand(anzahl=100, papierkorb=set(range(1, 100)))
        assert _frage(monkeypatch, q, photo_ids=[_id(0)], limit=10)["results"] == []


class TestBeispiele:
    def test_fehlendes_beispiel_kippt_nicht_die_ganze_anfrage(self, monkeypatch):
        q = _bestand(nur_clip={1, 2})
        got = _frage(monkeypatch, q, photo_ids=[_id(0), _id(1)], limit=10)
        assert got["space"] == "siglip2" and len(got["results"]) == 10

    def test_nur_fehlende_beispiele_lesen_den_alten_raum(self, monkeypatch):
        q = _bestand(nur_clip={1, 2})
        got = _frage(monkeypatch, q, photo_ids=[_id(1)], limit=10)
        assert got["space"] == "clip" and len(got["results"]) == 10

    def test_neues_foto_ohne_clip_vektor_wird_gefunden(self, monkeypatch):
        q = _bestand(nur_siglip={5})
        got = _frage(monkeypatch, q, photo_ids=[_id(0)], limit=10)
        assert _id(5) in {r["id"] for r in got["results"]}
        assert _frage(monkeypatch, q, photo_ids=[_id(5)], limit=3)["space"] == "siglip2"

    def test_leere_auswahl_bleibt_ein_fehler(self, monkeypatch):
        with pytest.raises(HTTPException):
            _frage(monkeypatch, _bestand(anzahl=3), photo_ids=[])


class TestIngestModell:
    def test_bestand_nur_in_clip_bleibt_clip(self, monkeypatch):
        monkeypatch.setattr(image_space, "_cuda", lambda: True)
        q = _bestand(nur_clip=set(range(300)))
        assert image_space.ingest_key(q) == "clip"

    def test_nach_dem_wechsel_siglip_auch_ohne_karte(self, monkeypatch):
        monkeypatch.setattr(image_space, "_cuda", lambda: False)
        assert image_space.ingest_key(_bestand()) == "siglip2"

    @pytest.mark.parametrize("karte,modell", [(True, "siglip2"), (False, "clip")])
    def test_leerer_index_nach_hardware(self, monkeypatch, karte, modell):
        monkeypatch.setattr(image_space, "_cuda", lambda: karte)
        assert image_space.ingest_key(_bestand(anzahl=0)) == modell

    def test_frische_installation_liest_was_sie_schreibt(self):
        """Nur SigLIP, kein einziger CLIP-Vektor: frueher blieb das bei CLIP
        haengen, und „Mehr davon" scheiterte fuer jedes Foto."""
        q = _bestand(anzahl=10, nur_siglip=set(range(10)))
        assert image_space.active(q).key == "siglip2"
        assert image_space.ingest_key(q) == "siglip2"

    def test_umgebung_gewinnt(self, monkeypatch):
        monkeypatch.setenv("PHOTOVAULT_IMAGE_MODEL", "clip")
        assert image_space.ingest_key(_bestand()) == "clip"

    def test_unerreichbares_qdrant_bleibt_bei_clip(self):
        class Kaputt:
            def __getattr__(self, name):
                raise RuntimeError("weg")

        assert image_space.ingest_key(Kaputt()) == "clip"


class TestLoeschen:
    def test_forget_nimmt_nur_die_genannten(self):
        q = _bestand(anzahl=10)
        image_space.forget(q, [_id(3), _id(4)])
        assert q.count(SIG.collection).count == 8
        assert q.count("photos").count == 10

    def test_forget_ohne_collection_ist_still(self):
        q = QdrantClient(":memory:")
        image_space.forget(q, [_id(1)])
        assert not q.collection_exists(SIG.collection)


class TestReIngest:
    def test_clip_vektor_ueberlebt_den_ingest_mit_siglip(self):
        """`upsert` ersetzt alle Vektoren; ohne Mitnahme waere der Rueckweg
        nach dem ersten Re-Ingest weg."""
        from ingest.pipeline import PhotoRecord
        from ingest.qdrant_writer import QdrantWriter

        q = _bestand(anzahl=0)
        # Die Beistell-Collection des Tests hat 4 statt 1152 Dimensionen.
        w = QdrantWriter.on(q)
        w._side_ready = {"siglip2"}
        w.upsert_faces = lambda record: None
        r = PhotoRecord(file_path="/f/a.jpg", photo_id="a")
        r.clip_embedding, r.image_model = [1.0, 0.0, 0.0, 0.0], "clip"
        w.upsert(r)
        r.clip_embedding, r.image_model = [0.0, 1.0, 0.0, 0.0], "siglip2"
        w.upsert(r)
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "a"))
        p = q.retrieve("photos", [pid], with_vectors=True)[0]
        s = q.retrieve(SIG.collection, [pid], with_vectors=True)[0]
        assert p.vector["clip"] == pytest.approx([1.0, 0.0, 0.0, 0.0])
        assert s.vector["siglip2"] == pytest.approx([0.0, 1.0, 0.0, 0.0])


class TestZustand:
    def test_update_hinweis_nur_mit_karte(self, monkeypatch):
        from api import capabilities
        from api.routes import ingest as ingest_route

        q = _bestand(nur_clip=set(range(10, 300)))
        for karte in (True, False):
            image_space._cache.clear()
            monkeypatch.setattr(capabilities, "_accelerator", lambda: {"cuda": karte})
            raum = ingest_route._image_space(q)
            assert raum["active"] == "clip" and raum["upgrade"] is karte

    def test_luecke_zaehlt_im_gelesenen_raum(self, monkeypatch):
        from api.routes import ingest as ingest_route

        q = _bestand(nur_siglip=set(range(290, 300)))
        q.upsert("photos", [PointStruct(id=_id(9999), vector={}, payload={"file_path": "/f/x.jpg"})])
        monkeypatch.setattr(ingest_route, "client", lambda: q)
        luecken = {g["vector"]: g["missing"] for g in ingest_route.index_state()["gaps"]}
        assert luecken["siglip2"] == 1 and "clip" not in luecken
        namen = ingest_route.gap_files("siglip2")["photos"]
        assert [p["file_path"] for p in namen] == ["/f/x.jpg"]


def test_job_steht_auf_der_liste_und_ist_abbrechbar():
    """Wer von einer aelteren Fassung kommt, hat keine Konsole: der Wechsel
    muss von der Jobs-Seite gehen, und der Abbruch muss den Prozess kennen."""
    from api.routes.jobs import RUNNABLE, build_argv
    from ingest.jobs import KIND_MARKERS

    spec = RUNNABLE["image_space"]
    assert KIND_MARKERS[spec.kind] == (spec.module,)
    assert build_argv(spec, dry_run=False, limit=None)[-1] == "--mit-tags"


def test_reembed_tags_erneuern_genau_die_textvektoren(monkeypatch):
    """Die Tags stehen im Dokument des Textvektors -- ohne Nachzug suchte die
    Freitextsuche weiter nach den alten."""
    from api import capabilities
    from tools import reembed_image

    q = SimpleNamespace(set_payload=lambda **kw: None)
    gesehen = {}
    monkeypatch.setattr(capabilities, "missing", lambda **kw: "")
    monkeypatch.setattr("ingest.reembed.rebuild_text_vectors",
                        lambda c, ids: gesehen.setdefault("ids", ids) and {} or
                        {"updated": len(ids), "skipped": 0, "failed": 0})
    plan = [("a", ["strand"], ["meer"], False), ("b", ["hund"], [], False)]
    assert reembed_image._retext(q, plan)["updated"] == 2
    assert gesehen["ids"] == ["a", "b"]
    monkeypatch.setattr(capabilities, "missing", lambda **kw: "kein Modell")
    assert reembed_image._retext(q, plan) is None


def test_das_bildmodell_laedt_nur_einmal(monkeypatch):
    """Acht Vorbereiter-Faeden, ein Modell -- sonst reicht der Speicher nicht."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from ingest.scene_tagger import SceneTagger

    geladen = []

    def laden(self):
        geladen.append(1)
        time.sleep(0.05)
        self._text_features = object()

    monkeypatch.setattr(SceneTagger, "_load", laden)
    tagger = SceneTagger(model="siglip2")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: tagger._ensure_loaded(), range(8)))
    assert geladen == [1]
