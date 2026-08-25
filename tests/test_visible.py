"""Papierkorb-Fotos aus Ansichten heraushalten.

Der Papierkorb war zunächst nur ein Vermerk für die Karte -- in Suche, Serien,
Alben und Personengalerien tauchten die Fotos weiter auf. Das war die offene
Frage: „von *wo* ausgeschlossen?“. Die Antwort steht jetzt an einer Stelle:
Ansichten überspringen den Papierkorb, Indexpflege nicht.

Der heikle Teil ist die Verknüpfung. Die Suche baut je nach Einstellung einen
`should`-Filter („eines der Kriterien genügt“). Hängt man die
Papierkorb-Bedingung dort als weiteres `should` an, wird sie zur *Alternative*
-- und der Papierkorb kommt wieder mit zurück.
"""
from qdrant_client.models import (
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
)

from api.qdrant_util import TRASH_KEY, not_trashed, visible


def _leaf(value):
    return FieldCondition(key="folder_name", match=MatchValue(value=value))


class TestNotTrashed:
    def test_prueft_den_vermerk(self):
        cond = not_trashed()
        assert isinstance(cond, IsEmptyCondition)
        assert cond.is_empty.key == TRASH_KEY == "trashed_at"


class TestVisible:
    def test_ohne_filter_nur_der_papierkorb(self):
        f = visible()
        assert f.must == [not_trashed()]
        assert not f.should
        assert not f.must_not

    def test_none_ist_wie_ohne(self):
        assert visible(None).must == visible().must

    def test_should_bleibt_eine_ebene_tiefer(self):
        # Der Kern: eine Oder-Verknüpfung darf nicht aufgebrochen werden.
        inner = Filter(should=[_leaf("Urlaub"), _leaf("Weihnachten")])
        f = visible(inner)
        assert len(f.must) == 2
        assert f.must[0] is inner
        assert f.must[1] == not_trashed()
        # Die Alternativen stehen weiterhin *nur* innen.
        assert f.should is None
        assert len(f.must[0].should) == 2

    def test_must_wird_nicht_flachgeklopft(self):
        inner = Filter(must=[_leaf("Urlaub")])
        f = visible(inner)
        assert f.must == [inner, not_trashed()]

    def test_zweimal_angewandt_bleibt_korrekt(self):
        # Doppelt gefiltert ist nicht falsch, nur redundant -- es darf aber
        # nichts kaputt gehen, wenn eine Route das aus Versehen tut.
        f = visible(visible(Filter(must=[_leaf("Urlaub")])))
        assert f.must[1] == not_trashed()
        assert f.must[0].must[1] == not_trashed()
