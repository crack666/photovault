from __future__ import annotations

import os

from qdrant_client import QdrantClient

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
PHOTOS = "photos"
FACES = "faces"


def client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)
