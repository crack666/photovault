"""Die Ingest-Routen und die Merkmalsauskunft.

Zwei bislang ungetestete Router, die dieselbe Frage aus zwei Richtungen
beantworten: „laeuft hier gerade etwas" (`api/routes/ingest.py`) und „kann
diese Installation das ueberhaupt" (`api/routes/capabilities.py`).

Kein Test hier spricht mit Qdrant oder Ollama. Beide Zugaenge sind
vorsorglich verriegelt (Fixture `_kein_netz`): wer sein Doppel vergisst,
bekommt einen Testfehler statt einer Verbindung. Auf localhost:6333 liegt
der Produktivindex.
"""
from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from api import capabilities as cap
from api.main import app
from api.routes import ingest as ingest_route
from api.routes.capabilities import capabilities as capabilities_route


# --- Doppel ---------------------------------------------------------------

class _Count:
    def __init__(self, count: int):
        self.count = count


class _Q:
    """Minimal-Qdrant: zaehlt, was ihm mitgegeben wurde.

    `vectors` sagt, wie viele Punkte einen benannten Vektor haben; was nicht
    darin steht, gilt als vollstaendig. `fail` nennt die Zaehlungen, die eine
    Ausnahme werfen sollen -- `"total"` fuer die Gesamtzahl.
    """

    def __init__(self, total: int = 0, vectors: dict | None = None, fail: tuple = ()):
        self.total = total
        self.vectors = vectors or {}
        self.fail = fail
        self.gefragt: list[str] = []

    def count(self, collection_name=None, exact=False, count_filter=None):
        name = "total" if count_filter is None else count_filter.must[0].has_vector
        self.gefragt.append(name)
        if name in self.fail:
            raise RuntimeError(f"Qdrant antwortet nicht ({name})")
        if name == "total":
            return _Count(self.total)
        return _Count(self.vectors.get(name, self.total))


@pytest.fixture(autouse=True)
def _kein_netz(monkeypatch, tmp_path):
    """Riegel gegen echte Dienste, dazu zwei Zwischenspeicher zurueckgesetzt.

    `_cache` und `ATLAS_FILE` sind Modulzustand: ohne diesen Riegel entschiede
    ein frueher gelaufener Test oder eine im Arbeitsbaum liegende `atlas.json`
    mit, was hier herauskommt.
    """
    def _qdrant(*a, **kw):
        raise AssertionError("Test wollte ein echtes Qdrant -- Doppel vergessen?")

    def _ollama_aus(*a, **kw):
        raise AssertionError("Test wollte ein echtes Ollama -- Doppel vergessen?")

    monkeypatch.setattr(ingest_route, "client", _qdrant)
    monkeypatch.setattr(cap, "ollama_models", _ollama_aus)
    monkeypatch.setattr(cap, "_accelerator", lambda: {"onnxruntime_providers": [], "cuda": False})
    monkeypatch.setattr(cap, "_cache", (0.0, {}))
    monkeypatch.setattr(cap, "ATLAS_FILE", tmp_path / "atlas.json")


@pytest.fixture
def qdrant(monkeypatch):
    """Installiert ein Qdrant-Doppel fuer die Routen, die zaehlen."""
    def _install(**kw):
        q = _Q(**kw)
        monkeypatch.setattr(ingest_route, "client", lambda: q)
        return q
    return _install


@pytest.fixture
def jobs(monkeypatch):
    """Doppel fuer die Job-Liste. Was `list_jobs` liefert, steht in `liste`."""
    q = _Q()
    box: dict = {"liste": [], "aufrufe": [], "q": q}

    def fake_list_jobs(client_, limit=50):
        box["aufrufe"].append((client_, limit))
        return list(box["liste"])

    monkeypatch.setattr(ingest_route, "client", lambda: q)
    monkeypatch.setattr(ingest_route, "list_jobs", fake_list_jobs)
    return box


def _ollama(monkeypatch, modelle):
    """Ollama-Doppel. `modelle=None` heisst: der Dienst antwortet nicht."""
    box = {"aufrufe": 0}

    def tags():
        box["aufrufe"] += 1
        return None if modelle is None else set(modelle)

    monkeypatch.setattr(cap, "ollama_models", tags)
    return box


ALLE_MODELLE = (cap.EMBED_MODEL, cap.CAPTION_MODEL)


# --- POST /api/ingest/start ----------------------------------------------

class TestStartMeldetErfolgUndStartetNichts:
    """Die Route ist ein Stummel: sie tut nichts.

    Der ganze Rumpf ist `return {"status": "started", "source": req.source}`
    (api/routes/ingest.py:18-20). Kein Prozess, kein Job, kein Scan --
    „started" ist eine Behauptung ohne Deckung.

    Diese Tests halten den Zustand fest, wie er ist; sie billigen ihn nicht.
    Faellt einer um, weil jemand die Route mit Leben gefuellt hat, ist das der
    erwuenschte Moment: dann steht die offene Entscheidung an, ob die Route
    einen Lauf startet oder ehrlich absagt.
    """

    def test_antwortet_started_obwohl_nichts_startet(self):
        antwort = ingest_route.start_ingest(
            ingest_route.IngestStartRequest(source="/mnt/foto/Urlaub", batch_size=50)
        )
        assert antwort == {"status": "started", "source": "/mnt/foto/Urlaub"}

    def test_ruehrt_qdrant_nicht_an(self):
        """Ein echter Start muesste einen Job anlegen. Der Riegel `_kein_netz`
        laesst keinen Zugriff durch -- dass die Route trotzdem antwortet, ist
        der Beweis, dass sie nichts anfasst."""
        antwort = ingest_route.start_ingest(ingest_route.IngestStartRequest(source="/x"))
        assert antwort["status"] == "started"

    def test_danach_meldet_der_fortschritt_weiter_idle(self, jobs):
        """Der sichtbare Widerspruch: „started", und im selben Atemzug sagt
        die Fortschrittsroute, es laufe nichts."""
        ingest_route.start_ingest(ingest_route.IngestStartRequest(source="/mnt/foto"))
        stand = ingest_route.get_progress()
        assert stand["status"] == "idle"
        assert stand["phase"] == "idle"

    def test_batch_size_wird_entgegengenommen_und_verworfen(self):
        """Das Feld steht im Schema, wirkt aber nirgends -- auch das gehoert
        zur offenen Entscheidung."""
        gross = ingest_route.start_ingest(
            ingest_route.IngestStartRequest(source="/x", batch_size=5000))
        klein = ingest_route.start_ingest(
            ingest_route.IngestStartRequest(source="/x", batch_size=1))
        assert gross == klein == {"status": "started", "source": "/x"}

    def test_die_quelle_wird_nicht_geprueft(self):
        """Weder Existenz noch Form: ein Unsinnspfad wird genauso quittiert.
        Sobald die Route wirklich startet, gehoert hier eine Pruefung hin."""
        antwort = ingest_route.start_ingest(
            ingest_route.IngestStartRequest(source="gibt-es-nicht"))
        assert antwort == {"status": "started", "source": "gibt-es-nicht"}

    def test_ohne_quelle_lehnt_das_schema_ab(self):
        """Das Einzige, was an dieser Route tatsaechlich prueft."""
        with pytest.raises(ValidationError):
            ingest_route.IngestStartRequest()


# --- GET /api/ingest/progress --------------------------------------------

class TestFortschritt:
    def test_ohne_lauf_ist_alles_idle(self, jobs):
        stand = ingest_route.get_progress()
        assert stand["status"] == "idle"
        assert stand["phase"] == "idle"
        assert stand["percent"] == 0.0
        assert stand["total"] == stand["processed"] == stand["errors"] == 0

    def test_fremde_laeufe_zaehlen_nicht(self, jobs):
        """Ein Caption-Nachlauf benutzt dieselbe Job-Collection. Er darf hier
        nicht als Ingest durchgehen, sonst zeigt die Seite einen Fortschritt
        fuer etwas anderes an."""
        jobs["liste"] = [{"kind": "caption_pass", "status": "running", "percent": 42.0}]
        assert ingest_route.get_progress()["status"] == "idle"

    def test_job_ohne_art_zaehlt_nicht(self, jobs):
        jobs["liste"] = [{"status": "running", "percent": 42.0}]
        assert ingest_route.get_progress()["status"] == "idle"

    def test_unterarten_von_ingest_zaehlen_mit(self, jobs):
        jobs["liste"] = [{"kind": "ingest_faces", "status": "running", "percent": 12.0}]
        assert ingest_route.get_progress()["percent"] == 12.0

    def test_der_laufende_gewinnt_gegen_den_neueren_fertigen(self, jobs):
        """Die Liste kommt neueste-zuerst. Wer gerade laeuft, ist trotzdem das,
        was der Nutzer sehen will -- sonst zeigt die Seite einen abgeschlossenen
        Lauf, waehrend nebenan noch gearbeitet wird."""
        jobs["liste"] = [
            {"kind": "ingest", "status": "done", "job_id": "fertig", "percent": 100.0},
            {"kind": "ingest", "status": "running", "job_id": "laeuft", "percent": 30.0},
        ]
        stand = ingest_route.get_progress()
        assert stand["job_id"] == "laeuft"
        assert stand["percent"] == 30.0

    def test_ohne_laufenden_gilt_der_erste_der_liste(self, jobs):
        jobs["liste"] = [
            {"kind": "ingest", "status": "done", "job_id": "neu"},
            {"kind": "ingest", "status": "done", "job_id": "alt"},
        ]
        assert ingest_route.get_progress()["job_id"] == "neu"

    def test_zahlen_und_kennungen_werden_durchgereicht(self, jobs):
        jobs["liste"] = [{
            "kind": "ingest", "status": "running", "phase": "embed",
            "total": 900, "processed": 300, "skipped": 12, "errors": 3,
            "percent": 33.3, "rate_per_s": 2.5, "eta_s": 240,
            "job_id": "abc", "source": "/mnt/foto",
        }]
        assert ingest_route.get_progress() == {
            "total": 900, "processed": 300, "skipped": 12, "errors": 3,
            "phase": "embed", "percent": 33.3, "status": "running",
            "rate_per_s": 2.5, "eta_s": 240, "job_id": "abc", "source": "/mnt/foto",
        }

    def test_ein_lueckenhafter_job_sprengt_die_antwort_nicht(self, jobs):
        """Ein abgebrochener Lauf hat manche Felder nie geschrieben."""
        jobs["liste"] = [{"kind": "ingest"}]
        stand = ingest_route.get_progress()
        assert stand["total"] == 0
        assert stand["phase"] == "idle"
        assert stand["job_id"] is None

    def test_die_leere_antwort_laesst_vier_felder_weg(self, jobs):
        """Festgehalten, wie es ist: der Idle-Zweig liefert sieben Schluessel,
        der Job-Zweig elf. Wer `job_id` oder `eta_s` ungeprueft liest, sieht
        den Unterschied erst zur Laufzeit."""
        leer = set(ingest_route.get_progress())
        jobs["liste"] = [{"kind": "ingest", "status": "running"}]
        voll = set(ingest_route.get_progress())
        assert voll - leer == {"rate_per_s", "eta_s", "job_id", "source"}

    def test_es_werden_hoechstens_fuenfzig_jobs_geholt(self, jobs):
        ingest_route.get_progress()
        assert jobs["aufrufe"] == [(jobs["q"], 50)]


# --- GET /api/ingest/stats -----------------------------------------------

class TestStats:
    def test_zaehlt_die_fotos(self, qdrant):
        q = qdrant(total=14593)
        assert ingest_route.get_stats() == {"total_photos": 14593}
        assert q.gefragt == ["total"]

    def test_unerreichbares_qdrant_wird_zu_null(self, qdrant):
        """Festgehalten, wie es ist: ein Verbindungsfehler kommt als „0 Fotos"
        heraus und ist damit nicht von einem leeren Index zu unterscheiden.
        `/state` zwei Funktionen weiter meldet denselben Fehler dagegen mit."""
        qdrant(total=14593, fail=("total",))
        assert ingest_route.get_stats() == {"total_photos": 0}


# --- GET /api/ingest/state -----------------------------------------------

class TestIndexZustand:
    def test_ohne_luecken_wird_nichts_gemeldet(self, qdrant):
        qdrant(total=100)
        assert ingest_route.index_state() == {"total": 100, "gaps": []}

    def test_fehlende_textvektoren_kommen_mit_grund_und_abhilfe(self, qdrant):
        """Der Punkt der Route: die Zahl allein sagt niemandem etwas. Erst
        „findet sie nicht" plus „braucht Ollama" ist eine Auskunft."""
        qdrant(total=100, vectors={"text": 60})
        luecke = ingest_route.index_state()["gaps"][0]
        assert luecke["vector"] == "text"
        assert luecke["missing"] == 40
        assert "Freitextsuche" in luecke["means"]
        assert "Ollama" in luecke["remedy"]

    def test_wichtigstes_zuerst(self, qdrant):
        """Fehlender Text ist der haeufige Fall (kein Ollama), fehlendes CLIP
        deutet auf eine kaputte Datei. Die Reihenfolge ist die Antwort auf
        „was soll ich zuerst tun"."""
        qdrant(total=100, vectors={"text": 10, "clip": 90})
        assert [g["vector"] for g in ingest_route.index_state()["gaps"]] == ["text", "clip"]

    def test_vollstaendiger_vektor_taucht_nicht_auf(self, qdrant):
        qdrant(total=100, vectors={"text": 100, "clip": 40})
        assert [g["vector"] for g in ingest_route.index_state()["gaps"]] == ["clip"]

    def test_leerer_index_hat_keine_luecken(self, qdrant):
        qdrant(total=0)
        assert ingest_route.index_state() == {"total": 0, "gaps": []}

    def test_unerreichbares_qdrant_wird_gemeldet_statt_geworfen(self, qdrant):
        """Die Seite bekommt eine Antwort, keine 500 -- und der Grund steht
        drin. Das ist der Unterschied zu `/stats`, wo derselbe Fehler
        stillschweigend als „0 Fotos" herauskommt."""
        qdrant(total=100, fail=("total",))
        antwort = ingest_route.index_state()
        assert antwort["total"] == 0
        assert antwort["gaps"] == []
        assert "Qdrant antwortet nicht" in antwort["error"]

    def test_ein_kaputter_client_aufbau_geht_ungefiltert_durch(self):
        """Festgehalten, wie es ist: `client()` steht vor dem `try`
        (api/routes/ingest.py:81). Wirft schon der Aufbau -- eine unbrauchbare
        `QDRANT_URL` genuegt, `QdrantClient` prueft das Schema --, gibt es
        keine Auskunft, sondern eine 500. Hier wirft der Riegel aus
        `_kein_netz` an genau dieser Stelle."""
        with pytest.raises(AssertionError, match="Doppel vergessen"):
            ingest_route.index_state()

    def test_nicht_zaehlbarer_vektor_verschweigt_die_anderen_nicht(self, qdrant):
        """Festgehalten, wie es ist: die missratene Zaehlung wird
        uebersprungen (`continue`) und ist danach nicht von „keine Luecke" zu
        unterscheiden. Die uebrigen Vektoren kommen aber weiterhin durch."""
        qdrant(total=100, vectors={"clip": 30}, fail=("text",))
        antwort = ingest_route.index_state()
        assert [g["vector"] for g in antwort["gaps"]] == ["clip"]
        assert "error" not in antwort


# --- GET /api/capabilities ------------------------------------------------

class TestMerkmalsrechnung:
    """Welches Modell fehlt, welches Merkmal faellt dadurch weg."""

    def test_alles_da_alles_verfuegbar(self, monkeypatch):
        _ollama(monkeypatch, ALLE_MODELLE)
        merkmale = capabilities_route()["features"]
        for key in ("freetext", "captions", "reembed"):
            assert merkmale[key]["ok"] is True, key
            assert merkmale[key]["why"] == ""

    def test_verfuegbares_merkmal_traegt_keinen_verlust(self, monkeypatch):
        """`lost` nur, wenn tatsaechlich etwas fehlt -- sonst warnt die
        Oberflaeche vor einem Verlust, den es nicht gibt."""
        _ollama(monkeypatch, ALLE_MODELLE)
        assert capabilities_route()["features"]["freetext"]["lost"] == ""

    def test_fehlendes_embed_modell_nimmt_zwei_merkmale(self, monkeypatch):
        """Freitextsuche und Neu-Rechnen haengen am selben Modell."""
        _ollama(monkeypatch, (cap.CAPTION_MODEL,))
        merkmale = capabilities_route()["features"]
        assert merkmale["freetext"]["ok"] is False
        assert merkmale["reembed"]["ok"] is False
        assert merkmale["captions"]["ok"] is True

    def test_fehlendes_caption_modell_laesst_die_suche_in_ruhe(self, monkeypatch):
        """Die Merkmale muessen einzeln fallen. Eine Sammelantwort „Ollama
        unvollstaendig" wuerde die Suche grundlos sperren."""
        _ollama(monkeypatch, (cap.EMBED_MODEL,))
        merkmale = capabilities_route()["features"]
        assert merkmale["captions"]["ok"] is False
        assert merkmale["freetext"]["ok"] is True

    def test_der_grund_nennt_das_fehlende_modell_und_die_abhilfe(self, monkeypatch):
        _ollama(monkeypatch, (cap.CAPTION_MODEL,))
        why = capabilities_route()["features"]["freetext"]["why"]
        assert cap.EMBED_MODEL in why
        assert "ollama pull" in why

    def test_ausgefallenes_merkmal_sagt_label_grund_und_verlust(self, monkeypatch):
        """Die drei zusammen sind die Auskunft: wie es heisst, warum es fehlt,
        und was dem Nutzer dadurch entgeht."""
        _ollama(monkeypatch, ())
        for key, merkmal in capabilities_route()["features"].items():
            if merkmal["ok"]:
                continue
            assert merkmal["label"], key
            assert merkmal["why"], key
            assert merkmal["lost"], key

    def test_jedes_merkmal_hat_immer_dieselben_felder(self, monkeypatch):
        _ollama(monkeypatch, ALLE_MODELLE)
        for key, merkmal in capabilities_route()["features"].items():
            assert set(merkmal) == {"label", "ok", "why", "lost"}, key

    def test_die_karte_ist_eine_datei_kein_modell(self, monkeypatch, tmp_path):
        """`atlas_map` haengt an einer gerechneten Datei -- deshalb steht sie
        neben den anderen Merkmalen und nicht in FEATURES."""
        _ollama(monkeypatch, ALLE_MODELLE)
        assert capabilities_route()["features"]["atlas_map"]["ok"] is False

        datei = tmp_path / "gerechnet.json"
        datei.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(cap, "ATLAS_FILE", datei)
        monkeypatch.setattr(cap, "_cache", (0.0, {}))
        karte = capabilities_route()["features"]["atlas_map"]
        assert karte["ok"] is True
        assert karte["why"] == karte["lost"] == ""


class TestOllamaNichtErreichbar:
    """Kein Dienst da heisst: eine brauchbare Auskunft, keine Ausnahme."""

    def test_die_route_antwortet_statt_zu_werfen(self, monkeypatch):
        _ollama(monkeypatch, None)
        antwort = capabilities_route()
        assert antwort["ollama"]["reachable"] is False
        assert antwort["ollama"]["models"] == []
        assert antwort["ollama"]["url"]

    def test_der_grund_nennt_die_adresse(self, monkeypatch):
        """Der haeufigste Fall ist nicht „Ollama aus", sondern „falsche
        Adresse" -- deshalb steht sie im Text."""
        _ollama(monkeypatch, None)
        why = capabilities_route()["features"]["freetext"]["why"]
        assert "nicht erreichbar" in why
        assert cap.ollama_url() in why

    def test_jedes_modellmerkmal_faellt_weg_und_sagt_was_fehlt(self, monkeypatch):
        _ollama(monkeypatch, None)
        merkmale = capabilities_route()["features"]
        for key in ("freetext", "captions", "reembed"):
            assert merkmale[key]["ok"] is False, key
            assert merkmale[key]["lost"], key

    def test_paketmerkmale_bleiben_unberuehrt(self, monkeypatch):
        """`atlas_build` haengt an umap/sklearn, nicht an Ollama. Ein toter
        Dienst darf die Karte nicht mit sperren -- und ob die Pakete hier
        liegen, entscheidet die Maschine, nicht dieser Test."""
        _ollama(monkeypatch, ALLE_MODELLE)
        mit = capabilities_route()["features"]["atlas_build"]

        monkeypatch.setattr(cap, "_cache", (0.0, {}))
        _ollama(monkeypatch, None)
        ohne = capabilities_route()["features"]["atlas_build"]
        assert mit == ohne

    def test_ein_leeres_ollama_ist_etwas_anderes_als_ein_totes(self, monkeypatch):
        """Dienst laeuft, Modelle nicht gezogen: erreichbar, aber nichts geht.
        Die Abhilfe ist eine andere, also muss die Auskunft eine andere sein."""
        _ollama(monkeypatch, ())
        antwort = capabilities_route()
        assert antwort["ollama"]["reachable"] is True
        assert "Modell fehlt" in antwort["features"]["freetext"]["why"]


class TestZwischenspeicher:
    def test_die_zweite_frage_geht_nicht_erneut_ans_netz(self, monkeypatch):
        """Die Oberflaeche fragt beim Laden; ein HTTP-Rundlauf pro Seitenaufruf
        waere zu viel."""
        box = _ollama(monkeypatch, ALLE_MODELLE)
        erste = capabilities_route()
        zweite = capabilities_route()
        assert box["aufrufe"] == 1
        assert erste == zweite

    def test_nach_ablauf_wird_neu_nachgesehen(self, monkeypatch):
        """Ollama kann jederzeit starten -- eine dauerhaft gecachte Absage
        waere gelogen."""
        tot = _ollama(monkeypatch, None)
        assert capabilities_route()["ollama"]["reachable"] is False

        veraltet = time.time() - cap.TTL_SECONDS - 1
        monkeypatch.setattr(cap, "_cache", (veraltet, cap._cache[1]))
        _ollama(monkeypatch, ALLE_MODELLE)
        assert capabilities_route()["ollama"]["reachable"] is True
        assert tot["aufrufe"] == 1


# --- Verdrahtung ----------------------------------------------------------

def test_beide_router_haengen_am_api():
    """Eine Route, die niemand erreicht, faellt sonst nicht auf."""
    pfade = app.openapi()["paths"]
    assert "post" in (pfade.get("/api/ingest/start") or {})
    for pfad in ("/api/ingest/progress", "/api/ingest/stats", "/api/ingest/state",
                 "/api/capabilities"):
        assert "get" in (pfade.get(pfad) or {}), pfad
