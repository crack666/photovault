import numpy as np

from ingest.face_cluster import cluster_faces, person_id_from_name


def _vec(*coords, dim=16):
    v = np.zeros(dim, dtype=np.float32)
    for i, c in coords:
        v[i] = c
    return (v / np.linalg.norm(v)).tolist()


def _item(fid, vector, score=0.9, box=(0, 0, 100, 100)):
    return {"id": fid, "vector": vector, "payload": {"score": score, "box": list(box)}}


def test_person_id_umlauts():
    assert person_id_from_name("Jürgen Maß") == "juergen-mass"


def test_cluster_same_face():
    v = _vec((0, 1), (1, 1))
    clusters = cluster_faces([_item("a", v), _item("b", v)], threshold=0.4)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 2


def test_cluster_splits_orthogonal():
    clusters = cluster_faces(
        [_item("a", _vec((0, 1))), _item("b", _vec((1, 1)))], threshold=0.4
    )
    assert len(clusters) == 2


def test_two_groups_stay_apart_and_merge_internally():
    a = [_item(f"a{i}", _vec((0, 1.0), (2, 0.05 * i))) for i in range(4)]
    b = [_item(f"b{i}", _vec((1, 1.0), (3, 0.05 * i))) for i in range(4)]
    clusters = cluster_faces(a + b, threshold=0.5)
    assert len(clusters) == 2
    assert sorted(len(c["members"]) for c in clusters) == [4, 4]


def test_result_is_independent_of_input_order():
    """Der greedy Vorgaenger lieferte je nach Reihenfolge andere Cluster."""
    items = [_item(f"f{i}", _vec((i % 3, 1.0), (8 + i % 3, 0.03 * i))) for i in range(12)]
    forward = cluster_faces(items, threshold=0.5)
    backward = cluster_faces(list(reversed(items)), threshold=0.5)
    key = lambda cs: sorted(tuple(sorted(m["id"] for m in c["members"])) for c in cs)  # noqa: E731
    assert key(forward) == key(backward)


def test_clusters_of_one_person_are_joined():
    """Kernschwaeche des greedy Verfahrens: zwei Cluster derselben Person
    wurden nie verglichen und blieben darum fuer immer getrennt."""
    same = [_item(f"p{i}", _vec((0, 1.0), (5, 0.02 * i))) for i in range(6)]
    clusters = cluster_faces(same, threshold=0.5)
    assert len(clusters) == 1
    assert len(clusters[0]["members"]) == 6


def test_weak_detections_are_dropped():
    strong = _item("strong", _vec((0, 1)), score=0.9)
    weak = _item("weak", _vec((0, 1)), score=0.2)
    clusters = cluster_faces([strong, weak], threshold=0.4, min_score=0.55)
    assert [m["id"] for c in clusters for m in c["members"]] == ["strong"]


def test_tiny_faces_are_dropped():
    big = _item("big", _vec((0, 1)), box=(0, 0, 100, 100))
    tiny = _item("tiny", _vec((0, 1)), box=(0, 0, 18, 18))
    clusters = cluster_faces([big, tiny], threshold=0.4, min_px=40)
    assert [m["id"] for c in clusters for m in c["members"]] == ["big"]


def test_largest_cluster_first():
    items = [_item(f"a{i}", _vec((0, 1.0), (5, 0.01 * i))) for i in range(5)]
    items += [_item("b0", _vec((1, 1)))]
    clusters = cluster_faces(items, threshold=0.5)
    assert len(clusters[0]["members"]) == 5


def test_empty_and_vectorless_input():
    assert cluster_faces([]) == []
    assert cluster_faces([{"id": "x", "vector": None, "payload": {}}]) == []


def test_centroid_is_unit_length():
    items = [_item(f"a{i}", _vec((0, 1.0), (4, 0.05 * i))) for i in range(3)]
    clusters = cluster_faces(items, threshold=0.5)
    assert abs(float(np.linalg.norm(clusters[0]["centroid"])) - 1.0) < 1e-5


class TestScale:
    """Das Zeilenmaximum darf am Ergebnis nichts aendern, nur an der Laufzeit.

    Frueher suchte jeder Merge das Maximum ueber die ganze Matrix -- O(n^3),
    weshalb nur 6000 von 21655 Gesichtern geclustert wurden. Dieselbe Person
    zerfiel dadurch in Gruppen diesseits und jenseits der Grenze.
    """

    def _blobs(self, groups=12, per=9, dim=32, seed=7):
        import numpy as np
        rng = np.random.default_rng(seed)
        items, k = [], 0
        for g in range(groups):
            base = rng.normal(size=dim)
            base /= np.linalg.norm(base)
            for _ in range(per):
                v = base + rng.normal(scale=0.12, size=dim)
                items.append({"id": f"f{k}", "vector": (v / np.linalg.norm(v)).tolist(),
                              "payload": {"score": 0.9, "box": [0, 0, 200, 200]}})
                k += 1
        return items, groups

    def test_separable_groups_are_found(self):
        from ingest.face_cluster import cluster_faces
        items, groups = self._blobs()
        got = cluster_faces(items, threshold=0.5)
        assert len(got) == groups
        assert sorted(len(c["members"]) for c in got) == [9] * groups

    def test_order_does_not_matter(self):
        from ingest.face_cluster import cluster_faces
        items, _ = self._blobs()
        a = cluster_faces(items, threshold=0.5)
        b = cluster_faces(list(reversed(items)), threshold=0.5)
        key = lambda cl: sorted(sorted(m["id"] for m in c["members"]) for c in cl)  # noqa: E731
        assert key(a) == key(b)

    def test_a_few_thousand_faces_finish_quickly(self):
        """Nicht als Benchmark, sondern als Riegel gegen einen Rueckfall auf O(n^3)."""
        import time
        from ingest.face_cluster import cluster_faces
        items, groups = self._blobs(groups=60, per=50)
        t0 = time.time()
        got = cluster_faces(items, threshold=0.5)
        elapsed = time.time() - t0
        assert len(got) == groups
        assert elapsed < 20, f"{len(items)} Gesichter brauchten {elapsed:.0f}s"
