"""Ein Video auf die visuelle Karte: drei Frames, der Medoid zaehlt.

Abgesichert wird die Wahl des Vertreters -- nicht CLIP selbst. Der Medoid
muss ein echter Frame sein (nie der Mittelwert), bei einem Frame dieser
eine, und wo kein Frame einen Vektor bekam, gibt es keinen Vertreter statt
eines erfundenen.
"""
from __future__ import annotations

import numpy as np
import pytest

from ingest.video_clip import choose, clip_video, pick_medoid


class TestMedoid:
    def test_der_mittlere_von_drei_aehnlichen(self):
        # Frame 1 liegt zwischen 0 und 2 -- er ist dem Mittel am naechsten.
        e = [[1.0, 0.0, 0.1], [0.7, 0.7, 0.1], [0.0, 1.0, 0.1]]
        assert pick_medoid(e) == 1

    def test_zwei_gleiche_und_ein_ausreisser(self):
        """Zwei Frames zeigen dasselbe, einer etwas anderes: der Vertreter
        ist einer der beiden -- nicht der Ausreisser, und nicht ein Punkt
        dazwischen, an dem kein Bild ist."""
        e = [[1.0, 0.0], [1.0, 0.02], [0.0, 1.0]]
        assert pick_medoid(e) in (0, 1)

    def test_ein_frame(self):
        assert pick_medoid([[0.3, 0.4]]) == 0

    def test_keiner(self):
        with pytest.raises(ValueError):
            pick_medoid([])

    def test_laenge_spielt_keine_rolle(self):
        """Verglichen wird die Richtung, nicht die Laenge."""
        e = [[10.0, 0.0], [0.7, 0.7], [0.0, 0.001]]
        assert pick_medoid(e) == 1


class TestChoose:
    FRAMES = [(0.5, "bild-a"), (2.5, "bild-b"), (4.5, "bild-c")]

    def test_medoid_traegt_zeitpunkt_und_bild(self):
        results = [
            {"embedding": [1.0, 0.0], "tags": ["a"]},
            {"embedding": [0.7, 0.7], "tags": ["b"]},
            {"embedding": [0.0, 1.0], "tags": ["c"]},
        ]
        out = choose(results, self.FRAMES)
        assert out["poster_ss"] == 2.5
        assert out["frame"] == "bild-b"
        assert out["tags"] == ["b"]
        assert out["embedding"] == [0.7, 0.7]

    def test_frames_ohne_vektor_werden_uebergangen(self):
        """CLIP kann auf einem Frame scheitern (Schwarzbild, Dekodierfehler).
        Der Medoid wird unter den uebrigen gewaehlt, mit ihren Zeitpunkten."""
        results = [
            {"embedding": None, "tags": []},
            {"embedding": [1.0, 0.0], "tags": ["b"]},
            {"embedding": [0.9, 0.1], "tags": ["c"]},
        ]
        out = choose(results, self.FRAMES)
        assert out["poster_ss"] in (2.5, 4.5)
        assert out["frame"] in ("bild-b", "bild-c")

    def test_gar_kein_vektor_heisst_kein_vertreter(self):
        out = choose([{"embedding": None, "tags": []}], self.FRAMES[:1])
        assert out == {"embedding": None, "tags": [], "poster_ss": None, "frame": None}


class TestClipVideo:
    def test_ruft_den_stapel_einmal(self):
        gesehen = []

        class Tagger:
            def process_images(self, images):
                gesehen.append(list(images))
                return [{"embedding": [1.0, 0.0], "tags": ["x"]},
                        {"embedding": [0.0, 1.0], "tags": ["y"]}]

        out = clip_video(Tagger(), [(0.1, "a"), (0.9, "b")])
        assert gesehen == [["a", "b"]]
        assert out["frame"] in ("a", "b")

    def test_ohne_frames_ohne_vertreter(self):
        out = clip_video(object(), [])
        assert out["embedding"] is None


class TestLoadVideo:
    """`_load_video` holt die Caption-Frames und gibt den ersten als
    vorlaeufiges Poster -- dort, wo das Poster schon immer lag."""

    def test_frames_am_record_erster_als_rgb(self, monkeypatch):
        from ingest import pipeline
        from ingest.pipeline import PhotoRecord

        monkeypatch.setattr("ingest.video.probe", lambda p: {"duration": 9.0})
        frames = [(0.9, "f1"), (4.5, "f2"), (8.1, "f3")]
        monkeypatch.setattr("ingest.video_clip.sample_frames", lambda p, d=None: frames)
        rec = PhotoRecord(photo_id="x", file_path="/x.mp4")
        raw, rgb, warn = pipeline._load_video("/x.mp4", rec)
        assert rgb == "f1" and raw == "f1" and warn is None
        assert rec._frames == frames
        assert rec.duration_s == 9.0

    def test_fehlende_frames_sind_unlesbar(self, monkeypatch):
        from ingest import pipeline
        from ingest.pipeline import PhotoRecord

        monkeypatch.setattr("ingest.video.probe", lambda p: {"duration": 9.0})
        monkeypatch.setattr("ingest.video_clip.sample_frames",
                            lambda p, d=None: (_ for _ in ()).throw(RuntimeError("ffmpeg")))
        rec = PhotoRecord(photo_id="x", file_path="/x.mp4")
        assert pipeline._load_video("/x.mp4", rec) == (None, None, "unreadable")


class TestBackfillVideo:
    def test_video_bekommt_vektor_poster_und_frischen_cache(self, monkeypatch):
        from tools import backfill_clip as bc

        class Tagger:
            def __init__(self, *a, **kw):
                pass

            def process_images(self, images):
                return [{"embedding": [1.0, 0.0], "tags": ["urlaub"]},
                        {"embedding": [0.9, 0.1], "tags": ["meer"]},
                        {"embedding": [0.0, 1.0], "tags": ["nacht"]}]

        monkeypatch.setattr("ingest.scene_tagger.SceneTagger", Tagger)
        monkeypatch.setattr("ingest.video_clip.sample_frames",
                            lambda p, d=None: [(1.0, "a"), (5.0, "b"), (9.0, "c")])
        geleert = []
        monkeypatch.setattr("api.thumbs.drop_cached", lambda fp: geleert.append(fp) or 1)

        class Q:
            def __init__(self):
                self.vectors, self.payloads = {}, {}

            def update_vectors(self, collection_name, points, wait):
                for p in points:
                    self.vectors[str(p.id)] = p.vector

            def set_payload(self, collection_name, payload, points, wait):
                for i in points:
                    self.payloads.setdefault(str(i), {}).update(payload)

        q = Q()
        got = bc.run(q, "photos", [("v", "/clip.mp4", True, False)], "/models", 0.2)
        assert got["done"] == 1
        assert q.vectors["v"] == {"clip": [1.0, 0.0]} or q.vectors["v"] == {"clip": [0.9, 0.1]}
        assert q.payloads["v"]["poster_ss"] in (1.0, 5.0)
        assert "scene_tags" in q.payloads["v"]          # keine Beschreibung -> CLIP ist Rueckfall
        assert geleert == ["/clip.mp4"]

    def test_mit_beschreibung_bleiben_die_etiketten_unangetastet(self, monkeypatch):
        """Vorher schrieb der Lauf CLIP-Etiketten bedingungslos -- bei einem
        beschriebenen Foto haette das die besseren aus der Beschreibung
        ueberschrieben."""
        from tools import backfill_clip as bc

        class Tagger:
            def __init__(self, *a, **kw):
                pass

            def process_image(self, image):
                return {"embedding": [1.0], "tags": ["urlaub"]}

        monkeypatch.setattr("ingest.scene_tagger.SceneTagger", Tagger)
        monkeypatch.setattr("ingest.pipeline._load_image", lambda p: (object(), object(), None))

        class Q:
            def __init__(self):
                self.vectors, self.payloads = {}, {}

            def update_vectors(self, collection_name, points, wait):
                for p in points:
                    self.vectors[str(p.id)] = p.vector

            def set_payload(self, collection_name, payload, points, wait):
                for i in points:
                    self.payloads.setdefault(str(i), {}).update(payload)

        q = Q()
        bc.run(q, "photos", [("f", "/a.jpg", False, True)], "/models", 0.2)
        assert q.vectors["f"] == {"clip": [1.0]}
        assert "f" not in q.payloads


class TestPosterThumb:
    def test_render_nimmt_den_gewaehlten_frame(self, monkeypatch, tmp_path):
        from PIL import Image

        from api import thumbs

        gefragt = []

        def frame_image(path, ss):
            gefragt.append(ss)
            return Image.new("RGB", (64, 48), (200, 30, 30))

        monkeypatch.setattr("ingest.video.frame_image", frame_image)
        monkeypatch.setattr("ingest.video.poster_image",
                            lambda p, d=None: (_ for _ in ()).throw(AssertionError("Poster statt Medoid")))
        data, warn = thumbs._render("/x.mp4", 160, None, 0.35, None, poster_ss=4.5)
        assert gefragt == [4.5] and data[:2] == b"\xff\xd8"

    def test_ohne_angabe_das_alte_poster(self, monkeypatch):
        from PIL import Image

        from api import thumbs

        monkeypatch.setattr("ingest.video.poster_image",
                            lambda p, d=None: Image.new("RGB", (64, 48)))
        monkeypatch.setattr("ingest.video.frame_image",
                            lambda p, ss: (_ for _ in ()).throw(AssertionError("kein poster_ss")))
        data, _ = thumbs._render("/x.mp4", 160, None, 0.35, None)
        assert data[:2] == b"\xff\xd8"


class TestVideoFlag:
    def test_flag_auf_der_karte(self):
        from tools.atlas_build import FLAG_VIDEO, photo_flags

        m = {"person_ids": [], "caption": "", "date_source": None, "event_name": None,
             "gps": None, "taken_at": None, "face_count": 0, "kind": "video"}
        assert photo_flags(m, False, False) & FLAG_VIDEO
        m["kind"] = "photo"
        assert not photo_flags(m, False, False) & FLAG_VIDEO


class TestSampleFramesRobust:
    def test_ein_leerer_offset_kostet_nicht_das_video(self, monkeypatch):
        """Ein aus einem GIF gewandelter Clip meldete 3,24 s, hatte aber vor
        90 % keinen Frame mehr. Zwei von drei Frames sind ein Video."""
        from PIL import Image

        from ingest import video_clip

        def frame_image(path, ss):
            if ss > 2.5:
                raise RuntimeError("ffmpeg frame exit 0: ")
            return Image.new("RGB", (32, 24))

        monkeypatch.setattr("ingest.video.frame_image", frame_image)
        monkeypatch.setattr("ingest.video.sample_offsets", lambda d: [0.32, 1.62, 2.92])
        frames = video_clip.sample_frames("/gif.mp4", 3.24)
        assert [ss for ss, _ in frames] == [0.32, 1.62]

    def test_gar_kein_frame_ist_ein_fehler(self, monkeypatch):
        from ingest import video_clip

        monkeypatch.setattr("ingest.video.frame_image",
                            lambda p, ss: (_ for _ in ()).throw(RuntimeError("kaputt")))
        monkeypatch.setattr("ingest.video.sample_offsets", lambda d: [0.0])
        import pytest
        with pytest.raises(RuntimeError):
            video_clip.sample_frames("/x.mp4", 1.0)
