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
CAPTION_MODEL = os.environ.get("PHOTOVAULT_CAPTION_MODEL", "qwen3.8:27b-ctx128k")
EMBED_MODEL = os.environ.get("PHOTOVAULT_EMBED_MODEL", "qwen3-embedding:4b-ctx2k")
TEXT_VECTOR_SIZE = 2560
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


def post_json(url: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.warning("Ollama HTTP %s %s: %s", e.code, url, body[:500])
        raise
