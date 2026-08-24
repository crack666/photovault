"""Face → bekannte Personen (Vorschlag, nie stilles Auto-Label)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.4
FACES = "faces"


class FaceMatcher:
    def __init__(self, client, collection: str = FACES, threshold: float = MATCH_THRESHOLD):
        self.client = client
        self.collection = collection
        self.threshold = threshold

    def suggest(self, embedding: list[float] | None, limit: int = 5) -> list[dict]:
        """Return [{id, name, score}] for labeled faces similar to embedding."""
        if not embedding:
            return []
        try:
            from qdrant_client.models import Filter, IsEmptyCondition, PayloadField

            labeled = Filter(
                must_not=[IsEmptyCondition(is_empty=PayloadField(key="person_id"))]
            )
            hits = self.client.query_points(
                collection_name=self.collection,
                query=embedding,
                query_filter=labeled,
                limit=limit * 3,
                score_threshold=self.threshold,
                with_payload=True,
            ).points
        except Exception as e:
            # Nicht verschlucken: ein kaputter Match sieht sonst aus wie "keine
            # bekannte Person" und die Caption verliert stillschweigend die Namen.
            logger.warning("Face match failed: %s: %s", type(e).__name__, e)
            return []
        best: dict[str, dict] = {}
        for hit in hits:
            payload = hit.payload or {}
            pid = payload.get("person_id")
            if not pid or pid.startswith("_"):
                continue
            score = float(hit.score)
            prev = best.get(pid)
            if prev is None or score > prev["score"]:
                best[pid] = {
                    "id": pid,
                    "name": payload.get("person_name") or pid,
                    "score": score,
                }
        ranked = sorted(best.values(), key=lambda x: -x["score"])
        return ranked[:limit]
