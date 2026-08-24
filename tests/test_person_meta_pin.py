from api.person_meta import PINS, _clean_aliases


def test_clean_aliases_dedupes_case():
    assert _clean_aliases(["Karo", " karo ", "Lina", ""]) == ["Karo", "Lina"]


def test_pins_allowed():
    assert None in PINS
    assert "favorite" in PINS
    assert "muted" in PINS
    assert "hidden" not in PINS


def test_people_sort_favorites_first():
    rank = {"favorite": 0, None: 1, "muted": 2}
    people = [
        {"name": "Zach", "pin": None},
        {"name": "Mara", "pin": "muted"},
        {"name": "Bert", "pin": "favorite"},
        {"name": "Adam", "pin": "favorite"},
    ]
    out = sorted(people, key=lambda p: (rank.get(p.get("pin"), 1), p["name"].lower()))
    assert [p["name"] for p in out] == ["Adam", "Bert", "Zach", "Mara"]
