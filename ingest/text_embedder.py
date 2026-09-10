"""Text Embedding via LiteLLM pool `embedder` (qwen3-embedding:4b-ctx2k, 2560d)."""
from __future__ import annotations

import logging
import os

from ingest.ollama_client import EMBED_MODEL, TEXT_VECTOR_SIZE, ollama_url, post_json

logger = logging.getLogger(__name__)

LITELLM_URL = os.environ.get("LITELLM_URL", "").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


class TextEmbedder:
    def __init__(self, ollama: str | None = None, model: str = EMBED_MODEL):
        self._url = ollama_url(ollama)
        self._model = model
        self._litellm = LITELLM_URL or os.environ.get("PHOTOVAULT_EMBED_URL", "").rstrip("/")

    def embed(self, text: str) -> list[float] | None:
        vecs = self.embed_batch([text])
        return vecs[0] if vecs else None

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            if self._litellm:
                headers = {}
                if LITELLM_MASTER_KEY:
                    headers["Authorization"] = f"Bearer {LITELLM_MASTER_KEY}"
                resp = post_json(
                    f"{self._litellm}/v1/embeddings",
                    {"model": self._model, "input": texts},
                    timeout=60,
                    headers=headers,
                )
                rows = resp.get("data") or []
                out: list[list[float] | None] = [None] * len(texts)
                for row in rows:
                    idx = int(row.get("index", 0))
                    vec = row.get("embedding")
                    if 0 <= idx < len(texts) and vec and len(vec) == TEXT_VECTOR_SIZE:
                        out[idx] = list(vec)
                return out
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
