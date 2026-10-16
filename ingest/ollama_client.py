"""Schmaler HTTP-Client fuer LiteLLM, wahlweise Ollama direkt (stdlib).

Zwei Wege zu den Modellen, und welcher gilt, entscheidet allein der Schalter
``PHOTOVAULT_LLM``:

- ``litellm`` (Vorgabe): LiteLLM, Pools ``local`` / ``embedder``. Nur dort sitzt
  das GPU-Tor (photovault#19, ``ingest/gpu_tor.py``); auf crackdesk ist das
  der Weg.
- ``ollama``: Ollama direkt unter ``OLLAMA_URL``, fuer Installationen ohne
  LiteLLM (photovault#26). Kennt kein GPU-Tor.

Es gibt keinen stillen Rueckfall: ein gesetztes ``OLLAMA_URL`` allein schaltet
nicht um (alte ``.env``-Dateien tragen es), und ein leeres ``LITELLM_URL``
heisst "der lokale Proxy", nicht "Ollama". Bis photovault#19 war genau dieser
Rueckfall der Weg am Tor vorbei. Alle Ollama-Endpunkte stehen in diesem Modul
(``ollama_post``, ``ollama_models``); ein Test laesst sie nirgends sonst zu.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from ingest.gpu_tor import from_http_error

logger = logging.getLogger(__name__)

DEFAULT_LITELLM_URL = "http://127.0.0.1:4000"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


def llm_mode() -> str:
    """``litellm`` (Vorgabe) oder ``ollama`` -- Ollama nur ausdruecklich gewaehlt."""
    mode = (os.environ.get("PHOTOVAULT_LLM") or "").strip().lower()
    return "ollama" if mode == "ollama" else "litellm"


#: Mit LiteLLM Pool-Aliase (`local` / `embedder`): welches Gewicht dahinter
#: liegt, steht nur in der LiteLLM-Config. Mit Ollama direkt Ollama-Tags; die
#: Vorgaben sind die Modelle aus dem README, 2560 Dimensionen wie der Pool.
_OLLAMA_AT_IMPORT = llm_mode() == "ollama"
#: Ein leerer Wert (Compose reicht `${X:-}` durch) heisst: Vorgabe des Modus.
CAPTION_MODEL = os.environ.get("PHOTOVAULT_CAPTION_MODEL") or (
    "qwen3.8:27b" if _OLLAMA_AT_IMPORT else "local")
EMBED_MODEL = os.environ.get("PHOTOVAULT_EMBED_MODEL") or (
    "qwen3-embedding:4b" if _OLLAMA_AT_IMPORT else "embedder")
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


def litellm_url() -> str:
    """Adresse von LiteLLM; ohne Angabe der lokale Proxy auf crackdesk.

    Bis photovault#19 hiess ein leerer Wert „Ollama direkt“. Diesen stillen
    Rueckfall gibt es nicht mehr: er ging am GPU-Tor vorbei. Ollama direkt
    gibt es nur noch per ``PHOTOVAULT_LLM=ollama``.
    """
    return (
        os.environ.get("LITELLM_URL")
        or os.environ.get("PHOTOVAULT_EMBED_URL")
        or DEFAULT_LITELLM_URL
    ).rstrip("/")


def ollama_url() -> str:
    """Adresse von Ollama, nur im Modus ``ollama`` benutzt."""
    return (os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")


def llm_target() -> str:
    """Wohin Modellanfragen gerade gehen -- fuer Meldungen und die Jobs-Seite."""
    return ollama_url() if llm_mode() == "ollama" else litellm_url()


def ollama_post(endpoint: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """Ollama direkt (``/api/<endpoint>``) -- nur im ausdruecklich gewaehlten Modus.

    Kein GPU-Tor: wer das auf crackdesk einschaltet, belastet die Karte auch
    waehrend eines Spiels oder Renders.
    """
    return post_json(f"{ollama_url()}/api/{endpoint}", payload, timeout=timeout)


def ollama_models() -> set[str] | None:
    """Installierte Ollama-Tags; ``None``, wenn Ollama nicht antwortet."""
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    return {str(m.get("name") or "") for m in data.get("models", [])}


def litellm_headers() -> dict[str, str]:
    key = os.environ.get("LITELLM_MASTER_KEY") or ""
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def apply_llm_env() -> None:
    """CLI und lokaler Server: LiteLLM ist der Chokepoint, nicht Ollama.

    Ohne das fehlt `python -m ingest.caption_pass` der Schluessel, weil keine
    `.env` geladen wird -- und LiteLLM lehnt jede Anfrage ab.
    Der Key kommt aus dem ai-stack-Hub, nicht aus diesem Repo.

    Mit ``PHOTOVAULT_LLM=ollama`` gibt es nichts zu setzen: Modellnamen und
    Kontextgroesse behalten ihre Ollama-Vorgaben.
    """
    if llm_mode() == "ollama":
        return
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
        busy = from_http_error(e.code, body, e.headers)
        if busy is not None:
            # Normalfall, kein Fehler: der Aufrufer pausiert oder meldet den Grund.
            raise busy from e
        logger.warning("LLM HTTP %s %s: %s", e.code, url, body[:500])
        raise
