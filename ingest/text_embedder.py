"""Text Embedding via qwen3-embedding:4b-ctx2k (Ollama, 2560d)."""
from __future__ import annotations

import logging

from ingest.ollama_client import EMBED_MODEL, TEXT_VECTOR_SIZE, ollama_url, post_json

logger = logging.getLogger(__name__)


class TextEmbedder:
    def __init__(self, ollama: str | None = None, model: str = EMBED_MODEL):
        self._url = ollama_url(ollama)
        self._model = model

    def embed(self, text: str) -> list[float] | None:
        vecs = self.embed_batch([text])
        return vecs[0] if vecs else None

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            resp = post_json(
                f"{self._url}/api/embed",
                {"model": self._model, "input": texts},
                timeout=60,
            )
            embeddings = resp.get("embeddings") or []
            out: list[list[float] | None] = []
            for i, _ in enumerate(texts):
                if i < len(embeddings) and embeddings[i] and len(embeddings[i]) == TEXT_VECTOR_SIZE:
                    out.append(list(embeddings[i]))
                else:
                    out.append(None)
            return out
        except Exception as e:
            logger.warning("Text embedding failed: %s", e)
            return [None] * len(texts)
