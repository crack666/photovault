"""Der Bereich als Geltungsbereich, nicht als Kriterium.

Der Unterschied ist nicht kosmetisch. Die Kriterienliste kann auf „eines
genügt“ stehen (`match="any"`). Landet der Bereich dort als weiterer Eintrag,
wird er zur *Alternative* -- „zeigt Person X ODER liegt in Fotos“ -- und die
Suche liefert mehr statt weniger. Deshalb steht er eine Ebene darüber.

Und: ein Geltungsbereich, der stillschweigend einschränkt, ist eine Falle.
`scope_text` liefert den Satz dazu, den die Oberfläche anzeigt.
"""
from qdrant_client.models import FieldCondition, Filter, MatchValue

from api.routes.search import scope_text, space_scope


class TestSpaceScope:
    def test_ohne_bereich_keine_bedingung(self):
        assert space_scope([]) is None

    def test_ein_bereich_ist_eine_blanke_bedingung(self):
        # Kein Filter drumherum -- eine Ebene weniger zu verschachteln.
        cond = space_scope(["Fotos"])
        assert isinstance(cond, FieldCondition)
        assert cond.key == "space"
        assert cond.match.value == "Fotos"

    def test_mehrere_bereiche_sind_ein_oder(self):
        # Ein Foto liegt an genau einem Ort; als UND wäre das immer leer.
        cond = space_scope(["Fotos", "Sonstiges"])
        assert isinstance(cond, Filter)
        assert [c.match.value for c in cond.should] == ["Fotos", "Sonstiges"]
        assert cond.must is None


class TestScopeText:
    def test_ohne_bereich_kein_satz(self):
        assert scope_text([]) == ""

    def test_einer(self):
        assert scope_text(["Fotos"]) == "nur im Bereich Fotos"

    def test_mehrere(self):
        assert scope_text(["Fotos", "Handys"]) == "nur in den Bereichen Fotos, Handys"


class TestNichtAlsAlternative:
    """Die Falle: `match="any"` plus Bereich.

    Nachgebaut wird hier, was die Route tut -- der Bereich darf nie im
    `should` der Kriterien landen.
    """

    def _kriterien(self):
        return [FieldCondition(key="person_ids", match=MatchValue(value="zelda"))]

    def test_oder_verknuepfung_bleibt_innen(self):
        inner = Filter(should=self._kriterien())
        scope = space_scope(["Fotos"])
        gebaut = Filter(must=[inner, scope])
        # Der Bereich ist eine Und-Bedingung auf der oberen Ebene ...
        assert gebaut.must[1] is scope
        # ... und die Alternativen stehen weiterhin nur innen.
        assert gebaut.should is None
        assert len(gebaut.must[0].should) == 1

    def test_ohne_kriterien_nur_der_bereich(self):
        scope = space_scope(["Fotos"])
        gebaut = Filter(must=[scope])
        assert gebaut.must == [scope]
