"""Profile und Ohren von echten Gesichtern trennen.

Der Detektor meldet Ohren und Hinterköpfe als Gesichter. Sie liefern ein
Embedding, taugen aber nicht zur Wiedererkennung — in der Labeling-Queue
kosten sie nur Zeit.
"""
from ingest.face_embedder import frontality

BOX = [0, 0, 100, 100]


def face(eye_l, eye_r, nose_x):
    """Fünf Landmarks: Augen links/rechts, Nase, zwei Mundwinkel."""
    return [[eye_l, 40], [eye_r, 40], [nose_x, 55], [eye_l + 5, 75], [eye_r - 5, 75]]


class TestFrontality:
    def test_frontal_face_scores_high(self):
        """Augen weit auseinander, Nase mittig."""
        assert frontality(face(30, 70, 50), BOX) > 0.9

    def test_profile_scores_low(self):
        """Augen dicht beieinander, Nase weit daneben — der Ohren-Fall."""
        assert frontality(face(46, 54, 70), BOX) < 0.2

    def test_three_quarter_view_is_in_between(self):
        score = frontality(face(35, 62, 58), BOX)
        assert 0.2 < score < 0.95

    def test_collapsed_eyes_score_zero(self):
        assert frontality(face(50, 50, 50), BOX) == 0.0

    def test_nose_far_outside_scores_zero(self):
        assert frontality(face(45, 55, 120), BOX) == 0.0

    def test_result_is_bounded(self):
        # Unrealistisch weit auseinander darf nicht über 1 hinausgehen.
        assert frontality(face(0, 100, 50), BOX) <= 1.0

    def test_missing_landmarks(self):
        assert frontality(None, BOX) is None
        assert frontality([], BOX) is None
        assert frontality([[1, 2]], BOX) is None

    def test_missing_box(self):
        assert frontality(face(30, 70, 50), None) is None
        assert frontality(face(30, 70, 50), [0, 0]) is None

    def test_garbage_does_not_raise(self):
        assert frontality("nonsense", BOX) is None
