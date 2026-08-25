"""Ein Re-Ingest darf keine Nutzerarbeit loeschen.

Der Qdrant-Upsert ersetzt das komplette Payload. Ohne Schutz macht der im
README beschriebene Ablauf -- erst schnell ohne Vision, Captions nachziehen --
beim naechsten Metadatenlauf die Vision-Arbeit zunichte, und ein Re-Ingest
loescht ausserdem jede bestaetigte Person und jede eigene Notiz.
"""
import hashlib
import uuid

from ingest.pipeline import IngestConfig, IngestPipeline


class _Point:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class _FakeClient:
    def __init__(self, stored):
        self.stored = stored
        self.requested_payload = None

    def retrieve(self, collection_name, ids, with_payload=None, with_vectors=None):
        self.requested_payload = with_payload
        return [_Point(i, self.stored[i]) for i in ids if i in self.stored]


class _FakeWriter:
    def __init__(self, client):
        self.client = client


def _photo_id(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def _point_id(path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, _photo_id(path)))


class TestPreserveUserData:
    def setup_method(self):
        self.pipeline = IngestPipeline(IngestConfig(source="/x", skip_caption=True))

    def test_caption_survives(self):
        path = "/photos/A/1.jpg"
        client = _FakeClient({_point_id(path): {"caption_de": "Ein Boot im Hafen."}})
        out = self.pipeline._load_preserved(_FakeWriter(client), [path])
        assert out[_photo_id(path)]["caption_de"] == "Ein Boot im Hafen."

    def test_labeled_people_survive(self):
        """Ohne das loescht jeder Re-Ingest die gesamte Labeling-Arbeit."""
        path = "/photos/A/1.jpg"
        client = _FakeClient({
            _point_id(path): {
                "person_ids": ["stefan-menzel", "lennart-behr"],
                "person_names": ["Quirin Falkenstein", "Zelda Quastel"],
            }
        })
        out = self.pipeline._load_preserved(_FakeWriter(client), [path])
        assert out[_photo_id(path)]["person_ids"] == ["stefan-menzel", "lennart-behr"]
        assert out[_photo_id(path)]["person_names"] == ["Quirin Falkenstein", "Zelda Quastel"]

    def test_annotations_survive(self):
        path = "/photos/A/1.jpg"
        client = _FakeClient({_point_id(path): {"annotations": ["Stripclub"]}})
        out = self.pipeline._load_preserved(_FakeWriter(client), [path])
        assert out[_photo_id(path)]["annotations"] == ["Stripclub"]

    def test_only_preserved_fields_are_fetched(self):
        """Nicht das ganze Payload ziehen - das waere bei 50k Fotos teuer."""
        path = "/photos/A/1.jpg"
        client = _FakeClient({_point_id(path): {"caption_de": "x"}})
        self.pipeline._load_preserved(_FakeWriter(client), [path])
        assert client.requested_payload == list(IngestPipeline.PRESERVE_FIELDS)

    def test_empty_values_are_not_kept(self):
        known, empty, absent = "/p/1.jpg", "/p/2.jpg", "/p/3.jpg"
        client = _FakeClient({
            _point_id(known): {"caption_de": "Da."},
            _point_id(empty): {"caption_de": None, "annotations": []},
        })
        out = self.pipeline._load_preserved(_FakeWriter(client), [known, empty, absent])
        assert list(out) == [_photo_id(known)]

    def test_lookup_failure_does_not_break_ingest(self):
        class _Broken(_FakeClient):
            def retrieve(self, *a, **kw):
                raise RuntimeError("qdrant down")

        out = self.pipeline._load_preserved(_FakeWriter(_Broken({})), ["/p/1.jpg"])
        assert out == {}
