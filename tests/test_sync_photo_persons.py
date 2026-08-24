"""Foto-Zuordnung aus den Gesichtern ableiten.

Der heikle Fall: Steht eine Person zweimal auf einem Foto und man entfernt
eines der beiden Gesichter, darf der Name *nicht* verschwinden. Inkrementelles
Entfernen bekommt das falsch hin, die Ableitung aus den Gesichtern nicht.
"""
import uuid

import pytest

from api.routes.persons import sync_photo_persons

PHOTOS = "photos"


def point_id(photo_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, photo_id))


class _P:
    def __init__(self, pid, payload, vector=None):
        self.id = pid
        self.payload = payload
        self.vector = vector


class _Client:
    """Minimal-Qdrant: faces je photo_id, photos je point_id."""

    def __init__(self, faces_by_photo, photo_payloads):
        self.faces_by_photo = faces_by_photo
        self.photo_payloads = photo_payloads
        self.writes = {}

    def scroll(self, collection_name, scroll_filter=None, limit=None, offset=None, **kw):
        photo_id = scroll_filter.must[0].match.value
        faces = self.faces_by_photo.get(photo_id, [])
        return [_P(f"f{i}", p, [0.0]) for i, p in enumerate(faces)], None

    def retrieve(self, collection_name, ids, **kw):
        out = []
        for i in ids:
            if i in self.photo_payloads:
                out.append(_P(i, self.photo_payloads[i]))
        return out

    def set_payload(self, collection_name, payload, points, wait=False):
        self.writes[points[0]] = payload
        self.photo_payloads[points[0]] = {**self.photo_payloads.get(points[0], {}), **payload}


@pytest.fixture
def photo():
    return "photoA"


class TestSync:
    def test_names_are_derived_from_faces(self, photo):
        c = _Client(
            {photo: [
                {"person_id": "lennart-behr", "person_name": "Jonas Meyer", "photo_id": photo},
                {"person_id": "max-friedel", "person_name": "Piet Fischer", "photo_id": photo},
            ]},
            {point_id(photo): {"person_ids": [], "person_names": []}},
        )
        changed = sync_photo_persons(c, [photo])
        assert changed == [point_id(photo)]
        w = c.writes[point_id(photo)]
        assert w["person_ids"] == ["lennart-behr", "max-friedel"]
        assert w["person_names"] == ["Jonas Meyer", "Piet Fischer"]

    def test_person_twice_on_one_photo_stays_after_removing_one(self, photo):
        """Zwei Gesichter derselben Person; eines wird gelöst -> Name bleibt."""
        c = _Client(
            {photo: [
                {"person_id": "lennart-behr", "person_name": "Jonas Meyer", "photo_id": photo},
                {"photo_id": photo},  # das gelöste Gesicht, jetzt ohne person_id
            ]},
            {point_id(photo): {"person_ids": ["lennart-behr"], "person_names": ["Jonas Meyer"]}},
        )
        sync_photo_persons(c, [photo])
        assert point_id(photo) not in c.writes  # unverändert, kein Schreibzugriff

    def test_last_face_removed_clears_the_name(self, photo):
        c = _Client(
            {photo: [{"photo_id": photo}]},
            {point_id(photo): {"person_ids": ["lennart-behr"], "person_names": ["Jonas Meyer"]}},
        )
        sync_photo_persons(c, [photo])
        assert c.writes[point_id(photo)] == {"person_ids": [], "person_names": []}

    def test_skipped_faces_are_not_persons(self, photo):
        c = _Client(
            {photo: [{"person_id": "_skipped", "person_name": "Übersprungen", "photo_id": photo}]},
            {point_id(photo): {"person_ids": [], "person_names": []}},
        )
        sync_photo_persons(c, [photo])
        assert point_id(photo) not in c.writes

    def test_unchanged_photo_is_not_rewritten(self, photo):
        """Kein Schreibzugriff ohne Änderung - sonst folgt ein unnötiger Re-Embed."""
        c = _Client(
            {photo: [{"person_id": "a", "person_name": "A", "photo_id": photo}]},
            {point_id(photo): {"person_ids": ["a"], "person_names": ["A"]}},
        )
        assert sync_photo_persons(c, [photo]) == []

    def test_duplicate_photo_ids_are_processed_once(self, photo):
        c = _Client(
            {photo: [{"person_id": "a", "person_name": "A", "photo_id": photo}]},
            {point_id(photo): {"person_ids": [], "person_names": []}},
        )
        assert sync_photo_persons(c, [photo, photo, photo]) == [point_id(photo)]

    def test_missing_photo_is_skipped(self):
        c = _Client({"ghost": [{"person_id": "a", "person_name": "A", "photo_id": "ghost"}]}, {})
        assert sync_photo_persons(c, ["ghost"]) == []
