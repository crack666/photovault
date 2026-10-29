"""Text-Embedding: LiteLLM-Pool `embedder` (Vorgabe), sonst Ollama direkt.

Hier: LiteLLM-Alias `embedder` = qwen3-embedding:4b-ctx2k, 2560 Dimensionen.
Vorgabe ist LiteLLM (photovault#19); Embeddings laesst das GPU-Tor immer
durch, die Suche funktioniert also auch waehrend eines Spiels. Mit
``PHOTOVAULT_LLM=ollama`` oder der Wahl "Ollama" im Setup-Wizard geht es an
Ollama direkt (photovault#26); dann gilt der Ollama-Tag aus
`data/settings.json`, und die Dimension ist die, die der Wizard per Probe
gemessen hat.
"""
from __future__ import annotations

import logging

from ingest.ollama_client import (
    embed_model,
    litellm_headers,
    litellm_url,
    llm_mode,
    ollama_post,
    post_json,
    text_vector_size,
)

logger = logging.getLogger(__name__)


class TextEmbedder:
    def __init__(self, model: str | None = None):
        self._model = model or embed_model()

    def embed(self, text: str) -> list[float] | None:
        vecs = self.embed_batch([text])
        return vecs[0] if vecs else None

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Vektoren in der Groesse der Collection; was nicht passt, wird None.

        Ein Vektor falscher Laenge kaeme bei Qdrant als Fehler an -- und
        zwar beim Schreiben des ganzen Stapels, nicht bei dem einen Text.
        """
        if not texts:
            return []
        dim = text_vector_size()
        try:
            raw = self.raw_batch(texts)
        except Exception as e:
            logger.warning("Text embedding failed: %s", e)
            return [None] * len(texts)
        return [v if v and len(v) == dim else None for v in raw]

    def raw_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Die Vektoren, wie das Modell sie liefert -- ohne Groessenpruefung.

        Der Setup-Wizard misst damit, welche Dimension ein gewaehltes
        Modell hat, bevor die Collection angelegt wird. Fehler kommen hier
        als Ausnahme, nicht als None: der Aufrufer will den Grund sehen.
        """
        if llm_mode() == "ollama":
            resp = ollama_post("embed", {"model": self._model, "input": texts}, timeout=60)
            embeddings = resp.get("embeddings") or []
            return [list(embeddings[i]) if i < len(embeddings) and embeddings[i] else None
                    for i in range(len(texts))]
        resp = post_json(
            f"{litellm_url()}/v1/embeddings",
            {"model": self._model, "input": texts},
            timeout=60,
            headers=litellm_headers(),
        )
        rows = resp.get("data") or []
        out: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            idx = int(row.get("index", 0))
            vec = row.get("embedding")
            if 0 <= idx < len(texts) and vec:
                out[idx] = list(vec)
        return out
