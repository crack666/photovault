"""Unbenannte Gesichter zu bereits benannten Personen zuordnen — als Vorschlag.

Am echten Bestand gehören 4868 von 16722 unbenannten Gesichtern zu einer der
93 benannten Personen. Als einzelne Cluster erscheinen sie dutzendfach; als
eine Rückfrage je Person sind es eine Handvoll Entscheidungen.
"""
from __future__ import annotations

import numpy as np
import pytest

from api.known_faces import MIN_BATCH, candidates, centroids


def _vec(base, jitter=0.0, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    v = np.zeros(dim, dtype=np.float32)
    v[base % dim] = 1.0
    if jitter:
        v = v + rng.normal(scale=jitter, size=dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _labeled(pid, name, base, n=4):
    return [{"id": f"{pid}-l{i}", "vector": _vec(base, 0.05, seed=i),
             "payload": {"person_id": pid, "person_name": name}} for i in range(n)]


def _unlabeled(prefix, base, n, jitter=0.05):
    return [{"id": f"{prefix}{i}", "vector": _vec(base, jitter, seed=100 + i),
             "payload": {"score": 0.9}} for i in range(n)]


class TestCentroids:
    def test_one_centroid_per_person(self):
        keys, mat = centroids(_labeled("p1", "Sophie", 1) + _labeled("p2", "Erik", 5))
        assert len(keys) == 2 and mat.shape[0] == 2

    def test_faces_without_a_person_are_ignored(self):
        keys, _ = centroids([{"id": "x", "vector": _vec(1), "payload": {}}])
        assert keys == []

    def test_no_labels_at_all(self):
        keys, mat = centroids([])
        assert keys == [] and mat.shape[0] == 0


class TestCandidates:
    def test_similar_faces_land_at_their_person(self):
        got = candidates(_unlabeled("u", 1, 6), _labeled("p1", "Sophie", 1))
        assert len(got) == 1
        assert got[0]["name"] == "Sophie" and got[0]["count"] == 6

    def test_strangers_stay_out(self):
        """Sonst waere die Rueckfrage wertlos -- man muesste jedes Bild pruefen."""
        got = candidates(_unlabeled("u", 9, 6), _labeled("p1", "Sophie", 1))
        assert got == []

    def test_each_face_goes_to_at_most_one_person(self):
        """Zwei „Ja" auf dasselbe Gesicht waeren ein Widerspruch."""
        labeled = _labeled("p1", "Sophie", 1) + _labeled("p2", "Erik", 2)
        got = candidates(_unlabeled("u", 1, 8), labeled)
        seen = [f["face_id"] for b in got for f in b["faces"]]
        assert len(seen) == len(set(seen))

    def test_small_batches_are_left_to_the_clusters(self):
        got = candidates(_unlabeled("u", 1, MIN_BATCH - 1), _labeled("p1", "Sophie", 1))
        assert got == []

    def test_biggest_batch_first(self):
        labeled = _labeled("p1", "Sophie", 1) + _labeled("p2", "Erik", 5)
        unlabeled = _unlabeled("a", 1, 3) + _unlabeled("b", 5, 7)
        got = candidates(unlabeled, labeled)
        assert [b["name"] for b in got] == ["Erik", "Sophie"]

    def test_most_similar_face_first_within_a_batch(self):
        """Wer oben bestaetigt, sieht sofort, ob die Gruppe stimmt."""
        unlabeled = _unlabeled("u", 1, 5, jitter=0.02) + _unlabeled("w", 1, 5, jitter=0.35)
        got = candidates(unlabeled, _labeled("p1", "Sophie", 1))
        scores = [f["score"] for f in got[0]["faces"]]
        assert scores == sorted(scores, reverse=True)

    def test_the_threshold_decides(self):
        unlabeled = _unlabeled("u", 1, 5, jitter=0.5)
        assert candidates(unlabeled, _labeled("p1", "Sophie", 1), threshold=0.99) == []
        assert candidates(unlabeled, _labeled("p1", "Sophie", 1), threshold=0.1)


class TestRobustness:
    def test_no_unlabeled_faces(self):
        assert candidates([], _labeled("p1", "Sophie", 1)) == []

    def test_no_labeled_people(self):
        assert candidates(_unlabeled("u", 1, 5), []) == []

    def test_faces_without_a_vector_are_skipped(self):
        unlabeled = _unlabeled("u", 1, 4) + [{"id": "kaputt", "vector": None, "payload": {}}]
        got = candidates(unlabeled, _labeled("p1", "Sophie", 1))
        assert all(f["face_id"] != "kaputt" for b in got for f in b["faces"])


def test_nothing_is_assigned_automatically():
    """Der Rueckgabewert ist ein Vorschlag. Ein falsch zugeordnetes Gesicht
    verfaelscht danach jede Suche, und niemand wuerde es bemerken."""
    got = candidates(_unlabeled("u", 1, 5), _labeled("p1", "Sophie", 1))
    assert "person_id" in got[0] and "faces" in got[0]
    # Kein Feld, das eine erfolgte Zuordnung behauptet.
    assert not any(k in got[0] for k in ("assigned", "applied", "written"))


def test_pseudo_persons_produce_no_question():
    """„Ist das Übersprungen?" ist keine sinnvolle Rückfrage.

    In `_ignored` und `_skipped` liegen Ohren, Hinterköpfe und Unscharfes
    beieinander -- ihr Mittelvektor beschreibt keine Person, sondern nur
    „kein Gesicht".
    """
    labeled = ([{"id": f"s{i}", "vector": _vec(1, 0.05, seed=i),
                 "payload": {"person_id": "_skipped", "person_name": "Übersprungen"}}
                for i in range(4)]
               + [{"id": f"g{i}", "vector": _vec(1, 0.05, seed=50 + i),
                   "payload": {"person_id": "_ignored", "person_name": "Ignoriert"}}
                  for i in range(4)])
    assert candidates(_unlabeled("u", 1, 8), labeled) == []


def test_a_real_person_still_wins_next_to_a_pseudo_person():
    labeled = ([{"id": f"s{i}", "vector": _vec(1, 0.05, seed=i),
                 "payload": {"person_id": "_skipped", "person_name": "Übersprungen"}}
                for i in range(4)]
               + _labeled("p1", "Sophie", 1))
    got = candidates(_unlabeled("u", 1, 6), labeled)
    assert [b["name"] for b in got] == ["Sophie"]
