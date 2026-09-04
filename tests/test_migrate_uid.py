"""Stufe 1: `photo_uid` nachtragen, ohne einen Wert zu bewegen.

Die Identität eines Fotos hing am Dateipfad -- und daran hingen der
Qdrant-Punkt, der Fremdschlüssel der Gesichter und der Cache-Schlüssel. Ein
Rename ausserhalb von PhotoVault erzeugte deshalb ein neues Foto und liess
Namen und Gesichter am alten hängen.

Dieser Lauf friert die bestehende Kennung ein, statt eine neue zu erfinden.
Genau darum geht es in diesen Tests: er darf **nichts** verändern, was schon
eine Kennung hat. Ein Lauf, der eine eingefrorene Kennung bewegt, wäre
schlimmer als gar keiner -- dann zeigen die Gesichter ins Leere und der
Cache ist wertlos.
"""
from tools.migrate_uid import plan


def zeilen(*tripel):
    return list(tripel)


class TestPlan:
    def test_nachtragen_wenn_uid_fehlt(self):
        p = plan(zeilen(("pt1", "abc", "")))
        assert p["todo"] == [("pt1", "abc")]
        assert p["ok"] == 0

    def test_wertgleich_gilt_als_fertig(self):
        p = plan(zeilen(("pt1", "abc", "abc")))
        assert p["todo"] == [] and p["ok"] == 1

    def test_abweichende_uid_wird_nicht_angetastet(self):
        # Das ist der wichtigste Test: eine schon eingefrorene Kennung zu
        # ueberschreiben hiesse, die Identitaet eines Fotos zu bewegen.
        p = plan(zeilen(("pt1", "abc", "xyz")))
        assert p["todo"] == []
        assert p["diverging"] == [("pt1", "abc", "xyz")]

    def test_ohne_photo_id_wird_uebersprungen(self):
        # Nichts einzufrieren -- und eine Kennung zu erfinden waere schlimmer:
        # die Gesichter zeigen dann auf etwas, das es nie gab.
        p = plan(zeilen(("pt1", "", "")))
        assert p["todo"] == [] and p["without_id"] == ["pt1"]

    def test_gemischt(self):
        p = plan(zeilen(
            ("a", "1", ""),      # nachtragen
            ("b", "2", "2"),     # fertig
            ("c", "3", "9"),     # abweichend
            ("d", "", ""),       # ohne
        ))
        assert [t[0] for t in p["todo"]] == ["a"]
        assert p["ok"] == 1
        assert [t[0] for t in p["diverging"]] == ["c"]
        assert p["without_id"] == ["d"]

    def test_leerer_bestand(self):
        p = plan([])
        assert p == {"todo": [], "ok": 0, "diverging": [], "without_id": []}

    def test_zweiter_lauf_hat_nichts_zu_tun(self):
        # Nachgemessen am Bestand: 14.593 nachgetragen, danach 0.
        erst = plan(zeilen(("a", "1", ""), ("b", "2", "")))
        danach = plan(zeilen(("a", "1", "1"), ("b", "2", "2")))
        assert len(erst["todo"]) == 2
        assert danach["todo"] == [] and danach["ok"] == 2


class TestIdentitaet:
    def test_uid_of_bevorzugt_das_neue_feld(self):
        from ingest.identity import uid_of

        assert uid_of({"photo_uid": "neu", "photo_id": "alt"}) == "neu"

    def test_uid_of_faellt_auf_das_alte_zurueck(self):
        from ingest.identity import uid_of

        # Waehrend der Umstellung tragen Punkte beides; danach faellt
        # photo_id weg, und diese Stelle bleibt die einzige, die das weiss.
        assert uid_of({"photo_id": "alt"}) == "alt"

    def test_uid_of_ohne_beides(self):
        from ingest.identity import uid_of

        assert uid_of({}) == ""

    def test_punkt_id_folgt_der_kennung(self):
        from ingest.identity import point_id_for, point_id_for_path, photo_uid_for

        assert point_id_for_path("/a/b.jpg") == point_id_for(photo_uid_for("/a/b.jpg"))
