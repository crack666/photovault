"""Thin Ollama HTTP client (stdlib)."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
#: LiteLLM-Aliase, nicht Ollama-Tags. Welches Gewicht dahinter liegt,
#: steht nur in der LiteLLM-Config -- PhotoVault kennt `local` / `embedder`.
CAPTION_MODEL = os.environ.get("PHOTOVAULT_CAPTION_MODEL", "local")
EMBED_MODEL = os.environ.get("PHOTOVAULT_EMBED_MODEL", "embedder")
TEXT_VECTOR_SIZE = 2560
DEFAULT_LITELLM_URL = "http://127.0.0.1:4000"
#: Kontextgroesse fuer Caption-Anfragen. ``0`` bedeutet: gar kein ``num_ctx``
#: mitschicken -- dann gilt, was das Modellprofil selbst festlegt.
#:
#: Das ist der Schalter zwischen zwei gleichwertigen Wegen, den Speicher
#: freizubekommen (siehe docs/performance.md):
#:
#:   1. Ein eigenes Profil, etwa ``qwen3.8:27b-ctx8k``, das 128k-Profil vorher
#:      entladen. Dann ``PHOTOVAULT_CAPTION_NUM_CTX=0`` setzen, sonst ueber-
#:      schreibt der Captioner die Kontextgroesse des Profils und loest genau
#:      den Reload aus, den das Profil vermeiden soll.
#:   2. Dasselbe Profil weiterverwenden und ``num_ctx`` pro Anfrage senken.
#:      Kostet einen Reload beim Start (der Warmup erledigt ihn kontrolliert).
CAPTION_NUM_CTX = int(os.environ.get("PHOTOVAULT_CAPTION_NUM_CTX", "8192"))


def ollama_url(override: str | None = None) -> str:
    return (override or DEFAULT_OLLAMA_URL).rstrip("/")


def litellm_url() -> str:
    """Leer, wenn der Pool nicht konfiguriert ist -- dann bleibt Ollama der Fallback."""
    return (
        os.environ.get("LITELLM_URL") or os.environ.get("PHOTOVAULT_EMBED_URL") or ""
    ).rstrip("/")


def litellm_headers() -> dict[str, str]:
    key = os.environ.get("LITELLM_MASTER_KEY") or ""
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def apply_llm_env() -> None:
    """CLI und lokaler Server: LiteLLM ist der Chokepoint, nicht Ollama.

    Ohne das faellt `python -m ingest.caption_pass` auf :11434 zurueck, weil
    keine `.env` geladen wird -- und der Pool `local`/`embedder` bleibt tot.
    Der Key kommt aus dem ai-stack-Hub, nicht aus diesem Repo.
    """
    os.environ.setdefault("LITELLM_URL", DEFAULT_LITELLM_URL)
    os.environ.setdefault("PHOTOVAULT_CAPTION_MODEL", "local")
    os.environ.setdefault("PHOTOVAULT_EMBED_MODEL", "embedder")
    os.environ.setdefault("PHOTOVAULT_CAPTION_NUM_CTX", "0")
    if os.environ.get("LITELLM_MASTER_KEY"):
        return
    path = _ai_stack_env_path()
    key = _env_file_value(path, "LITELLM_MASTER_KEY")
    if key:
        os.environ["LITELLM_MASTER_KEY"] = key


def _ai_stack_env_path():
    from pathlib import Path

    override = os.environ.get("AI_STACK_ENV")
    if override:
        return Path(override)
    for candidate in (
        Path("/mnt/d/ai/ai-stack/.env"),
        Path("D:/ai/ai-stack/.env"),
    ):
        if candidate.is_file():
            return candidate
    return Path("/mnt/d/ai/ai-stack/.env")


def _env_file_value(path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    prefix = f"{key}="
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return ""


def post_json(
    url: str,
    payload: dict[str, Any],
    timeout: int = 180,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=data,
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.warning("Ollama HTTP %s %s: %s", e.code, url, body[:500])
        raise
