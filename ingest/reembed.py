"""Text-Vektoren neu berechnen, ohne das Vision-Modell zu bemuehen.

Wenn sich Personen oder Notizen an einem Foto aendern, muss nur das Dokument
neu zusammengesetzt und eingebettet werden -- rund 130 ms pro Foto gegen etwa
8 s fuer eine neue Caption. Bei 50k Fotos ist das der Unterschied zwischen zwei
Stunden und mehreren Tagen. Die teure LLM-Beschreibung bleibt unangetastet.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ingest.grounding import caption_display, grounded_document
from ingest.text_embedder import TextEmbedder

logger = logging.getLogger(__name__)

BATCH = 32


def rebuild_text_vectors(
    client,
    point_ids: list[str],
    collection: str = "photos",
    ollama_url: str | None = None,
    embedder: TextEmbedder | None = None,
) -> dict[str, int]:
    """Dokument + Kopfzeile + Text-Vektor fuer die gegebenen Punkte erneuern."""
    stats = {"requested": len(point_ids), "updated": 0, "skipped": 0, "failed": 0}
    if not point_ids:
        return stats
    emb = embedder or TextEmbedder(ollama_url)

    for chunk in _chunks(point_ids, BATCH):
        try:
            points = client.retrieve(
                collection_name=collection, ids=chunk, with_payload=True, with_vectors=False
            )
        except Exception as e:
            logger.warning("Retrieve failed for %d points: %s", len(chunk), e)
            stats["failed"] += len(chunk)
            continue

        docs: list[str] = []
        targets: list[Any] = []
        heads: list[str | None] = []
        for point in points:
            payload = point.payload or {}
            doc = grounded_document(payload)
            if not doc.strip():
                stats["skipped"] += 1
                continue
            docs.append(doc)
            targets.append(point.id)
            heads.append(caption_display(payload))

        if not docs:
            continue

        vectors = emb.embed_batch(docs)
        ok_ids, ok_vecs, ok_heads = [], [], []
        for pid, vec, head in zip(targets, vectors, heads):
            if vec is None:
                stats["failed"] += 1
                continue
            ok_ids.append(pid)
            ok_vecs.append(vec)
            ok_heads.append(head)

        if not ok_ids:
            continue
        try:
            from qdrant_client.models import PointVectors

            client.update_vectors(
                collection_name=collection,
                points=[
                    PointVectors(id=pid, vector={"text": vec})
                    for pid, vec in zip(ok_ids, ok_vecs)
                ],
                wait=True,
            )
            for pid, head in zip(ok_ids, ok_heads):
                client.set_payload(
                    collection_name=collection,
                    payload={"caption_display": head},
                    points=[pid],
                    wait=False,
                )
            stats["updated"] += len(ok_ids)
        except Exception as e:
            logger.warning("Vector update failed: %s", e)
            stats["failed"] += len(ok_ids)

    return stats


def apply_annotations(
    client,
    point_ids: list[str],
    annotations: list[str],
    mode: str = "add",
    collection: str = "photos",
) -> dict[str, int]:
    """Notizen an Fotos haengen. mode: add | remove | replace.

    Gibt zurueck, welche Punkte sich geaendert haben -- nur die brauchen
    danach einen neuen Text-Vektor.
    """
    cleaned = [a.strip() for a in annotations if a and a.strip()]
    stats = {"requested": len(point_ids), "changed": 0, "missing": 0}
    changed: list[str] = []
    if not point_ids or (mode != "replace" and not cleaned):
        stats["changed_ids"] = []
        return stats

    for chunk in _chunks(point_ids, 128):
        points = client.retrieve(
            collection_name=collection, ids=chunk, with_payload=True, with_vectors=False
        )
        found = {str(p.id): (p.payload or {}) for p in points}
        stats["missing"] += len(chunk) - len(found)
        for pid, payload in found.items():
            current = list(payload.get("annotations") or [])
            if mode == "replace":
                new = list(dict.fromkeys(cleaned))
            elif mode == "remove":
                drop = {c.lower() for c in cleaned}
                new = [c for c in current if c.lower() not in drop]
            else:
                have = {c.lower() for c in current}
                new = current + [c for c in cleaned if c.lower() not in have]
            if new == current:
                continue
            client.set_payload(
                collection_name=collection,
                payload={"annotations": new},
                points=[pid],
                wait=True,
            )
            changed.append(pid)

    stats["changed"] = len(changed)
    stats["changed_ids"] = changed
    return stats


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
