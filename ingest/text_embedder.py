"""Text-Embedding: OpenAI-Format ueber den Pool (`embedder`), sonst Ollama direkt.

Hier: LiteLLM-Alias `embedder` = qwen3-embedding:4b-ctx2k, 2560 Dimensionen.
Bei einem Fremden: der Ollama-Tag aus `data/settings.json`, und die Dimension
ist die, die der Setup-Wizard per Probe gemessen hat.
"""
from __future__ import annotations

import logging

from ingest.ollama_client import (
    embed_model,
    litellm_headers,
    litellm_url,
    ollama_url,
    post_json,
    text_vector_size,
)

logger = logging.getLogger(__name__)


class TextEmbedder:
    def __init__(self, ollama: str | None = None, model: str | None = None):
        self._url = ollama_url(ollama)
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
        pool = litellm_url()
        if pool:
            resp = post_json(
                f"{pool}/v1/embeddings",
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
        resp = post_json(
            f"{self._url}/api/embed",
            {"model": self._model, "input": texts},
            timeout=60,
        )
        embeddings = resp.get("embeddings") or []
        return [list(embeddings[i]) if i < len(embeddings) and embeddings[i] else None
                for i in range(len(texts))]
