"""Der Setup-Wizard als Server: Empfehlung, Modellwahl, Stoppuhr, Erstlauf.

Was hier geprueft wird, sind die Stellen, an denen ein Fremder sonst allein
waere: dass die Empfehlung aus dem Speicher kommt, dass der Schluessel nie
zurueckkommt, dass die Test-Caption eine Hochrechnung liefert -- und dass
eine bestehende Installation nach dem Update *nicht* auf der
Einrichtungsseite landet.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from api.routes import setup as su
from ingest import settings


@pytest.fixture(autouse=True)
def _ohne_umgebung(monkeypatch):
    for name in ("LITELLM_URL", "PHOTOVAULT_EMBED_URL", "LITELLM_MASTER_KEY",
                 "PHOTOVAULT_CAPTION_MODEL", "PHOTOVAULT_EMBED_MODEL", "OLLAMA_URL",
                 "GPU_VRAM_MB"):
        monkeypatch.delenv(name, raising=False)


class TestEmpfehlung:
    def test_bänder_aus_gemessenem_speicher(self):
        """Die 5060 Ti gibt es mit 8 und 16 GB -- nur die Zahl trennt sie."""
        assert su.recommend(32_607)["caption_model"] == "qwen3.8:27b"
        assert su.recommend(16_000)["caption_model"] == "gemma4:12b"
        assert su.recommend(8_000)["caption_model"] == "gemma4:e4b-it-qat"
        assert su.recommend(8_000)["embed_model"] == "qwen3-embedding:0.6b"
        assert su.recommend(0)["cpu"] is True and su.recommend(16_000)["cpu"] is False

    def test_jede_stufe_passt_in_ihr_band(self):
        """Caption- und Embedding-Modell muessen zusammen unter die Untergrenze
        des Bands passen -- sonst empfiehlt der Wizard etwas, das nicht laedt."""
        for row in su.RECOMMENDATIONS:
            if row["min_mb"]:
                assert (row["caption_gb"] + row["embed_gb"]) * 1000 < row["min_mb"], row

    def test_vram_kommt_aus_der_umgebung(self, monkeypatch):
        assert su.vram_mb() == 0
        monkeypatch.setenv("GPU_VRAM_MB", "16311")
        assert su.vram_mb() == 16311
        monkeypatch.setenv("GPU_VRAM_MB", "kaputt")
        assert su.vram_mb() == 0


class TestErstlauf:
    def test_ohne_quellen_und_nicht_fertig_braucht_es_den_wizard(self, monkeypatch):
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "Keine sources.txt")
        assert su.needs_setup() is True

    def test_bestehende_installation_mit_quellen_wird_nicht_umgeleitet(self, monkeypatch):
        """Der Wizard lief hier nie -- und trotzdem ist das eingerichtet."""
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "")
        assert su.needs_setup() is False

    def test_fertig_gemeldet_heisst_fertig(self, monkeypatch):
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "Keine sources.txt")
        su.finish()
        assert su.needs_setup() is False

    def test_startseite_leitet_nur_im_erstlauf_um(self, monkeypatch, tmp_path):
        from api import main as app_main

        (tmp_path / "index.html").write_text("<p>ui</p>", encoding="utf-8")
        (tmp_path / "setup.html").write_text("<p>setup</p>", encoding="utf-8")
        monkeypatch.setattr(app_main, "WEB_DIR", tmp_path)
        monkeypatch.setattr(su, "needs_setup", lambda: True)
        assert app_main.ui_index().headers["location"] == "/setup"
        monkeypatch.setattr(su, "needs_setup", lambda: False)
        assert "location" not in app_main.ui_index().headers


class TestModellwahl:
    def test_speichern_gibt_den_schluessel_nie_zurueck(self, monkeypatch):
        monkeypatch.setattr(su.cap, "forget", lambda: None)
        out = su.save_llm(su.LlmRequest(mode="openai", url="https://api.example.com/v1/",
                                        key="TESTSCHLUESSEL-9876", caption_model="vision-x"))
        assert out["llm"]["has_key"] is True
        assert "geheim" not in json.dumps(out)
        assert settings.pool_key() == "TESTSCHLUESSEL-9876"
        assert settings.pool_url() == "https://api.example.com/v1"

    def test_schluessel_bleibt_wenn_er_nicht_mitgeschickt_wird(self, monkeypatch):
        monkeypatch.setattr(su.cap, "forget", lambda: None)
        su.save_llm(su.LlmRequest(mode="openai", url="https://x", key="schluessel-eins"))
        su.save_llm(su.LlmRequest(mode="openai", url="https://x", caption_model="m"))
        assert settings.pool_key() == "schluessel-eins"

    def test_cloud_ohne_adresse_wird_abgewiesen(self):
        with pytest.raises(HTTPException) as err:
            su.save_llm(su.LlmRequest(mode="openai", url=""))
        assert err.value.status_code == 400

    def test_unbekannter_modus_wird_abgewiesen(self):
        with pytest.raises(HTTPException):
            su.save_llm(su.LlmRequest(mode="magie"))

    def test_zustand_nennt_was_die_umgebung_festnagelt(self, monkeypatch):
        """Der Wizard darf nicht anbieten, was er nicht aendern kann."""
        monkeypatch.setattr(su.cap, "llm_models", lambda: {"local", "embedder"})
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "")
        monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")
        st = su.state()
        assert st["llm"]["effective"]["from_env"]["pool"] is True
        assert st["llm"]["effective"]["caption_model"] == "local"
        assert st["llm"]["caption_ready"] is True
        assert st["needs_setup"] is False

    def test_zustand_ohne_modell_ist_nicht_bereit(self, monkeypatch):
        monkeypatch.setattr(su.cap, "llm_models", lambda: {"gemma4:12b"})
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "keine")
        st = su.state()
        assert st["llm"]["caption_ready"] is False
        assert st["gpu"]["recommendation"]["cpu"] is True


class TestZiehen:
    def test_fortschritt_wird_zeilenweise_durchgereicht(self, monkeypatch):
        import io

        class Antwort(io.BytesIO):
            def close(self):
                self.closed_by_us = True
                super().close()

        body = (b'{"status":"pulling manifest"}\n'
                b'{"status":"pulling abc","total":100,"completed":40}\n'
                b'{"status":"success"}')
        monkeypatch.setattr(su.urllib.request, "urlopen", lambda req, timeout=0: Antwort(body))
        monkeypatch.setattr(su.cap, "forget", lambda: None)
        resp = su.pull_model(su.PullRequest(model="gemma4:12b"))

        async def sammeln():
            return b"".join([c async for c in resp.body_iterator])

        lines = asyncio.run(sammeln()).decode().splitlines()
        assert [json.loads(l)["status"] for l in lines] == ["pulling manifest", "pulling abc", "success"]

    def test_ziehen_nur_mit_ollama(self, monkeypatch):
        settings.save({"llm": {"mode": "openai", "url": "https://x"}})
        with pytest.raises(HTTPException) as err:
            su.pull_model(su.PullRequest(model="egal"))
        assert err.value.status_code == 409

    def test_unerreichbares_ollama_ist_502_mit_adresse(self, monkeypatch):
        def kaputt(req, timeout=0):
            raise OSError("connection refused")

        monkeypatch.setattr(su.urllib.request, "urlopen", kaputt)
        with pytest.raises(HTTPException) as err:
            su.pull_model(su.PullRequest(model="gemma4:12b"))
        assert err.value.status_code == 502 and "11434" in err.value.detail


class TestStoppuhr:
    def test_test_caption_liefert_dauer_und_hochrechnung(self, monkeypatch, tmp_path):
        foto = tmp_path / "a.jpg"
        foto.write_bytes(b"\xff\xd8" + b"x" * 64)
        settings.save({"llm": {"caption_model": "gemma4:e2b-it-qat"}})
        monkeypatch.setattr("api.routes.sources._in_der_bibliothek", lambda p: p)

        class Langsam:
            def __init__(self, model=None, **kw):
                self.model = model

            def caption_structured(self, path, ctx=None, **kw):
                return {"caption_de": "Ein Garten.", "scene_tags": ["garten"]}

        monkeypatch.setattr("ingest.captioner.Captioner", Langsam)
        ticks = iter([100.0, 138.0])
        monkeypatch.setattr(su.time, "monotonic", lambda: next(ticks))
        out = su.test_caption(su.TestRequest(path=str(foto), photos=4800))
        assert out["seconds"] == 38.0
        assert out["estimate"] == {"photos": 4800, "hours": 50.7}
        assert out["model"] == "gemma4:e2b-it-qat" and out["caption_de"] == "Ein Garten."

    def test_ohne_modell_keine_stoppuhr(self):
        with pytest.raises(HTTPException) as err:
            su.test_caption(su.TestRequest(path="/x"))
        assert err.value.status_code == 409

    def test_embedding_probe_merkt_sich_die_dimension(self, monkeypatch):
        class Emb:
            def __init__(self, model=None, **kw):
                pass

            def raw_batch(self, texts):
                return [[0.1] * 1024]

        monkeypatch.setattr("ingest.text_embedder.TextEmbedder", Emb)
        monkeypatch.setattr(su, "_collection_text_dim", lambda: None)
        out = su.probe_embedding(su.EmbedProbeRequest())
        assert out["dim"] == 1024 and out["stored"] is True
        assert settings.text_vector_size() == 1024

    def test_embedding_probe_ueberschreibt_keine_bestehende_collection(self, monkeypatch):
        class Emb:
            def __init__(self, model=None, **kw):
                pass

            def raw_batch(self, texts):
                return [[0.1] * 1024]

        monkeypatch.setattr("ingest.text_embedder.TextEmbedder", Emb)
        monkeypatch.setattr(su, "_collection_text_dim", lambda: 2560)
        out = su.probe_embedding(su.EmbedProbeRequest())
        assert out["stored"] is False and "2560" in out["note"]
        assert settings.text_vector_size() == 2560


class TestOrdnerwaehlerImVerbund:
    """Zwei Fehler aus dem ersten Browserlauf des Wizards.

    Ohne `sources.txt` starben Baum und Trockenlauf mit 500 -- der Wizard ist
    aber genau der Moment, in dem es die Datei noch nicht gibt. Und nach der
    ersten Quelle auf `D:` verweigerte die Bibliothekswurzel (gemeinsames
    Elternverzeichnis) jede zweite auf `C:` -- im Verbund muss der Waehler
    ueberall auf den Laufwerken hin duerfen."""

    @pytest.fixture
    def laufwerke(self, tmp_path, monkeypatch):
        from api.routes import sources as sr

        host = tmp_path / "host"
        for rel in ("d/Fotos/Urlaub", "d/Fotos/Screenshots", "c/Users/x/Bilder"):
            (host / rel).mkdir(parents=True)
        (host / "d/Fotos/Urlaub/a.jpg").write_bytes(b"\xff\xd8" + b"x" * 64)
        monkeypatch.setenv("PHOTOVAULT_BROWSE_ROOT", str(host))
        monkeypatch.setenv("PHOTOVAULT_SOURCES", str(tmp_path / "sources.txt"))
        monkeypatch.setattr(sr, "FILE", str(tmp_path / "sources.txt"))
        monkeypatch.setattr(sr, "client", lambda: None)
        monkeypatch.setattr(sr, "_count_in_index", lambda q, p: 0)
        return host

    def test_baum_und_trockenlauf_gehen_ohne_datei(self, laufwerke):
        from api.routes import sources as sr

        d = sr.browse("")
        assert d["path"] == str(laufwerke) and {x["name"] for x in d["dirs"]} == {"c", "d"}
        assert sr.preview()["total"] == 0

    def test_erste_quelle_legt_die_datei_an(self, laufwerke, tmp_path):
        from api.routes import sources as sr

        sr.add_source(sr.AddRequest(path=str(laufwerke / "d/Fotos")))
        text = (tmp_path / "sources.txt").read_text(encoding="utf-8")
        assert str(laufwerke / "d/Fotos") in text and text.startswith("#")

    def test_zweites_laufwerk_bleibt_erlaubt(self, laufwerke):
        from api.routes import sources as sr

        sr.add_source(sr.AddRequest(path=str(laufwerke / "d/Fotos")))
        sr.add_source(sr.AddRequest(path=str(laufwerke / "d/Fotos/Screenshots"), exclude=True))
        sr.add_source(sr.AddRequest(path=str(laufwerke / "c/Users/x/Bilder")))
        aktiv = [e["path"] for e in sr.list_sources()["entries"] if e["enabled"]]
        assert len(aktiv) == 3
        assert sr.browse("")["root"] == str(laufwerke)     # die Krumen fangen bei den Laufwerken an

    def test_ausserhalb_der_laufwerke_bleibt_zu(self, laufwerke, tmp_path):
        from api.routes import sources as sr

        with pytest.raises(HTTPException) as err:
            sr.browse(str(tmp_path))
        assert err.value.status_code == 403
