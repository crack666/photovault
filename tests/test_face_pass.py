"""Gesichter auf Video-Frames: zusammenziehen, vorschlagen, nicht zuordnen."""
from __future__ import annotations

from ingest.caption_pass import Photo, build_filter
from ingest.face_pass import (
    FACE_SOURCE,
    collapse_same_person,
    run,
    suggestion_ids,
    write_faces,
)


def _face(vec, *, frontality=0.5, score=0.8, area=100, box=None, ss=0.1):
    return {
        "embedding": list(vec),
        "frontality": frontality,
        "score": score,
        "area": area,
        "box": box or [0, 0, 10, 10],
        "frame_ss": ss,
    }


class TestCollapse:
    def test_same_person_keeps_the_more_frontal(self):
        vec = [1.0, 0.0, 0.0]
        a = _face(vec, frontality=0.3, ss=0.4)
        b = _face(vec, frontality=0.9, ss=1.8)
        out = collapse_same_person([a, b])
        assert len(out) == 1
        assert out[0]["frontality"] == 0.9
        assert out[0]["frame_ss"] == 1.8

    def test_different_people_stay(self):
        a = _face([1.0, 0.0, 0.0], ss=0.4)
        b = _face([0.0, 1.0, 0.0], ss=1.8)
        out = collapse_same_person([a, b])
        assert len(out) == 2

    def test_empty_embedding_is_dropped(self):
        assert collapse_same_person([{"embedding": None, "box": [1, 2, 3, 4]}]) == []


class TestSuggestions:
    def test_unique_ids_in_first_seen_order(self):
        class Matcher:
            def suggest(self, embedding, limit=1):
                if embedding[0] > 0.5:
                    return [{"id": "zelda", "name": "Zelda", "score": 0.8}]
                return [{"id": "bodo", "name": "Bodo", "score": 0.7}]

        faces = [_face([1.0, 0, 0]), _face([1.0, 0, 0]), _face([0.0, 1, 0])]
        assert suggestion_ids(faces, Matcher()) == ["zelda", "bodo"]

    def test_skipped_marker_is_not_a_suggestion(self):
        class Matcher:
            def suggest(self, embedding, limit=1):
                return [{"id": "_skipped", "name": "übersprungen", "score": 0.9}]

        assert suggestion_ids([_face([1.0, 0, 0])], Matcher()) == []


class TestWrite:
    def test_does_not_assign_person_ids(self):
        """Vorschlag, nie stilles Auto-Label -- wie bei Fotos."""
        class Writer:
            def __init__(self):
                self.record = None

            def upsert_faces(self, record):
                self.record = record

        class Client:
            def __init__(self):
                self.payloads = []

            def set_payload(self, collection_name, payload, points, wait=False):
                self.payloads.append(payload)

        photo = Photo("p1", {
            "file_path": "/clip.mp4",
            "photo_id": "aaa",
            "content_sha256": "h",
        })
        writer = Writer()
        client = Client()
        faces = [_face([1.0, 0, 0], box=[2, 2, 12, 12], ss=1.1)]
        write_faces(client, writer, photo, faces, ["zelda"])
        assert "person_ids" not in client.payloads[0]
        assert "person_names" not in client.payloads[0]
        assert client.payloads[0]["person_suggestions"] == ["zelda"]
        assert client.payloads[0]["face_source"] == FACE_SOURCE
        assert client.payloads[0]["face_count"] == 1
        assert writer.record.faces[0]["frame_ss"] == 1.1


class TestRun:
    def test_skips_already_scanned(self, monkeypatch):
        from ingest import face_pass

        done = Photo(0, {"file_path": "/a.mp4", "photo_id": "a",
                         "face_source": FACE_SOURCE, "kind": "video"})
        monkeypatch.setattr(face_pass, "select_photos", lambda *a, **k: [done])
        stats = run(object(), track=False, embedder=object(), writer=object(),
                    matcher=object())
        assert stats["selected"] == 0
        assert stats["processed"] == 0

    def test_writes_faces_not_names(self, monkeypatch):
        from ingest import face_pass

        photo = Photo(0, {"file_path": "/clip.mp4", "photo_id": "aaa", "kind": "video"})
        monkeypatch.setattr(face_pass, "select_photos", lambda *a, **k: [photo])
        monkeypatch.setattr(
            face_pass, "load_video_frames",
            lambda *a, **k: [(0.4, object())],
        )

        class Embedder:
            def process(self, file_path, image=None, bgr=None):
                return {"faces": [_face([1.0, 0, 0], box=[0, 0, 8, 8])]}

        class Matcher:
            def suggest(self, embedding, limit=1):
                return [{"id": "zelda", "name": "Zelda", "score": 0.8}]

        class Writer:
            def __init__(self):
                self.got = None

            def upsert_faces(self, record):
                self.got = record

        class Client:
            def __init__(self):
                self.payloads = []

            def set_payload(self, collection_name, payload, points, wait=False):
                self.payloads.append(payload)

        writer = Writer()
        client = Client()
        stats = run(client, track=False, embedder=Embedder(), writer=writer,
                    matcher=Matcher(), io_workers=1, ffmpeg_workers=1)
        assert stats["processed"] == 1
        assert stats["faces"] == 1
        assert "person_ids" not in client.payloads[0]
        assert client.payloads[0]["person_suggestions"] == ["zelda"]
        assert writer.got.faces[0]["frame_ss"] == 0.4

    def test_dry_run_writes_nothing(self, monkeypatch):
        from ingest import face_pass

        photo = Photo(0, {"file_path": "/clip.mp4", "photo_id": "aaa", "kind": "video"})
        monkeypatch.setattr(face_pass, "select_photos", lambda *a, **k: [photo])
        called = []
        monkeypatch.setattr(face_pass, "load_video_frames",
                            lambda *a, **k: called.append("frames") or [])
        stats = run(object(), dry_run=True, track=False)
        assert stats["selected"] == 1
        assert stats["processed"] == 0
        assert called == []

    def test_ffmpeg_and_gpu_run_on_different_threads(self, monkeypatch):
        """Sonst wartet die Karte auf jeden Seek, und der Taskmanager zeigt 0 %."""
        import threading

        from ingest import face_pass

        photo = Photo(0, {"file_path": "/clip.mp4", "photo_id": "aaa", "kind": "video"})
        monkeypatch.setattr(face_pass, "select_photos", lambda *a, **k: [photo])
        names = {}

        def load(*a, **k):
            names["read"] = threading.current_thread().name
            return [(0.1, object())]

        monkeypatch.setattr(face_pass, "load_video_frames", load)

        class Embedder:
            def process(self, file_path, image=None, bgr=None):
                names["gpu"] = threading.current_thread().name
                return {"faces": []}

        class Matcher:
            def suggest(self, embedding, limit=1):
                return []

        class Writer:
            def upsert_faces(self, record):
                pass

        class Client:
            def set_payload(self, **kw):
                pass

        run(Client(), track=False, embedder=Embedder(), writer=Writer(),
            matcher=Matcher(), io_workers=2, ffmpeg_workers=1)
        assert names["read"].startswith("read-")
        assert names["gpu"] == "gpu"
        assert names["read"] != names["gpu"]


def test_locked_captions_stay_in_the_face_filter():
    keys = [c.key for c in (build_filter(skip_locked=False).must_not or [])]
    assert "caption_locked" not in keys
