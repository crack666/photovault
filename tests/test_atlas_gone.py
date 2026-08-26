"""Abgleich der Karte mit dem Index.

`atlas.json` ist ein Standbild. Wer danach löscht, sieht das Foto weiter
darauf stehen -- angeklickt kommt ein 404, und das Vorschaubild zeigt es
sogar noch, weil es im Browser liegt. Gemeldet als „selbst nach hartem
Neuladen tauchen die gelöschten Bilder wieder auf".

Die drei Fälle müssen auseinandergehalten werden: da, weg, vorgemerkt. Alle
drei führen dazu, dass ein Punkt verschwindet, aber sie bedeuten
Verschiedenes -- und die Kopfzeile sagt welches.
"""
from api.routes.atlas import BATCH, check


class _P:
    def __init__(self, pid, payload=None):
        self.id = pid
        self.payload = payload or {}


class _Q:
    """Minimal-Qdrant: kennt nur, was in `da` steht."""

    def __init__(self, da):
        self.da = da
        self.batches = []

    def retrieve(self, collection_name, ids, **kw):
        self.batches.append(list(ids))
        return [_P(i, self.da[i]) for i in ids if i in self.da]


class TestCheck:
    def test_alles_da(self):
        q = _Q({"a": {}, "b": {}})
        assert check(q, ["a", "b"]) == ([], [])

    def test_geloescht_wird_gemeldet(self):
        q = _Q({"a": {}})
        assert check(q, ["a", "b"]) == (["b"], [])

    def test_vorgemerkt_wird_getrennt_gemeldet(self):
        q = _Q({"a": {}, "b": {"trashed_at": "2026-08-26T10:00:00Z"}})
        assert check(q, ["a", "b"]) == ([], ["b"])

    def test_beides_zugleich(self):
        q = _Q({"a": {}, "c": {"trashed_at": "x"}})
        weg, korb = check(q, ["a", "b", "c", "d"])
        assert weg == ["b", "d"]
        assert korb == ["c"]

    def test_leerer_vermerk_gilt_nicht_als_vorgemerkt(self):
        # `trashed_at: None` steht im Payload, sobald einmal gerettet wurde.
        q = _Q({"a": {"trashed_at": None}})
        assert check(q, ["a"]) == ([], [])

    def test_reihenfolge_bleibt_die_der_karte(self):
        q = _Q({"b": {}})
        weg, _ = check(q, ["z", "b", "a"])
        assert weg == ["z", "a"]

    def test_wird_in_haeppchen_gefragt(self):
        # 17.000 Kennungen in einer Abfrage waeren eine Wand aus JSON.
        ids = [f"p{i}" for i in range(BATCH * 2 + 5)]
        q = _Q({i: {} for i in ids})
        check(q, ids)
        assert len(q.batches) == 3
        assert [len(b) for b in q.batches] == [BATCH, BATCH, 5]

    def test_leere_karte(self):
        q = _Q({})
        assert check(q, []) == ([], [])
        assert q.batches == []
