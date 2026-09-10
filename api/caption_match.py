"""Ob ein Suchsatz wirklich im Foto vorkommt -- nicht nur irgendwie ähnlich.

Der Textvektor trägt Person, Album und Caption. Fotos derselben Person
liegen deshalb bei Cosinus ~0,45 zu *jedem* Stichwort: „Balkon“ hebt ein
Sofa über ein Bier, weil der Vektor vor allem „Jonas Meyer, Wohnung“
kodiert. Ohne eine Prüfung gegen die Wörter selbst ist die Rangfolge
Rauschen, und 90 Fotos heißen Treffer.

Die harte Frage ist deshalb: steht das Wort in Beschreibung, Tags, Notiz
oder Album? Fehlt es, ist die ehrliche Antwort leer -- nicht „hier sind
irgendwelche Fotos der Person“.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ingest.captioner import unwrap_caption

#: Funktionswörter, die in „Feuerwerk in der Nacht“ nicht mitgesucht werden.
STOPWORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "einem",
    "eines", "und", "oder", "mit", "ohne", "auf", "aus", "bei", "nach", "von",
    "zu", "zum", "zur", "im", "in", "am", "an", "als", "wie", "ist", "sind",
    "war", "fuer", "für", "uber", "über", "the", "and", "or", "of", "a",
})

_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
_UMLAUT = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def fold(text: str) -> str:
    return (text or "").casefold().translate(_UMLAUT)


def tokens(query: str) -> list[str]:
    """Inhaltliche Wörter. Leer heißt: an diesem Satz gibt es nichts zu prüfen."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _SPLIT.split(fold(query)):
        if len(raw) < 3 or raw in STOPWORDS or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def haystack(payload: dict[str, Any]) -> str:
    teile: list[str] = [
        unwrap_caption(payload.get("caption_de")),
        str(payload.get("caption_display") or ""),
        str(payload.get("folder_name") or ""),
        str(payload.get("location") or ""),
        str(payload.get("event_name") or ""),
    ]
    for key in ("scene_tags", "annotations"):
        teile.extend(str(x) for x in (payload.get(key) or []))
    return fold(" ".join(teile))


def payload_matches(payload: dict[str, Any], terms: Iterable[str]) -> bool:
    """Jedes Wort muss als Wortanfang vorkommen -- „balkon“ trifft „Balkone“."""
    text = haystack(payload)
    if not text:
        return False
    for term in terms:
        if not re.search(rf"(?<![a-z0-9]){re.escape(term)}", text):
            return False
    return True


def collapse_copies(points: list) -> list:
    """Bitidentische Dateien einmal -- WhatsApp-Kopie neben dem Original."""
    seen: set[str] = set()
    out = []
    for point in points:
        digest = (getattr(point, "payload", None) or {}).get("content_sha256")
        if digest:
            if digest in seen:
                continue
            seen.add(digest)
        out.append(point)
    return out
