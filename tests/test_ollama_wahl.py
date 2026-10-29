"""Ollama direkt als ausdrueckliche Wahl (photovault#26), LiteLLM als Vorgabe (#19).

Kein echtes Ollama, kein LiteLLM: ``post_json`` wird durch ein Doppel ersetzt,
das nur die Adressen und Nutzlasten mitschreibt.
"""
import pytest

import ingest.captioner as cap
import ingest.ollama_client as oc
from ingest.text_embedder import TextEmbedder

VEC = [0.1] * oc.TEXT_VECTOR_SIZE


@pytest.fixture
def calls(monkeypatch):
    """Ein Doppel fuer jeden HTTP-Weg; antwortet passend zu LiteLLM oder Ollama."""
    seen = []

    def fake_post(url, payload, timeout=180, headers=None):
        seen.append((url, payload))
        if url.endswith("/v1/chat/completions"):
            return {"choices": [{"message": {"content": '{"caption_de": "x", "scene_tags": []}'}}]}
        if url.endswith("/v1/embeddings"):
            return {"data": [{"index": 0, "embedding": VEC}]}
        if url.endswith("/api/chat"):
            return {"message": {"content": '{"caption_de": "x", "scene_tags": []}'}}
        if url.endswith("/api/embed"):
            return {"embeddings": [VEC]}
        return {}

    for target in ("ingest.ollama_client.post_json", "ingest.captioner.post_json",
                   "ingest.text_embedder.post_json"):
        monkeypatch.setattr(target, fake_post)
    return seen


def _all_paths(monkeypatch):
    from ingest.pipeline import IngestConfig, IngestPipeline

    assert cap.Captioner().caption_structured("/nix.jpg", {}, image_b64="AAAA") is not None
    assert TextEmbedder().embed("ok") == VEC
    IngestPipeline(IngestConfig(source="/x"))._warm_caption_model()


def test_ohne_schalter_geht_nichts_zu_ollama(monkeypatch, calls):
    """Der alte stille Rueckfall: OLLAMA_URL gesetzt, LITELLM_URL leer. Das darf
    nicht mehr zu Ollama fuehren -- auf crackdesk ginge es sonst am GPU-Tor vorbei."""
    monkeypatch.delenv("PHOTOVAULT_LLM", raising=False)
    monkeypatch.delenv("LITELLM_URL", raising=False)
    monkeypatch.delenv("PHOTOVAULT_EMBED_URL", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test:11434")

    assert oc.llm_mode() == "litellm"
    _all_paths(monkeypatch)
    urls = [u for u, _ in calls]
    assert urls and all(u.startswith("http://127.0.0.1:4000/v1/") for u in urls), urls


def test_mit_schalter_laufen_captions_textvektoren_und_aufwaermen_gegen_ollama(monkeypatch, calls):
    monkeypatch.setenv("PHOTOVAULT_LLM", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test:11434/")
    monkeypatch.setenv("LITELLM_URL", "http://litellm.test:4000")

    _all_paths(monkeypatch)
    urls = [u for u, _ in calls]
    assert urls == [
        "http://ollama.test:11434/api/chat",
        "http://ollama.test:11434/api/embed",
        "http://ollama.test:11434/api/generate",
        "http://ollama.test:11434/api/embed",
    ]
    chat = calls[0][1]
    assert chat["think"] is False
    assert chat["format"] == "json"
    assert chat["messages"][0]["images"] == ["AAAA"]
    # Aufwaermen: erst das grosse Modell anheften, dann den Embedder zurueckholen
    assert calls[2][1]["keep_alive"] == -1
    assert calls[3][1]["keep_alive"] == -1


def test_schalter_ist_ausdruecklich(monkeypatch):
    for wert, modus in (("ollama", "ollama"), ("OLLAMA", "ollama"), ("litellm", "litellm"),
                        ("", "litellm"), ("irgendwas", "litellm")):
        monkeypatch.setenv("PHOTOVAULT_LLM", wert)
        assert oc.llm_mode() == modus, wert


def test_apply_llm_env_setzt_im_ollama_modus_keine_pool_namen(monkeypatch):
    """Sonst bekaeme Ollama `local` / `embedder` als Modellnamen -- die gibt es dort nicht."""
    monkeypatch.setenv("PHOTOVAULT_LLM", "ollama")
    for name in ("LITELLM_URL", "PHOTOVAULT_CAPTION_MODEL", "PHOTOVAULT_EMBED_MODEL",
                 "PHOTOVAULT_CAPTION_NUM_CTX"):
        monkeypatch.delenv(name, raising=False)
    oc.apply_llm_env()
    import os
    for name in ("LITELLM_URL", "PHOTOVAULT_CAPTION_MODEL", "PHOTOVAULT_EMBED_MODEL"):
        assert name not in os.environ, name


def test_jobs_seite_fragt_im_ollama_modus_ollama(monkeypatch):
    import api.capabilities as capab

    monkeypatch.setenv("PHOTOVAULT_LLM", "ollama")
    monkeypatch.setattr(capab, "ollama_models", lambda: {"qwen3.8:27b", "qwen3-embedding:4b"})
    assert capab.llm_models() == {"qwen3.8:27b", "qwen3-embedding:4b"}
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test:11434")
    msg = capab.missing(models=("egal:1b",), have_models=None)
    assert "http://ollama.test:11434" in msg


# --------------------------------------------------------------------------
# Die Wahl im Setup-Wizard (photovault#18) ist derselbe Schalter
# --------------------------------------------------------------------------

def _ohne_umgebung(monkeypatch):
    for name in ("PHOTOVAULT_LLM", "LITELLM_URL", "PHOTOVAULT_EMBED_URL", "OLLAMA_URL",
                 "PHOTOVAULT_CAPTION_MODEL", "PHOTOVAULT_EMBED_MODEL",
                 "PHOTOVAULT_OLLAMA_DEFAULT", "LITELLM_MASTER_KEY"):
        monkeypatch.delenv(name, raising=False)


def _wizard_waehlt(**llm):
    """Wie der Wizard speichert: ueber die Route, nicht an ihr vorbei."""
    import api.capabilities as capab
    from api.routes import setup as su

    capab.forget()
    su.save_llm(su.LlmRequest(**llm))


def test_wizard_ollama_ist_dasselbe_wie_der_schalter(monkeypatch, calls):
    """"Ollama" im Wizard schaltet llm_mode() um -- Captions, Textvektoren und
    Aufwaermen gehen an das dort eingetragene Ollama, mit den dort gewaehlten
    Modellen, nicht an einen leeren Pool."""
    _ohne_umgebung(monkeypatch)
    assert oc.llm_mode() == "litellm"          # vor der Wahl: Vorgabe

    _wizard_waehlt(mode="ollama", url="http://nas.test:11434/",
                   caption_model="gemma4:12b", embed_model="qwen3-embedding:4b")

    assert oc.llm_mode() == "ollama"
    assert oc.ollama_url() == "http://nas.test:11434"
    assert oc.llm_target() == "http://nas.test:11434"
    _all_paths(monkeypatch)
    urls = [u for u, _ in calls]
    assert urls == [
        "http://nas.test:11434/api/chat",
        "http://nas.test:11434/api/embed",
        "http://nas.test:11434/api/generate",
        "http://nas.test:11434/api/embed",
    ]
    assert calls[0][1]["model"] == "gemma4:12b"
    assert calls[2][1]["model"] == "gemma4:12b"
    assert calls[1][1]["model"] == "qwen3-embedding:4b"


def test_wizard_ollama_ohne_adresse_nimmt_das_ollama_aus_dem_buendel(monkeypatch):
    _ohne_umgebung(monkeypatch)
    monkeypatch.setenv("PHOTOVAULT_OLLAMA_DEFAULT", "http://ollama:11434")
    assert oc.llm_mode() == "litellm"           # das Buendel allein schaltet nicht um
    _wizard_waehlt(mode="ollama", caption_model="gemma4:12b")
    assert oc.llm_mode() == "ollama"
    assert oc.ollama_url() == "http://ollama:11434"


def test_umgebung_schlaegt_die_wahl_im_wizard(monkeypatch):
    """Umgebung > Datei: auf crackdesk legt die .env LiteLLM fest, und eine
    Datei daneben darf daran nichts aendern -- sonst ginge es am GPU-Tor vorbei."""
    _ohne_umgebung(monkeypatch)
    _wizard_waehlt(mode="ollama", caption_model="gemma4:12b")

    monkeypatch.setenv("PHOTOVAULT_LLM", "litellm")
    assert oc.llm_mode() == "litellm"
    assert oc.caption_model() == "local"
    monkeypatch.delenv("PHOTOVAULT_LLM")

    monkeypatch.setenv("LITELLM_URL", "http://litellm.test:4000")
    assert oc.llm_mode() == "litellm"
    assert oc.litellm_url() == "http://litellm.test:4000"
    assert oc.caption_model() == "local"


def test_wizard_anbieter_und_aus_bleiben_auf_dem_litellm_weg(monkeypatch):
    _ohne_umgebung(monkeypatch)
    _wizard_waehlt(mode="openai", url="https://api.example.test/v1",
                   caption_model="vision-x", embed_model="embed-x")
    assert oc.llm_mode() == "litellm"
    assert oc.litellm_url() == "https://api.example.test/v1"
    assert oc.caption_model() == "vision-x"

    _wizard_waehlt(mode="off", caption_model="gemma4:12b")
    assert oc.llm_mode() == "litellm"
    assert oc.caption_model() == ""
    assert oc.embed_model() == ""


def test_schalter_ollama_ohne_wizard_behaelt_die_readme_vorgaben(monkeypatch):
    _ohne_umgebung(monkeypatch)
    monkeypatch.setenv("PHOTOVAULT_LLM", "ollama")
    assert oc.caption_model() == "qwen3.8:27b"
    assert oc.embed_model() == "qwen3-embedding:4b"
