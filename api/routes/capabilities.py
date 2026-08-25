"""Was diese Installation kann — eine Frage, eine Antwort.

Die Oberflaeche fragt das einmal beim Laden und bietet danach nichts an, was
hier nicht steht. Vorher pruefte jede Stelle fuer sich, und die meisten
pruefte gar nicht: das Freitextfeld nahm eine Eingabe an und antwortete erst
beim Suchen mit 503, der Haken „Textvektoren neu rechnen" stand auch ohne
Ollama da, und der Atlas riet zu einem Befehl, der ohne Zusatzpaket scheitert.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.capabilities import snapshot

router = APIRouter()


@router.get("")
def capabilities() -> dict:
    return snapshot()
