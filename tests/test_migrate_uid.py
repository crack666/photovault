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


class TestInhaltsHash:
    """Stufe 2: der Inhalts-Hash.

    Er loest zwei Dinge, die der Pfad-Hash schlecht konnte: gleiche Bytes
    heissen gleiche Vorschaukachel (kein Verwaisen beim Verschieben), und
    eine von aussen verschobene Datei laesst sich wiedererkennen.

    Der Preis ist gering, weil der Ingest die Bytes ohnehin liest -- gemessen
    0,9 ms je Foto nach dem Bildladen gegen 35,8 ms kalt. Deshalb *nach* dem
    Laden hashen.
    """

    def test_gleiche_bytes_gleicher_hash(self, tmp_path):
        from ingest.identity import content_hash

        a = tmp_path / "a.jpg"
        b = tmp_path / "tief" / "b.jpg"
        b.parent.mkdir()
        a.write_bytes(b"dieselben bytes")
        b.write_bytes(b"dieselben bytes")
        # Der Punkt der ganzen Uebung: verschiedene Pfade, ein Hash.
        assert content_hash(str(a)) == content_hash(str(b))

    def test_andere_bytes_anderer_hash(self, tmp_path):
        from ingest.identity import content_hash

        a = tmp_path / "a.jpg"
        b = tmp_path / "b.jpg"
        a.write_bytes(b"eins")
        b.write_bytes(b"zwei")
        assert content_hash(str(a)) != content_hash(str(b))

    def test_unlesbar_gibt_none_statt_ausnahme(self, tmp_path):
        from ingest.identity import content_hash

        # Ein unlesbares Foto darf den Lauf nicht kosten -- ohne Hash faellt
        # es nur auf den Pfad-Schluessel zurueck.
        assert content_hash(str(tmp_path / "gibtsnicht.jpg")) is None

    def test_leere_datei_hat_einen_hash(self, tmp_path):
        from ingest.identity import content_hash

        f = tmp_path / "leer.jpg"
        f.write_bytes(b"")
        assert content_hash(str(f)) == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_ueber_blockgrenzen_hinweg(self, tmp_path):
        import hashlib

        from ingest.identity import content_hash

        f = tmp_path / "gross.jpg"
        daten = bytes(range(256)) * 9000      # ~2,3 MB, mehr als ein Block
        f.write_bytes(daten)
        assert content_hash(str(f), chunk=4096) == hashlib.sha256(daten).hexdigest()

    def test_record_traegt_das_feld(self):
        from ingest.pipeline import PhotoRecord

        r = PhotoRecord(photo_id="x", file_path="/a.jpg")
        assert r.content_sha256 is None
