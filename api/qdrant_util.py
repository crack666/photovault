from __future__ import annotations

import os

from qdrant_client import QdrantClient

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
PHOTOS = "photos"
FACES = "faces"


def client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


#: Fotos im Papierkorb tragen `trashed_at`. Sie sind noch vollständig im
#: Index -- Datei, Vektoren, Gesichter liegen alle noch da, damit „retten“
#: ein Klick bleibt. Aber sie sollen nirgends mehr auftauchen, wo Fotos
#: *angezeigt* werden. Die Trennlinie: Ansichten überspringen den
#: Papierkorb, Indexpflege (Re-Embedding, Lückenzählung) nicht -- ein
#: Wartungslauf, der stillschweigend Daten überspringt, ist schlimmer als
#: einer, der ein paar Punkte zu viel anfasst.
TRASH_KEY = "trashed_at"


def not_trashed():
    """Bedingung: dieses Foto liegt nicht im Papierkorb."""
    from qdrant_client.models import IsEmptyCondition, PayloadField

    return IsEmptyCondition(is_empty=PayloadField(key=TRASH_KEY))


def visible(filter_=None):
    """Einen Filter um „nicht im Papierkorb“ ergänzen.

    Verschachtelt statt angehängt: der übergebene Filter darf `should`
    benutzen („eines der Kriterien genügt“), und eine zusätzliche
    `must`-Bedingung daneben würde dessen Bedeutung verändern. Als eigene
    Ebene bleibt sie eine Und-Verknüpfung, egal was innen steht.
    """
    from qdrant_client.models import Filter

    if filter_ is None:
        return Filter(must=[not_trashed()])
    return Filter(must=[filter_, not_trashed()])
