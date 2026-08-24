"""Freitext-Namen zu person_ids auflösen.

In der Suche tippt man „Jonas, Piet", gespeichert ist „jonas-meyer".

Alle Namen hier sind erfunden. Das ist Absicht: Tests wandern in ein
öffentliches Repository, echte Personen haben dort nichts verloren.
"""
from api.people_index import resolve

PEOPLE = [
    {"id": "jonas-meyer", "name": "Jonas Meyer"},
    {"id": "piet-fischer", "name": "Piet Fischer"},
    # Zwei Personen mit demselben Vornamen -- genau dafuer gibt es die
    # Mehrdeutigkeitsregel weiter unten.
    {"id": "sven-richter", "name": "Sven Richter"},
    {"id": "sven-kortum", "name": "Sven Kortum"},
    {"id": "marco-weber", "name": "Marco Weber"},
]


class TestResolve:
    def test_first_name_alone(self):
        assert resolve("Jonas", PEOPLE) == ["jonas-meyer"]

    def test_last_name_alone(self):
        assert resolve("Fischer", PEOPLE) == ["piet-fischer"]

    def test_full_name(self):
        assert resolve("Jonas Meyer", PEOPLE) == ["jonas-meyer"]

    def test_id_is_accepted(self):
        assert resolve("jonas-meyer", PEOPLE) == ["jonas-meyer"]

    def test_case_insensitive(self):
        assert resolve("JONAS", PEOPLE) == ["jonas-meyer"]
        assert resolve("jonas meyer", PEOPLE) == ["jonas-meyer"]

    def test_ambiguous_first_name_returns_all(self):
        """Zwei Svens: beide zurückgeben, statt still einen zu raten."""
        assert sorted(resolve("Sven", PEOPLE)) == ["sven-kortum", "sven-richter"]

    def test_full_name_disambiguates(self):
        assert resolve("Sven Richter", PEOPLE) == ["sven-richter"]
        assert resolve("Sven Kortum", PEOPLE) == ["sven-kortum"]

    def test_unknown_name(self):
        assert resolve("Zaphod", PEOPLE) == []

    def test_empty_token(self):
        assert resolve("", PEOPLE) == []
        assert resolve("   ", PEOPLE) == []

    def test_prefix_matches_word_start_only(self):
        """'Mey' trifft 'Meyer'; 'eyer' soll nicht mitten im Wort greifen."""
        assert resolve("Mey", PEOPLE) == ["jonas-meyer"]

    def test_exact_name_wins_over_prefix(self):
        people = PEOPLE + [{"id": "max-mustermann", "name": "Max Mustermann"}]
        assert resolve("Piet Fischer", people) == ["piet-fischer"]
        assert resolve("Max Mustermann", people) == ["max-mustermann"]

    def test_no_people_labeled_yet(self):
        assert resolve("Jonas", []) == []
