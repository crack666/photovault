"""Search Routes: Payload-Filter + optional Text-Vektor (qwen3-embedding)."""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.qdrant_util import client as qdrant, visible
from api.query import QueryNode
from ingest.dates import date_bound

logger = logging.getLogger(__name__)

#: Reissleine fuer `ids_only`. Mehr Punkte als der ganze Bestand kann eine
#: Suche nicht treffen; die Grenze faengt nur den Fall ab, dass die Schleife
#: aus einem anderen Grund nicht endet.
IDS_CAP = 200_000

#: Wie viele Kennungen der Freitext hoechstens liefert.
#:
#: Freitext ist eine *Rangfolge*, keine Menge -- "alle Treffer" gibt es dort
#: nicht, jedes Foto hat irgendeinen Abstand zum Suchsatz. Fuer die Karte
#: wird deshalb oben abgeschnitten, und die Antwort sagt es (`ranked`), damit
#: das Band nicht "412 Treffer" behauptet, wo "die 412 aehnlichsten" gemeint
#: ist.
IDS_RANK_LIMIT = 1500
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
    #: Bereich = die erste Ordnerebene, also woher das Foto stammt (`Handys`
    #: der Dump, `Fotos` die Bibliothek). Mehrere heisst "einer davon" --
    #: als Und-Bedingung waere es immer leer, ein Foto liegt an einem Ort.
    spaces: list[str] = []
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


def space_scope(spaces: list[str]):
    """Bedingung „liegt in einem dieser Bereiche“ -- oder None.

    Mehrere Bereiche heißen „einer davon“. Als Und-Bedingung wäre das Ergebnis
    immer leer: ein Foto liegt an genau einem Ort.
    """
    if not spaces:
        return None
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    terms = [FieldCondition(key="space", match=MatchValue(value=s)) for s in spaces]
    return terms[0] if len(terms) == 1 else Filter(should=terms)


def scope_text(spaces: list[str]) -> str:
    """Der Geltungsbereich in Worten -- er darf nicht unsichtbar wirken."""
    if not spaces:
        return ""
    if len(spaces) == 1:
        return f"nur im Bereich {spaces[0]}"
    return "nur in den Bereichen " + ", ".join(spaces)


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
    #: Geltungsbereich, nicht Bedingung -- wie der Papierkorb. Steht deshalb
    #: neben dem Ausdruck und nicht in ihm: der Baum bleibt der des Nutzers.
    spaces: list[str] = []
    caption_query: Optional[str] = None
    caption_min_score: Optional[float] = None
    limit: int = 50
    offset: int = 0
    #: Nur die Kennungen, ohne Payload. Fuer die Karte: sie zeigt *wo* die
    #: Treffer liegen und braucht dafuer alle, nicht die ersten fuenfzig --
    #: aber von jedem nur, dass er dazugehoert. 14.000 volle Ergebnisse waeren
    #: mehrere Megabyte fuer eine Frage, die mit einer Liste beantwortet ist.
    ids_only: bool = False


class QuerySearchResponse(BaseModel):
    #: Wie viele Fotos die Bedingung *insgesamt* treffen -- nicht wie viele
    #: auf dieser Seite stehen. Vorher stand hier die Seitenlänge, und die
    #: Oberfläche schrieb bei 5.000 passenden Fotos „48 Treffer (erste
    #: Seite)" ohne einen Weg zur zweiten.
    total: int
    #: Wie viele auf dieser Seite stehen. Zusammen mit `offset` alles, was
    #: ein Blättern braucht.
    returned: int = 0
    offset: int = 0
    results: list[dict]
    #: Der Ausdruck in Worten -- kommt aus derselben Verschachtelung wie der Filter.
    expression: str
    conditions: int
    #: Der Geltungsbereich in Worten. Leer heißt: alles außer Papierkorb.
    scope: str = ""
    #: Nur bei `ids_only` gefüllt.
    ids: list[str] = []
    #: Wahr, wenn die Liste eine Rangfolge ist und oben abgeschnitten wurde --
    #: dann sind es „die N ähnlichsten", nicht „alle Treffer".
    ranked: bool = False


@router.post("/query", response_model=QuerySearchResponse)
def search_by_query(req: QuerySearchRequest) -> QuerySearchResponse:
    from api.people_index import known_persons, resolve
    from api.query import count_conditions, describe, to_filter

    # Ueber qdrant_util.client(), nicht selbst gebaut: ein neuer Client je
    # Anfrage kostet eine neue Verbindung samt Versionsabgleich -- bei der
    # Suche gemessen 27 Anfragen je Sekunde gegen 452 bei den Kacheln,
    # nachdem dort dasselbe behoben war.
    client = qdrant()
    people = known_persons(client)
    inner = to_filter(req.query, resolver=lambda v: resolve(v, people))
    scope = space_scope(req.spaces)
    if scope is not None:
        from qdrant_client.models import Filter as _Filter

        inner = _Filter(must=[inner, scope]) if inner is not None else _Filter(must=[scope])
    filter_ = visible(inner)
    expression = describe(req.query)

    try:
        if req.caption_query:
            from ingest.text_embedder import TextEmbedder

            vec = TextEmbedder().embed(req.caption_query)
            if vec is None:
                raise HTTPException(503, "Text-Embedding fehlgeschlagen (Ollama erreichbar?)")
            points = client.query_points(
                collection_name=COLLECTION, query=vec, using="text",
                query_filter=filter_,
                limit=IDS_RANK_LIMIT if req.ids_only else req.limit,
                offset=0 if req.ids_only else req.offset,
                score_threshold=req.caption_min_score,
                with_payload=not req.ids_only,
            ).points
        elif req.ids_only:
            # Seitenweise bis zum Ende: `limit` ist hier keine Obergrenze,
            # sondern die Haeppchengroesse. Die Karte will alle.
            points, offset = [], None
            while True:
                batch, offset = client.scroll(
                    collection_name=COLLECTION, scroll_filter=filter_,
                    limit=1024, offset=offset,
                    with_payload=False, with_vectors=False,
                )
                points.extend(batch)
                if offset is None or len(points) >= IDS_CAP:
                    break
        else:
            # Blättern braucht eine Zahl, und `scroll` nimmt keine.
            #
            # Der `offset` von `scroll` ist ein Punkt-Cursor: die Kennung,
            # hinter der es weitergeht. Eine 48 dort sind keine 48 Punkte,
            # sondern eine Kennung, die es nicht gibt -- Qdrant fängt dann
            # wieder vorn an. Gemessen: Seite zwei lieferte dieselben 48
            # Fotos wie Seite eins. Aufgefallen ist das nie, weil die
            # Oberfläche nie einen Offset geschickt hat.
            #
            # `query_points` ohne Suchvektor listet dieselbe Menge in
            # Kennungsreihenfolge und versteht eine Zahl.
            points = client.query_points(
                collection_name=COLLECTION, query_filter=filter_,
                limit=req.limit, offset=req.offset,
                with_payload=True, with_vectors=False,
            ).points
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Query search failed")
        raise HTTPException(500, f"Suche fehlgeschlagen: {type(e).__name__}: {e}") from e

    if req.ids_only:
        return QuerySearchResponse(
            total=len(points),
            returned=len(points),
            results=[],
            ids=[str(p.id) for p in points],
            ranked=bool(req.caption_query),
            expression=expression or "alle Fotos",
            conditions=count_conditions(req.query),
            scope=scope_text(req.spaces),
        )

    results = [_point_to_result(p) for p in points]
    return QuerySearchResponse(
        total=_total_for(client, filter_, req, len(results)),
        returned=len(results),
        offset=req.offset,
        results=results,
        expression=expression or "alle Fotos",
        conditions=count_conditions(req.query),
        scope=scope_text(req.spaces),
    )


def _total_for(client, filter_, req, seite: int) -> int:
    """Wie viele Fotos die Bedingung trifft -- unabhängig von der Seitengröße.

    Qdrant zählt das selbst, exakt und ohne die Punkte zu holen. Die Rangfolge
    nach Bildbeschreibung ändert daran nichts: sie sortiert dieselbe Menge um.
    Nur eine Mindestähnlichkeit würde sie beschneiden, und dann ist die Zahl
    eine Obergrenze -- die Oberfläche setzt keine.

    Scheitert das Zählen, ist die Seitenlänge die ehrlichere Antwort als eine
    Ausnahme: die Treffer stehen ja schon da.
    """
    if req.caption_min_score is not None:
        return seite
    try:
        return client.count(
            collection_name=COLLECTION, count_filter=filter_, exact=True
        ).count
    except Exception as e:  # pragma: no cover -- Zaehlen ist Beiwerk
        logger.warning("Trefferzahl konnte nicht ermittelt werden: %s", e)
        return seite


@router.post("")
def search(req: SearchRequest) -> SearchResponse:
    from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

    # Ueber qdrant_util.client(), nicht selbst gebaut: ein neuer Client je
    # Anfrage kostet eine neue Verbindung samt Versionsabgleich -- bei der
    # Suche gemessen 27 Anfragen je Sekunde gegen 452 bei den Kacheln,
    # nachdem dort dasselbe behoben war.
    client = qdrant()
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

    # Der Bereich kommt als eigene Ebene dazu, nicht in die Kriterienliste:
    # bei match="any" wäre er dort eine *Alternative* ("... oder liegt in
    # Fotos") und würde die Auswahl aufweichen statt sie einzuschränken.
    scope = space_scope(req.spaces)
    if scope is not None:
        inner = Filter(must=[inner, scope]) if inner is not None else Filter(must=[scope])

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


@router.get("/spaces")
def list_spaces(limit: int = 40) -> dict:
    """Welche Bereiche es gibt und wie viele Fotos darin liegen.

    Der Bereich ist die erste Ordnerebene -- woher ein Foto stammt. An diesem
    Bestand: `Handys` der Dump, aus dem aufgeräumt wird, `Fotos` die
    Bibliothek. Das ist die Frage, die eine Bedeutungssuche allein nicht
    beantwortet: Screenshots und Belege *sind* interessant, nur nicht zwischen
    den Fotos von Menschen.

    Gezählt wird ohne Papierkorb -- sonst stimmt die Zahl im Wähler nicht mit
    der Zahl der Treffer überein, und das sähe aus wie ein Fehler.
    """
    from api.qdrant_util import visible

    # Ueber qdrant_util.client(), nicht selbst gebaut: ein neuer Client je
    # Anfrage kostet eine neue Verbindung samt Versionsabgleich -- bei der
    # Suche gemessen 27 Anfragen je Sekunde gegen 452 bei den Kacheln,
    # nachdem dort dasselbe behoben war.
    client = qdrant()
    try:
        hits = client.facet(collection_name=COLLECTION, key="space",
                            facet_filter=visible(), limit=limit).hits
    except Exception as e:
        # Kein leeres Ergebnis vortäuschen: ohne Bereiche fehlt der Wähler,
        # und der Grund soll dastehen.
        logger.exception("Bereiche nicht zählbar")
        raise HTTPException(
            500, f"Bereiche nicht zählbar: {type(e).__name__}: {e}"
        ) from e
    return {
        "spaces": [{"name": str(h.value), "count": h.count} for h in hits],
        "total": sum(h.count for h in hits),
    }
