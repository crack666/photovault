"""Zusammengeklickte Suchausdrücke in Qdrant-Filter übersetzen.

Ein Ausdruck ist ein Baum: Bedingungen als Blätter, Gruppen als Knoten mit
UND/ODER. Damit lässt sich „Jonas UND Marco UND (2015 ODER 2016)“ abbilden --
Klammern sind einfach Gruppen in Gruppen.

`describe()` erzeugt die lesbare Form aus demselben Baum, aus dem auch der
Filter gebaut wird. Was angezeigt wird, ist damit garantiert das, was gesucht
wird -- eine getrennt zusammengesetzte Beschreibung würde früher oder später
von der echten Abfrage abweichen.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ingest.dates import date_bound

#: Feld -> (Payload-Schlüssel, Beschriftung)
FIELDS: dict[str, tuple[str, str]] = {
    "person": ("person_ids", "zeigt"),
    "year": ("taken_at", "aus dem Jahr"),
    "date_from": ("taken_at", "aufgenommen ab"),
    "date_to": ("taken_at", "aufgenommen bis"),
    "location": ("location_key", "am Ort"),
    "tag": ("scene_tags", "mit Szene"),
    "annotation": ("annotations", "mit Notiz"),
    "folder": ("folder_name", "im Album"),
    "space": ("space", "im Bereich"),
}


class QueryNode(BaseModel):
    """Bedingung (`field`+`value`) oder Gruppe (`op`+`children`)."""

    op: Optional[Literal["and", "or"]] = None
    children: list["QueryNode"] = Field(default_factory=list)
    field: Optional[str] = None
    value: Optional[str] = None
    #: Nur für Personen: der lesbare Name zur ID, für die Beschreibung.
    label: Optional[str] = None

    @property
    def is_group(self) -> bool:
        return self.op is not None or bool(self.children)


QueryNode.model_rebuild()


def _condition(node: QueryNode, resolver=None):
    """Ein Blatt in eine Qdrant-Bedingung übersetzen."""
    from qdrant_client.models import DatetimeRange, FieldCondition, Filter, MatchValue

    field = (node.field or "").strip()
    value = (node.value or "").strip()
    if not field or not value or field not in FIELDS:
        return None
    key = FIELDS[field][0]

    if field == "year":
        return FieldCondition(
            key=key,
            range=DatetimeRange(
                gte=date_bound(value, end=False), lte=date_bound(value, end=True)
            ),
        )
    if field == "date_from":
        return FieldCondition(key=key, range=DatetimeRange(gte=date_bound(value, end=False)))
    if field == "date_to":
        return FieldCondition(key=key, range=DatetimeRange(lte=date_bound(value, end=True)))
    if field == "person":
        ids = resolver(value) if resolver else [value.lower()]
        if not ids:
            ids = [value.lower()]
        if len(ids) == 1:
            return FieldCondition(key=key, match=MatchValue(value=ids[0]))
        # Ein mehrdeutiger Vorname trifft alle Kandidaten.
        return Filter(
            should=[FieldCondition(key=key, match=MatchValue(value=i)) for i in ids]
        )
    if field in ("location", "tag"):
        value = value.lower()
    return FieldCondition(key=key, match=MatchValue(value=value))


def to_filter(node: QueryNode, resolver=None) -> Any:
    """Baum -> Qdrant-Filter. Gibt None zurück, wenn nichts eingeschränkt wird."""
    from qdrant_client.models import Filter

    if not node.is_group:
        return _condition(node, resolver)

    parts = [to_filter(child, resolver) for child in node.children]
    parts = [p for p in parts if p is not None]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return Filter(should=parts) if node.op == "or" else Filter(must=parts)


def describe(node: QueryNode, top: bool = True) -> str:
    """Lesbare Form: 'zeigt Jonas UND zeigt Marco UND (aus 2015 ODER aus 2016)'."""
    if not node.is_group:
        field = (node.field or "").strip()
        value = (node.label or node.value or "").strip()
        if not field or not value or field not in FIELDS:
            return ""
        return f"{FIELDS[field][1]} {value}"

    parts = [describe(child, top=False) for child in node.children]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    joined = f" {'ODER' if node.op == 'or' else 'UND'} ".join(parts)
    return joined if top else f"({joined})"


def count_conditions(node: QueryNode) -> int:
    if not node.is_group:
        return 1 if (node.field and node.value) else 0
    return sum(count_conditions(child) for child in node.children)
