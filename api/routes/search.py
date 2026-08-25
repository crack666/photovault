"""Search Routes: Payload-Filter + optional Text-Vektor (qwen3-embedding)."""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import visible
from api.query import QueryNode
from ingest.dates import date_bound

logger = logging.getLogger(__name__)
router = APIRouter()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = "photos"


class SearchRequest(BaseModel):
    persons: list[str] = []
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    location: Optional[str] = None
    scene_tags: list[str] = []
    annotations: list[str] = []
    folder_name: Optional[str] = None
    caption_query: Optional[str] = None
    #: Wie die Kriterien verknüpft werden. "all" = jedes muss zutreffen
    #: (der Normalfall), "any" = eines genügt.
    match: Literal["all", "any"] = "all"
    #: Mehrere Personen: gemeinsam auf einem Foto ("all") oder jede für sich ("any").
    persons_match: Literal["all", "any"] = "all"
    #: Freitext ist kein Filter, sondern eine Rangfolge. Ab hier wird
    #: abgeschnitten -- ohne das kommen auch die schlechtesten Treffer mit.
    caption_min_score: Optional[float] = None
    limit: int = 50
    offset: int = 0


class SearchResponse(BaseModel):
    total: int
    results: list[dict]
    #: Namen, zu denen keine Person gefunden wurde -- die UI soll das zeigen,
    #: statt "keine Treffer" unerklaert stehen zu lassen.
    unknown_persons: list[str] = []


def _point_to_result(p) -> dict:
    payload = p.payload or {}
    return {
        "id": p.id,
        "file_path": payload.get("file_path"),
        "caption_de": payload.get("caption_de"),
        "caption_display": payload.get("caption_display"),
        "annotations": payload.get("annotations") or [],
        "person_ids": payload.get("person_ids") or [],
        "person_names": payload.get("person_names") or [],
        "date": payload.get("date"),
        "location": payload.get("location"),
        "scene_tags": payload.get("scene_tags") or [],
        "folder_name": payload.get("folder_name"),
        "sequence_in_folder": payload.get("sequence_in_folder"),
        "person_suggestions": payload.get("person_suggestions") or [],
        "score": getattr(p, "score", None),
    }


class QuerySearchRequest(BaseModel):
    """Zusammengeklickter Ausdruck statt fester Felder."""

    query: QueryNode
    caption_query: Optional[str] = None
    caption_min_score: Optional[float] = None
    limit: int = 50
    offset: int = 0


class QuerySearchResponse(BaseModel):
    total: int
    results: list[dict]
    #: Der Ausdruck in Worten -- kommt aus derselben Verschachtelung wie der Filter.
    expression: str
    conditions: int


@router.post("/query", response_model=QuerySearchResponse)
def search_by_query(req: QuerySearchRequest) -> QuerySearchResponse:
    from qdrant_client import QdrantClient

    from api.people_index import known_persons, resolve
    from api.query import count_conditions, describe, to_filter

    client = QdrantClient(url=QDRANT_URL)
    people = known_persons(client)
    filter_ = visible(to_filter(req.query, resolver=lambda v: resolve(v, people)))
    expression = describe(req.query)

    try:
        if req.caption_query:
            from ingest.text_embedder import TextEmbedder

            vec = TextEmbedder().embed(req.caption_query)
            if vec is None:
                raise HTTPException(503, "Text-Embedding fehlgeschlagen (Ollama erreichbar?)")
            points = client.query_points(
                collection_name=COLLECTION, query=vec, using="text",
                query_filter=filter_, limit=req.limit, offset=req.offset,
                score_threshold=req.caption_min_score, with_payload=True,
            ).points
        else:
            points, _ = client.scroll(
                collection_name=COLLECTION, scroll_filter=filter_,
                limit=req.limit, offset=req.offset,
                with_payload=True, with_vectors=False,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Query search failed")
        raise HTTPException(500, f"Suche fehlgeschlagen: {type(e).__name__}: {e}") from e

    results = [_point_to_result(p) for p in points]
    return QuerySearchResponse(
        total=len(results),
        results=results,
        expression=expression or "alle Fotos",
        conditions=count_conditions(req.query),
    )


@router.post("")
def search(req: SearchRequest) -> SearchResponse:
    from qdrant_client import QdrantClient
    from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

    client = QdrantClient(url=QDRANT_URL)
    conditions = []
    unresolved: list[str] = []
    if req.persons:
        from api.people_index import known_persons, resolve

        people = known_persons(client)
        person_terms = []
        for person in req.persons:
            ids = resolve(person, people)
            if not ids:
                unresolved.append(person)
                # Unbekannter Name: nicht ignorieren, sonst liefert die Suche
                # stillschweigend die Treffer *ohne* diese Person.
                ids = [person.lower()]
            if len(ids) == 1:
                person_terms.append(
                    FieldCondition(key="person_ids", match=MatchValue(value=ids[0]))
                )
            else:
                # "Sven" trifft mehrere -- eine davon muss es sein.
                person_terms.append(
                    Filter(
                        should=[
                            FieldCondition(key="person_ids", match=MatchValue(value=i))
                            for i in ids
                        ]
                    )
                )
        if req.persons_match == "any" and len(person_terms) > 1:
            conditions.append(Filter(should=person_terms))
        else:
            conditions.extend(person_terms)
    if req.date_from or req.date_to:
        kwargs = {}
        if req.date_from:
            kwargs["gte"] = date_bound(req.date_from, end=False)
        if req.date_to:
            kwargs["lte"] = date_bound(req.date_to, end=True)
        conditions.append(FieldCondition(key="taken_at", range=DatetimeRange(**kwargs)))
    if req.location:
        loc = req.location.lower().strip()
        conditions.append(FieldCondition(key="location_key", match=MatchValue(value=loc)))
    for tag in req.scene_tags:
        conditions.append(FieldCondition(key="scene_tags", match=MatchValue(value=tag.lower())))
    for note in req.annotations:
        conditions.append(FieldCondition(key="annotations", match=MatchValue(value=note.strip())))
    if req.folder_name:
        conditions.append(
            FieldCondition(key="folder_name", match=MatchValue(value=req.folder_name))
        )
    if not conditions:
        inner = None
    elif req.match == "any":
        inner = Filter(should=conditions)
    else:
        inner = Filter(must=conditions)
    # Wer ein Foto in den Papierkorb legt, will es nicht als Suchtreffer
    # wiedersehen. Das war die offene Frage: von *wo* ausgeschlossen.
    filter_ = visible(inner)

    # Fehler hier nicht verschlucken: eine kaputte Suche saehe sonst exakt aus
    # wie "keine Treffer" und bliebe unbemerkt.
    try:
        if req.caption_query:
            from ingest.text_embedder import TextEmbedder

            vec = TextEmbedder().embed(req.caption_query)
            if vec is None:
                raise HTTPException(503, "Text-Embedding fehlgeschlagen (Ollama erreichbar?)")
            points = client.query_points(
                collection_name=COLLECTION,
                query=vec,
                using="text",
                query_filter=filter_,
                limit=req.limit,
                offset=req.offset,
                score_threshold=req.caption_min_score,
                with_payload=True,
            ).points
            results = [_point_to_result(p) for p in points]
            return SearchResponse(total=len(results), results=results, unknown_persons=unresolved)

        points, _ = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=filter_,
            limit=req.limit,
            offset=req.offset,
            with_payload=True,
            with_vectors=False,
        )
        results = [_point_to_result(p) for p in points]
        return SearchResponse(total=len(results), results=results, unknown_persons=unresolved)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(500, f"Suche fehlgeschlagen: {type(e).__name__}: {e}") from e
