"""Einstellungen zur Laufzeit: Umgebung > Datei > Vorgabe.

Der eine Satz, der diese Installation schuetzt: eine gesetzte Umgebungs-
variable gewinnt immer. Sonst wuerde die erste Datei, die der Wizard
schreibt, hier die LiteLLM-Aliasse ueberschreiben.
"""
from __future__ import annotations

import json

import pytest

from ingest import settings


@pytest.fixture(autouse=True)
def _ohne_pool(monkeypatch):
    for name in ("LITELLM_URL", "PHOTOVAULT_EMBED_URL", "LITELLM_MASTER_KEY",
                 "PHOTOVAULT_CAPTION_MODEL", "PHOTOVAULT_EMBED_MODEL", "OLLAMA_URL",
                 "PHOTOVAULT_TEXT_DIM"):
        monkeypatch.delenv(name, raising=False)


class TestVorrang:
    def test_ohne_datei_gelten_die_vorgaben(self):
        assert settings.load() == settings.DEFAULTS
        assert settings.llm_mode() == "ollama"
        assert settings.caption_model() == ""          # nichts gewaehlt, nichts erfunden
        assert settings.embed_model() == "qwen3-embedding:4b"
        assert settings.text_vector_size() == 2560

    def test_datei_ueberlagert_die_vorgaben(self):
        settings.save({"llm": {"caption_model": "gemma4:12b", "embed_dim": 1024}})
        assert settings.caption_model() == "gemma4:12b"
        assert settings.text_vector_size() == 1024
        assert settings.load()["llm"]["embed_model"] == "qwen3-embedding:4b"   # Rest bleibt

    def test_umgebung_schlaegt_die_datei(self, monkeypatch):
        """Diese Maschine: LiteLLM-Aliasse aus der Umgebung, und eine Datei
        daneben aendert daran nichts."""
        settings.save({"llm": {"mode": "openai", "url": "https://api.example.com",
                               "key": "fremder-schluessel", "caption_model": "gpt-irgendwas"}})
        monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")
        monkeypatch.setenv("LITELLM_MASTER_KEY", "pool-key")
        assert settings.pool_url() == "http://127.0.0.1:4000"
        assert settings.pool_key() == "pool-key"
        assert settings.caption_model() == "local"
        assert settings.embed_model() == "embedder"
        assert settings.llm_mode() == "openai"

    def test_pool_in_der_umgebung_spricht_aliasse(self, monkeypatch):
        """Ohne ausdrueckliches Modell heisst der Pool `local`/`embedder` --
        Ollama-Tags kennt er nicht."""
        settings.save({"llm": {"caption_model": "gemma4:12b"}})
        monkeypatch.setenv("LITELLM_URL", "http://127.0.0.1:4000")
        assert settings.caption_model() == "local"
        monkeypatch.setenv("PHOTOVAULT_CAPTION_MODEL", "fast")
        assert settings.caption_model() == "fast"

    def test_modus_aus_schaltet_beide_modelle_ab(self):
        settings.save({"llm": {"mode": "off", "caption_model": "gemma4:12b"}})
        assert settings.caption_model() == ""
        assert settings.embed_model() == ""

    def test_cloud_modus_liefert_adresse_und_schluessel(self):
        settings.save({"llm": {"mode": "openai", "url": "https://api.example.com/v1/",
                               "key": "schluessel-1234abcd", "caption_model": "vision-x"}})
        assert settings.pool_url() == "https://api.example.com/v1"
        assert settings.pool_key() == "schluessel-1234abcd"
        assert settings.caption_model() == "vision-x"

    def test_ollama_adresse_aus_der_datei_nur_im_ollama_modus(self):
        settings.save({"llm": {"mode": "ollama", "url": "http://nas:11434/"}})
        assert settings.ollama_base() == "http://nas:11434"
        settings.save({"llm": {"mode": "openai", "url": "https://api.example.com"}})
        assert settings.ollama_base() == "http://127.0.0.1:11434"


class TestDatei:
    def test_schreiben_ist_atomar_und_liest_sich_zurueck(self):
        path = settings.settings_path()
        settings.save({"setup": {"done": True}})
        assert path.is_file() and not path.with_suffix(".json.tmp").exists()
        assert json.loads(path.read_text(encoding="utf-8"))["setup"]["done"] is True

    def test_kaputte_datei_ist_wie_keine(self, caplog):
        settings.settings_path().parent.mkdir(parents=True, exist_ok=True)
        settings.settings_path().write_text("{nicht json", encoding="utf-8")
        assert settings.load()["llm"]["mode"] == "ollama"
        assert "nicht lesbar" in caplog.text

    def test_aenderung_auf_der_platte_kommt_an(self):
        """Der Caption-Lauf ist ein eigener Prozess; was der Wizard schreibt,
        muss er beim naechsten Blick sehen -- kein Neustart."""
        settings.load()
        path = settings.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"llm": {"caption_model": "neu:1b"}}), encoding="utf-8")
        settings.reset_cache()   # mtime-Aufloesung: nicht auf die Uhr verlassen
        assert settings.caption_model() == "neu:1b"

    def test_schluessel_wird_nach_aussen_maskiert(self):
        settings.save({"llm": {"mode": "openai", "url": "https://x", "key": "geheim-1234"}})
        out = settings.redacted()
        assert out["llm"]["key"].endswith("1234") and "geheim" not in out["llm"]["key"]
        assert out["llm"]["has_key"] is True
        assert settings.load()["llm"]["key"] == "geheim-1234"   # das Original bleibt
