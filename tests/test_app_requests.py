"""Die App als Ganzes -- ueber echte HTTP-Anfragen.

Die Luecke, die der Plan unter "was noch offen ist" nennt: `TestClient` kam
im Repo nicht vor. Die Routentests decken je eine Funktion mit Doubles ab,
aber nichts pruefte den Weg *durch* die App -- Router-Prefix, Validierung,
Serialisierung, Fehlerabbildung, statische Auslieferung. Genau dort sassen
diese Session zwei Fehler, die von Hand mit `curl` gefunden wurden:

* Die Kostenschaetzung auf der Jobs-Seite rechnete nach der Umstellung des
  Cache-Schluessels im alten Schluessel und meldete 29.174 fehlende
  Kacheln, wo 118 fehlten.
* Die gelbe Zustandszeile nannte keine Dateien, und die Route dafuer gab es
  noch nicht.

Beide waren ueber eine Anfrage sichtbar, ueber keinen Test.

Qdrant wird ausgetauscht, nicht angesprochen: gepatcht wird
`api.qdrant_util._client_for`, und weil `client()` diesen Namen erst beim
Aufruf aus seinem Modul holt, wirkt das in *allen* Routen -- auch in denen,
die `client` direkt importiert haben. Ein Test, der den echten Index
braucht, ist kein Test, sondern eine Wette auf den Datenbestand.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


class FakeQdrant:
    """So viel Qdrant, wie die Routen hier anfassen -- und nicht mehr."""

    def __init__(self, punkte=None, faces=None, anzahl=None):
        self.punkte = list(punkte or [])
        self.faces = list(faces or [])
        self._anzahl = anzahl
        self.geschrieben = []

    class _Count:
        def __init__(self, n):
            self.count = n

    def count(self, collection_name=None, **kw):
        if self._anzahl is not None:
            return self._Count(self._anzahl)
        return self._Count(len(self.punkte))

    def scroll(self, collection_name=None, limit=256, offset=None, **kw):
        if collection_name == "faces":
            return list(self.faces), None
        return list(self.punkte), None

    def retrieve(self, collection_name=None, ids=None, **kw):
        gesucht = set(str(i) for i in (ids or []))
        return [p for p in self.punkte if str(p.id) in gesucht]

    def set_payload(self, collection_name=None, payload=None, points=None, **kw):
        self.geschrieben.append(("set_payload", payload, points))

    def delete(self, **kw):
        self.geschrieben.append(("delete", kw))

    def get_collections(self):
        class _R:
            collections = []

        return _R()


class _Point:
    def __init__(self, pid, payload=None, vector=None):
        self.id = pid
        self.payload = payload or {}
        self.vector = vector or {}


@pytest.fixture
def fake(monkeypatch):
    """Ein Qdrant-Double, das jede Route erreicht."""
    q = FakeQdrant()
    monkeypatch.setattr("api.qdrant_util._client_for", lambda url: q)
    return q


@pytest.fixture
def client(fake):
    from api.main import app

    with TestClient(app) as c:
        yield c


class TestVerdrahtung:
    """Haengt jeder Router dort, wo die Oberflaeche ihn sucht."""

    #: Was `web/static/core/api.js` anspricht. Ein Prefix, das sich
    #: verschiebt, faellt sonst erst im Browser auf.
    PREFIXE = ("/api/search", "/api/sources", "/api/persons", "/api/faces",
               "/api/photos", "/api/jobs", "/api/events", "/api/albums",
               "/api/ingest", "/api/capabilities", "/api/trash", "/api/atlas")

    def test_alle_prefixe_sind_montiert(self, client):
        pfade = client.get("/openapi.json").json()["paths"]
        for prefix in self.PREFIXE:
            assert any(p.startswith(prefix) for p in pfade), f"{prefix} fehlt"

    def test_health_braucht_kein_qdrant(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "photovault"}


class TestStatischeAuslieferung:
    """Der `no-cache`-Header ist jetzt der einzige Schutz.

    Die 80 handgepflegten `?v=`-Stempel sind raus -- sie standen auf zwei
    verschiedenen Werten und deckten den Modul-Graphen ohnehin nicht ab.
    Damit haengt alles an diesem Header, und was allein traegt, gehoert in
    einen Test: faellt er weg, laedt ein Browser beliebig lange die alte
    Fassung, waehrend die Datei auf der Platte richtig ist.
    """

    def test_startseite_wird_nicht_gecacht(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_jobs_seite_wird_nicht_gecacht(self, client):
        r = client.get("/jobs.html")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_module_werden_nicht_gecacht(self, client):
        r = client.get("/static/core/dom.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"

    def test_nachfrage_kostet_keine_uebertragung(self, client):
        """`no-cache` heisst "vorher nachfragen", nicht "nicht speichern".

        Ohne die 304-Antwort waere der Header teuer statt billig -- jeder
        Seitenaufruf uebertruege den ganzen Modul-Graphen neu.
        """
        erst = client.get("/static/core/dom.js")
        wieder = client.get("/static/core/dom.js",
                            headers={"If-None-Match": erst.headers["etag"]})
        assert wieder.status_code == 304

    def test_keine_versionsstempel_mehr_im_ausgelieferten_html(self, client):
        text = client.get("/").text
        assert "?v=" not in text


class TestIngestZustand:
    def test_zustand_meldet_gesamtzahl(self, client, fake):
        fake._anzahl = 14591
        r = client.get("/api/ingest/state")
        assert r.status_code == 200
        assert r.json()["total"] == 14591

    def test_unbekannter_vektor_wird_abgewiesen(self, client):
        """Und zwar mit den bekannten Namen in der Meldung.

        Ein 404 ohne Auskunft darueber, was gueltig waere, verschiebt das
        Raten nur nach vorn.
        """
        r = client.get("/api/ingest/gaps/gibtsnicht")
        assert r.status_code == 404
        assert "clip" in r.json()["detail"]

    def test_luecke_nennt_die_dateien(self, client, fake):
        fake.punkte = [_Point("a", {"file_path": "/mnt/photo/x/a.jpg",
                                    "file_size": 3_100_000,
                                    "file_warning": "truncated",
                                    "folder_name": "x", "date": "2009-12-17"})]
        r = client.get("/api/ingest/gaps/clip")
        assert r.status_code == 200
        daten = r.json()
        assert daten["vector"] == "clip"
        assert daten["photos"][0]["file_path"] == "/mnt/photo/x/a.jpg"
        assert daten["photos"][0]["warning"] == "truncated"


class TestPapierkorb:
    def test_leere_kennungsliste_ist_ein_fehler(self, client):
        r = client.post("/api/trash", json={"photo_ids": []})
        assert r.status_code == 400

    def test_ohne_confirm_wird_nichts_geloescht(self, client, fake):
        """Die Schranke muss ueber die ganze Anfrage halten, nicht nur in
        der Funktion."""
        fake.punkte = [_Point("a", {"file_path": "/mnt/photo/x/a.jpg",
                                    "trashed_at": "2026-09-04T10:00:00+00:00"})]
        r = client.post("/api/trash/empty", json={"photo_ids": ["a"], "confirm": False})
        assert r.status_code == 200
        assert r.json()["deleted"] == 0
        assert not any(art == "delete" for art, *_ in fake.geschrieben)

    def test_kennungen_umgehen_den_papierkorb_nicht(self, client, fake):
        """Ein Foto, das *nicht* vorgemerkt ist, darf so nicht verschwinden.

        Das war einmal eine echte Luecke: explizite Kennungen liessen den
        Papierkorb vollstaendig ueberspringen.
        """
        fake.punkte = [_Point("a", {"file_path": "/mnt/photo/x/a.jpg"})]
        r = client.post("/api/trash/empty", json={"photo_ids": ["a"], "confirm": True})
        assert r.status_code == 200
        assert r.json()["deleted"] == 0


class TestQuellen:
    def test_quellen_werden_gelesen(self, client):
        r = client.get("/api/sources")
        assert r.status_code == 200
        daten = r.json()
        assert "entries" in daten and "file" in daten

    def test_durchsehen_verlaesst_die_bibliothek_nicht(self, client, monkeypatch):
        """Der Ordnerwaehler darf kein Dateibrowser fuer die Maschine sein.

        Er war einer: `browse` nahm jeden absoluten Pfad, und die
        Brotkrumen boten `/` ausdruecklich an. Ueber CORS ist die
        Schnittstelle fuer `localhost:3000` mit erreichbar.
        """
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "/mnt/photo")
        r = client.get("/api/sources/browse", params={"path": "/etc"})
        assert r.status_code == 403
        assert "/mnt/photo" in r.json()["detail"]

    def test_aehnlicher_praefix_zaehlt_nicht_als_drinnen(self, client, monkeypatch):
        """`/mnt/photo-alt` faengt mit `/mnt/photo` an und gehoert nicht dazu."""
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "/mnt/photo")
        r = client.get("/api/sources/browse", params={"path": "/mnt/photo-alt"})
        assert r.status_code == 403

    def test_punkt_punkt_fuehrt_nicht_hinaus(self, client, monkeypatch):
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "/mnt/photo")
        r = client.get("/api/sources/browse", params={"path": "/mnt/photo/../etc"})
        assert r.status_code == 403

    def test_quelle_ausserhalb_wird_nicht_eingetragen(self, client, monkeypatch):
        """Sonst liest der naechste Lauf die ganze Platte ein."""
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "/mnt/photo")
        r = client.post("/api/sources/add", json={"path": "/", "exclude": False})
        assert r.status_code == 403

    def test_ohne_parameter_faengt_er_an_der_wurzel_an(self, client, monkeypatch):
        """Die Voreinstellung war `/` -- und lief damit in die eigene
        Schranke.

        Der Fehler fiel erst am laufenden Server auf: `?path=` (leer) ging,
        der Aufruf *ohne* Parameter gab 403, weil `/` nicht in der
        Bibliothek liegt. "Nicht angegeben" und "leer angegeben" muessen
        dasselbe heissen.
        """
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "/mnt/photo")
        r = client.get("/api/sources/browse")
        assert r.status_code == 200
        assert r.json()["path"] == "/mnt/photo"
        assert r.json()["parent"] is None      # kein Weg nach oben an der Wurzel

    def test_ohne_bestimmbare_wurzel_bleibt_der_waehler_benutzbar(self, client, monkeypatch, tmp_path):
        """Fail-closed waere hier der Einrichtungstod.

        Ohne Quellen gibt es keine Wurzel -- und ohne Waehler kaeme man nie
        zur ersten Quelle. Anders als beim Loeschen, das bei unbestimmbarer
        Wurzel verweigert.
        """
        monkeypatch.setattr("ingest.spaces.photo_root", lambda: "")
        r = client.get("/api/sources/browse", params={"path": str(tmp_path)})
        assert r.status_code == 200


class TestKarte:
    def test_ohne_gerechnete_karte_ein_klares_nein(self, client, monkeypatch, tmp_path):
        """Kein leeres Ergebnis: "nichts ist weg" waere hier eine Luege."""
        monkeypatch.setattr("api.routes.atlas.ATLAS_FILE", tmp_path / "fehlt.json")
        r = client.get("/api/atlas/gone")
        assert r.status_code == 404

    def test_abgleich_meldet_geloeschte(self, client, fake, monkeypatch, tmp_path):
        karte = tmp_path / "atlas.json"
        karte.write_text(json.dumps({"built_at": "2026-08-26T14:02:25+00:00",
                                     "ids": ["a", "b"]}), encoding="utf-8")
        monkeypatch.setattr("api.routes.atlas.ATLAS_FILE", karte)
        monkeypatch.setattr("api.routes.atlas._cache", {"value": None, "at": 0.0})
        # Nur "a" liegt noch im Index; "b" ist geloescht.
        fake.punkte = [_Point("a", {"file_path": "/mnt/photo/x/a.jpg"})]
        r = client.get("/api/atlas/gone")
        assert r.status_code == 200
        daten = r.json()
        assert daten["checked"] == 2
        assert daten["deleted"] == ["b"]


class TestKachelKosten:
    def test_kosten_kommen_aus_dem_vorhandenen_cache(self, client, fake, monkeypatch, tmp_path):
        """Die Zahl, die auf der Jobs-Seite steht, bevor man den Knopf
        drueckt.

        Sie stand einmal bei 29.174 fehlenden Kacheln, weil hier im
        Pfad-Schluessel gerechnet wurde, waehrend der Cache schon am
        Inhalts-Hash hing. Ein leerer Cache muss 'alles fehlt' melden --
        und nicht ins Blaue schaetzen.
        """
        monkeypatch.setattr("api.thumbs.CACHE_DIR", tmp_path)
        fake.punkte = [_Point("a", {"file_path": "/mnt/photo/x/a.jpg",
                                    "content_sha256": "abc"})]
        r = client.get("/api/jobs/cost/thumbs")
        assert r.status_code == 200
        daten = r.json()
        assert daten["photos"] == 1
        assert daten["missing"] == len(daten["sizes"])
        assert daten["bytes_missing"] == 0     # nichts da, woraus zu schaetzen waere
