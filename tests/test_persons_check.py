"""Die Pruefung aus photovault#57: jede Person eines Fotos braucht ein Gesicht."""
from __future__ import annotations

from tools.persons_check import faces_by_photo, find_unbelegt


def _photo(uid, person_ids, **extra):
    return (f"pt-{uid}", {"photo_uid": uid, "photo_id": uid, "person_ids": person_ids, **extra})


def test_name_without_face_is_found():
    """Der Fall aus #57: Name auf dem Foto, das Gesicht dort unbenannt."""
    persons_on = faces_by_photo([{"photo_id": "a", "person_id": None}])
    found = find_unbelegt([_photo("a", ["zelda"], person_suggestions=["zelda"])], persons_on)
    assert [r.point_id for r in found] == ["pt-a"]
    assert found[0].missing == ["zelda"]
    assert found[0].suggested == 1


def test_video_frame_face_counts_as_proof():
    """Ein Clip zeigt die Person oft nur auf einem Frame -- das reicht."""
    persons_on = faces_by_photo([{"photo_id": "v", "person_id": "zelda", "frame_ss": 1.5}])
    assert find_unbelegt([_photo("v", ["zelda"])], persons_on) == []


def test_pseudo_ids_are_ignored_on_both_sides():
    """`_skipped` ist keine Person: weder Anspruch noch Beleg."""
    persons_on = faces_by_photo([{"photo_id": "a", "person_id": "_skipped"}])
    assert find_unbelegt([_photo("a", ["_skipped"])], persons_on) == []
    found = find_unbelegt([_photo("b", ["zelda"])],
                          faces_by_photo([{"photo_id": "b", "person_id": "_skipped"}]))
    assert len(found) == 1


def test_older_photo_id_still_links_faces():
    """Gesichter, die noch an der alten `photo_id` haengen, belegen trotzdem."""
    persons_on = faces_by_photo([{"photo_id": "old", "person_id": "zelda"}])
    photo = ("pt", {"photo_uid": "new", "photo_id": "old", "person_ids": ["zelda"]})
    assert find_unbelegt([photo], persons_on) == []


def test_face_of_another_photo_is_no_proof():
    persons_on = faces_by_photo([{"photo_id": "other", "person_id": "zelda"}])
    assert len(find_unbelegt([_photo("a", ["zelda"])], persons_on)) == 1
