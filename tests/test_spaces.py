"""Der Bereich eines Fotos -- eine Regel, zwei Verwender.

Die Karte leitet den Bereich beim Bauen aus dem Pfad ab, die Suche braucht ihn
als Payload-Feld. Damit das nie auseinanderläuft, rechnet beides dieselbe
Funktion. Die Fallen stecken in den Rändern: der zeichenweise Präfix, die
Datei direkt in der Wurzel, der einzelne Pfad.
"""
from ingest.spaces import UNKNOWN, assign, common_root, space_of


class TestCommonRoot:
    def test_gemeinsames_elternverzeichnis(self):
        assert common_root(["/mnt/photo/Handys/a.jpg", "/mnt/photo/Fotos/b.jpg"]) == "/mnt/photo"

    def test_zeichenweiser_praefix_wird_zurueckgeschnitten(self):
        # commonprefix liefert hier "/mnt/photo/F" -- das ist kein Verzeichnis.
        assert common_root(["/mnt/photo/Fotos/a.jpg", "/mnt/photo/Fun/b.jpg"]) == "/mnt/photo"

    def test_ein_einzelner_pfad(self):
        assert common_root(["/mnt/photo/Fotos/a.jpg"]) == "/mnt/photo/Fotos"

    def test_leer(self):
        assert common_root([]) == ""
        assert common_root([None, ""]) == ""


class TestSpaceOf:
    def test_erste_ebene_unter_der_wurzel(self):
        assert space_of("/mnt/photo/Handys/Handy A/x.jpg", "/mnt/photo") == "Handys"
        assert space_of("/mnt/photo/Fotos/Abi 08/x.jpg", "/mnt/photo") == "Fotos"

    def test_datei_direkt_in_der_wurzel_hat_keinen_bereich(self):
        # Sonst wäre jeder Dateiname ein Bereich.
        assert space_of("/mnt/photo/lose.jpg", "/mnt/photo") == UNKNOWN

    def test_pfad_ausserhalb_der_wurzel(self):
        assert space_of("/anderswo/Bilder/x.jpg", "/mnt/photo") == "anderswo"

    def test_leerer_pfad(self):
        assert space_of("", "/mnt/photo") == UNKNOWN

    def test_ohne_wurzel(self):
        assert space_of("/mnt/photo/Handys/x.jpg", "") == "mnt"


class TestAssign:
    def test_reihenfolge_des_auftretens(self):
        root, names, idx = assign([
            "/mnt/photo/Handys/a.jpg",
            "/mnt/photo/Fotos/b.jpg",
            "/mnt/photo/Handys/c.jpg",
        ])
        assert root == "/mnt/photo"
        assert names == ["Handys", "Fotos"]
        assert idx == [0, 1, 0]

    def test_jeder_pfad_bekommt_einen_index(self):
        _, names, idx = assign(["/mnt/photo/A/x.jpg", "", "/mnt/photo/B/y.jpg"])
        assert len(idx) == 3
        assert names[idx[1]] == UNKNOWN

    def test_leere_eingabe_liefert_einen_namen(self):
        # Die Karte erwartet mindestens einen Bereich, sonst hat sie keine
        # Legende und keinen Umschalter.
        root, names, idx = assign([])
        assert (root, names, idx) == ("", [UNKNOWN], [])
