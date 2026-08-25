"""Gesichter vollständig durchblättern -- oder gar nicht.

Hier lag ein stiller Datenverlust: `list_persons` las 5.000 Gesichter, im
Bestand lagen 14.212. Ein `limit`, das kleiner ist als der Bestand, liefert
keine Auswahl, sondern ein falsches Ergebnis -- eine Person mit 17 Fotos
existierte in der Liste einfach nicht, und damit auch nicht im Personen-Reiter
und nicht in der Namensauswahl. Gefunden beim Zuordnen aus einem Foto heraus:
die neu angelegte Person stand nicht in ihrer eigenen Liste.
"""
from api.routes.persons import _scroll_faces


class _P:
    def __init__(self, pid, payload=None, vector=None):
        self.id = pid
        self.payload = payload or {}
        self.vector = vector


class _Q:
    """Blättert `total` Punkte in Häppchen aus, wie Qdrant es tut."""

    def __init__(self, total):
        self.total = total
        self.calls = []

    def scroll(self, collection_name, scroll_filter=None, limit=None, offset=None, **kw):
        start = offset or 0
        end = min(start + limit, self.total)
        self.calls.append({"limit": limit, "offset": start, "vectors": kw.get("with_vectors"),
                           "payload": kw.get("with_payload")})
        batch = [_P(i, {"person_id": f"p{i}"}) for i in range(start, end)]
        return batch, (end if end < self.total else None)


class TestScrollFaces:
    def test_ohne_limit_kommt_alles(self):
        q = _Q(14212)
        assert len(_scroll_faces(q, None, limit=None, with_vectors=False)) == 14212

    def test_limit_schneidet_ab(self):
        # Das ist erlaubt -- aber nur, wo eine Stichprobe gemeint ist.
        q = _Q(14212)
        assert len(_scroll_faces(q, None, limit=5000)) == 5000

    def test_weniger_im_bestand_als_erlaubt(self):
        q = _Q(37)
        assert len(_scroll_faces(q, None, limit=5000)) == 37

    def test_leerer_bestand(self):
        assert _scroll_faces(_Q(0), None, limit=None, with_vectors=False) == []

    def test_ohne_vektoren_groessere_haeppchen(self):
        # 512 Floats je Gesicht sind der Kostenfaktor. Fallen sie weg, sind
        # 56 Rundreisen für 14.000 Punkte Verschwendung.
        q = _Q(5000)
        _scroll_faces(q, None, limit=None, with_vectors=False)
        assert all(c["vectors"] is False for c in q.calls)
        assert q.calls[0]["limit"] == 1024

    def test_mit_vektoren_kleine_haeppchen(self):
        q = _Q(5000)
        _scroll_faces(q, None, limit=None, with_vectors=True)
        assert q.calls[0]["limit"] == 256

    def test_payload_wird_durchgereicht(self):
        q = _Q(10)
        _scroll_faces(q, None, limit=None, with_vectors=False, payload=["person_id"])
        assert q.calls[0]["payload"] == ["person_id"]

    def test_kein_ueberholen_am_ende(self):
        # Letztes Häppchen genau auf der Grenze: kein zusätzlicher Aufruf.
        q = _Q(2048)
        _scroll_faces(q, None, limit=None, with_vectors=False)
        assert len(q.calls) == 2
