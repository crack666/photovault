"""Von Hand geschriebene Captions überleben Vision-Läufe.

Ohne die Sperre würde der nächste Ingest jede manuelle Korrektur durch eine
Modellbeschreibung ersetzen -- und das unbemerkt.
"""
import hashlib
import uuid

from ingest.pipeline import IngestConfig, IngestPipeline


class _Point:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class _Client:
    def __init__(self, stored):
        self.stored = stored

    def retrieve(self, collection_name, ids, with_payload=None, with_vectors=None):
        return [_Point(i, self.stored[i]) for i in ids if i in self.stored]


class _Writer:
    def __init__(self, client):
        self.client = client


def _pid(path):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, hashlib.sha256(path.encode()).hexdigest()))


def _photo_id(path):
    return hashlib.sha256(path.encode()).hexdigest()


class TestPreserveFields:
    def test_lock_fields_are_preserved(self):
        assert "caption_locked" in IngestPipeline.PRESERVE_FIELDS
        assert "caption_source" in IngestPipeline.PRESERVE_FIELDS

    def test_locked_caption_is_loaded(self):
        path = "/p/1.jpg"
        client = _Client({
            _pid(path): {
                "caption_de": "Von Hand geschrieben.",
                "caption_source": "manual",
                "caption_locked": True,
            }
        })
        p = IngestPipeline(IngestConfig(source="/x"))
        out = p._load_preserved(_Writer(client), [path])
        kept = out[_photo_id(path)]
        assert kept["caption_locked"] is True
        assert kept["caption_de"] == "Von Hand geschrieben."

    def test_unlocked_caption_carries_no_lock(self):
        path = "/p/1.jpg"
        client = _Client({
            _pid(path): {"caption_de": "Vom Modell.", "caption_source": "llm",
                         "caption_locked": False}
        })
        p = IngestPipeline(IngestConfig(source="/x"))
        kept = p._load_preserved(_Writer(client), [path])[_photo_id(path)]
        assert "caption_locked" not in kept  # False zählt als leer
        assert kept["caption_source"] == "llm"


class TestRecordDefaults:
    def test_new_record_is_not_locked(self):
        from ingest.pipeline import PhotoRecord

        r = PhotoRecord(photo_id="x", file_path="/p/1.jpg")
        assert r.caption_locked is False
        assert r.caption_source is None
