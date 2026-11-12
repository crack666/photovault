"""Bildmodell waehlbar, SigLIP 2 in der Beistell-Collection (#66).

Abgesichert sind die Pfade, die sonst still brechen: ein Tippfehler im
Modellnamen, der in den falschen Vektor rechnet; ein halb gefuellter neuer
Raum, aus dem die Suche schon liest; ein SigLIP-Vektor, der im Feld `clip`
von `photos` landet; und ein Tag-Wechsel, der Beschreibungen ueberstimmt
oder die alten Tags ohne Sicherung ueberschreibt.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ingest import image_space
from ingest.scene_tagger import IMAGE_MODELS, image_model


class TestImageModel:
    def test_vorgabe_ist_clip(self, monkeypatch):
        monkeypatch.delenv("PHOTOVAULT_IMAGE_MODEL", raising=False)
        assert image_model().key == "clip"

    def test_umgebung_waehlt(self, monkeypatch):
        monkeypatch.setenv("PHOTOVAULT_IMAGE_MODEL", "SigLIP2")
        spec = image_model()
        assert (spec.key, spec.dim, spec.collection) == ("siglip2", 1152, "photos_siglip2")

    def test_unbekannt_ist_fehler_kein_rueckfall(self, monkeypatch):
        monkeypatch.setenv("PHOTOVAULT_IMAGE_MODEL", "siglip3")
        with pytest.raises(ValueError):
            image_model()

    def test_clip_bleibt_wie_es_war(self):
        """Temperatur 100 und die alten Schwellen -- sonst aendern sich die
        Tags jedes kuenftigen CLIP-Ingests, ohne dass es jemand wollte."""
        c = IMAGE_MODELS["clip"]
        assert (c.name, c.pretrained, c.temperature, c.vector) == ("ViT-L-14", "openai", 100.0, "clip")


class FakeQdrant:
    def __init__(self, clip_points: int, side_points: int | None):
        self.clip_points = clip_points
        self.side_points = side_points
        self.counts = 0

    def count(self, collection_name, exact=True, count_filter=None):
        self.counts += 1
        if collection_name == "photos":
            return SimpleNamespace(count=self.clip_points)
        if self.side_points is None:
            raise RuntimeError("Collection not found")
        return SimpleNamespace(count=self.side_points)


class TestActive:
    def setup_method(self):
        image_space._cache.clear()

    def test_halb_voll_liest_clip(self):
        assert image_space.active(FakeQdrant(1000, 500), "siglip2").key == "clip"

    def test_voll_liest_siglip(self):
        assert image_space.active(FakeQdrant(1000, 990), "siglip2").key == "siglip2"

    def test_ohne_collection_clip(self):
        assert image_space.active(FakeQdrant(1000, None), "siglip2").key == "clip"

    def test_leerer_bestand_ist_nicht_voll(self):
        """0 >= 0,98 * 0 waere wahr -- und eine frische Installation laese
        aus einer Collection, die es nicht gibt."""
        assert image_space.active(FakeQdrant(0, 0), "siglip2").key == "clip"

    def test_ergebnis_wird_kurz_gemerkt(self):
        q = FakeQdrant(1000, 1000)
        image_space.active(q, "siglip2", now=0.0)
        image_space.active(q, "siglip2", now=10.0)
        assert q.counts == 2  # ein fill() = zwei count()
        image_space.active(q, "siglip2", now=image_space.CACHE_S + 1)
        assert q.counts == 4


class RecordingClient:
    def __init__(self):
        self.upserts: list[tuple[str, list]] = []
        self.created: list[str] = []

    def upsert(self, collection_name, points, wait=True):
        self.upserts.append((collection_name, points))

    def get_collection(self, name):
        if name not in self.created:
            raise RuntimeError("not found")
        vec = SimpleNamespace(size=1152)
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors={"siglip2": vec})))

    def create_collection(self, collection_name, vectors_config):
        self.created.append(collection_name)

    def create_payload_index(self, *a, **kw):
        pass


class TestWriter:
    def _record(self, model):
        from ingest.pipeline import PhotoRecord

        r = PhotoRecord(file_path="/mnt/photo/a.jpg", photo_id="a")
        r.clip_embedding = [0.1] * 4
        r.scene_tags = ["strand"]
        r.image_model = model
        return r

    def test_siglip_landet_nicht_im_clip_feld(self):
        from ingest.qdrant_writer import QdrantWriter

        c = RecordingClient()
        w = QdrantWriter.on(c)
        w.upsert_faces = lambda record: None
        w.upsert(self._record("siglip2"))
        (col1, p1), (col2, p2) = c.upserts
        assert col1 == "photos" and "clip" not in (p1[0].vector or {})
        assert col2 == "photos_siglip2"
        assert p2[0].vector == {"siglip2": [0.1] * 4}
        assert p2[0].payload == {"kind": "photo", "scene_tags": ["strand"]}
        assert p2[0].id == p1[0].id

    def test_clip_wie_bisher(self):
        from ingest.qdrant_writer import QdrantWriter

        c = RecordingClient()
        w = QdrantWriter.on(c)
        w.upsert_faces = lambda record: None
        w.upsert(self._record("clip"))
        assert len(c.upserts) == 1
        assert c.upserts[0][1][0].vector["clip"] == [0.1] * 4


class ScrollClient:
    """`scroll` ueber zwei Collections, `set_payload` mitgeschrieben."""

    def __init__(self, side: dict, photos: dict):
        self.data = {"photos_siglip2": side, "photos": photos}
        self.written: dict[str, dict] = {}

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        pts = [SimpleNamespace(id=k, payload=v) for k, v in self.data[collection_name].items()]
        return pts, None

    def set_payload(self, collection_name, payload, points, wait=False):
        self.written[points[0]] = payload


class TestTags:
    def test_nur_ohne_beschreibung_und_mit_sicherung(self):
        from tools import reembed_image

        side = {"a": {"scene_tags": ["strand"]}, "b": {"scene_tags": ["hund"]},
                "c": {"scene_tags": ["party"]}, "d": {"scene_tags": ["berge"]}}
        photos = {
            "a": {"scene_tags": ["meer"]},                                   # wechselt
            "b": {"caption_de": "Ein Hund", "scene_tags": ["katze"]},         # Beschreibung gewinnt
            "c": {"scene_tags": ["party"]},                                   # schon gleich
            "d": {"scene_tags": ["schnee"], "scene_tags_clip": ["wald"]},     # Sicherung schon da
        }
        c = ScrollClient(side, photos)
        plan = reembed_image.plan_tags(c, IMAGE_MODELS["siglip2"])
        assert sorted(p[0] for p in plan) == ["a", "d"]
        reembed_image.apply_tags(c, plan)
        assert c.written["a"] == {"scene_tags": ["strand"], "scene_tags_clip": ["meer"]}
        # Die erste Sicherung bleibt die echte CLIP-Fassung.
        assert c.written["d"] == {"scene_tags": ["berge"]}
