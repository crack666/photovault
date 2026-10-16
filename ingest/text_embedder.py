"""Text Embedding via LiteLLM pool `embedder` (qwen3-embedding:4b-ctx2k, 2560d).

Vorgabe ist LiteLLM (photovault#19); Embeddings laesst das GPU-Tor immer
durch, die Suche funktioniert also auch waehrend eines Spiels. Mit
``PHOTOVAULT_LLM=ollama`` geht es an Ollama direkt (photovault#26).
"""
from __future__ import annotations

import logging

from ingest.ollama_client import (
    EMBED_MODEL,
    TEXT_VECTOR_SIZE,
    litellm_headers,
    litellm_url,
    llm_mode,
    ollama_post,
    post_json,
)

logger = logging.getLogger(__name__)


class TextEmbedder:
    def __init__(self, model: str = EMBED_MODEL):
        self._model = model

    def embed(self, text: str) -> list[float] | None:
        vecs = self.embed_batch([text])
        return vecs[0] if vecs else None

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            if llm_mode() == "ollama":
                return self._via_ollama(texts)
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
                if 0 <= idx < len(texts) and vec and len(vec) == TEXT_VECTOR_SIZE:
                    out[idx] = list(vec)
            return out
        except Exception as e:
            logger.warning("Text embedding failed: %s", e)
            return [None] * len(texts)

    def _via_ollama(self, texts: list[str]) -> list[list[float] | None]:
        resp = ollama_post("embed", {"model": self._model, "input": texts}, timeout=60)
        embeddings = resp.get("embeddings") or []
        out: list[list[float] | None] = []
        for i, _ in enumerate(texts):
            vec = embeddings[i] if i < len(embeddings) else None
            out.append(list(vec) if vec and len(vec) == TEXT_VECTOR_SIZE else None)
        return out
