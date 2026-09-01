"""Der Bereich eines Fotos: die erste Ordnerebene unter der gemeinsamen Wurzel.

Kein neues Konzept, sondern ein Name für etwas, das schon da ist. `sources.txt`
listet die Verzeichnisse, die indiziert werden -- `/mnt/photo/Handys` (der
Dump, aus dem aufgeräumt wird) und `/mnt/photo/Fotos` (die Bibliothek). Genau
diese Ebene ist der Bereich, und deshalb ist der Bereich *wo die Datei liegt*:
verschiebt man ein Foto, wechselt es den Bereich.

Diese Datei existiert, damit es dafür genau eine Rechnung gibt. Die Karte
leitet den Bereich beim Bauen aus dem Pfad ab, die Suche braucht ihn als
Payload-Feld (Qdrant kann keine Präfixe filtern) -- zwei Stellen, aber eine
Regel. Weicht das Feld vom Pfad ab, ist das Feld falsch, nicht der Pfad;
`tools/backfill_spaces.py` rechnet es jederzeit neu.
"""
from __future__ import annotations

import os

#: Wenn ein Pfad nicht unter der Wurzel liegt oder nichts übrig bleibt.
UNKNOWN = "?"


def _slash(path: str) -> str:
    """Backslashes zu Schrägstrichen -- wie in normalizer, provenance, relocate.

    Ohne das bekäme bei einem Ingest unter Windows jedes Foto den Bereich `?`:
    die Zerlegung sucht `/`, findet in `D:\\Fotos\\Handys\\x.jpg` keinen, und
    hält den ganzen Pfad für einen Dateinamen ohne Ordner. Es schlägt nichts
    fehl, der Bereichs-Wähler der Suche wäre nur leer.
    """
    return (path or "").replace("\\", "/")


def common_root(paths) -> str:
    """Das gemeinsame Elternverzeichnis aller Pfade.

    `os.path.commonprefix` arbeitet zeichenweise und liefert bei
    `/mnt/photo/Fotos` und `/mnt/photo/Fun` das Stück `/mnt/photo/F` -- deshalb
    wird bis zum letzten Trenner zurückgeschnitten.
    """
    paths = [_slash(p) for p in paths if p]
    if not paths:
        return ""
    prefix = os.path.commonprefix(paths)
    if len(paths) == 1:
        # Ein einzelner Pfad ist sein eigener Präfix; ohne das wäre die Wurzel
        # der Ordner der Datei und jeder Bereich hieße wie die Datei.
        return prefix.rsplit("/", 1)[0]
    return prefix.rsplit("/", 1)[0] if "/" in prefix else ""


def space_of(file_path: str, root: str) -> str:
    """Der Bereichsname für einen Pfad unterhalb von `root`."""
    if not file_path:
        return UNKNOWN
    file_path, root = _slash(file_path), _slash(root)
    rest = (
        file_path[len(root):] if root and file_path.startswith(root) else file_path
    ).strip("/")
    if not rest:
        return UNKNOWN
    head = rest.split("/")[0]
    # Eine Datei direkt in der Wurzel hat keinen Bereich -- sonst wäre jeder
    # Dateiname einer.
    return head if "/" in rest else UNKNOWN


def assign(paths) -> tuple[str, list[str], list[int]]:
    """(Wurzel, Bereichsnamen, Index je Pfad) -- in der Reihenfolge des Auftretens."""
    paths = list(paths)
    root = common_root(paths)
    names: list[str] = []
    index: dict[str, int] = {}
    out: list[int] = []
    for path in paths:
        name = space_of(path or "", root)
        if name not in index:
            index[name] = len(names)
            names.append(name)
        out.append(index[name])
    return root, (names or [UNKNOWN]), out


def root_from_index(client, collection: str = "photos", batch: int = 1024) -> str:
    """Die Wurzel aus allen indizierten Pfaden bestimmen.

    Bewusst *alle*, nicht eine Stichprobe: der gemeinsame Präfix einer
    Stichprobe kann tiefer liegen als der echte -- fallen zufällig nur Pfade
    aus einem Bereich hinein, wäre die Wurzel dieser Bereich und alle Fotos
    lägen plötzlich in Unterbereichen. Ein Durchlauf über 17.000 Pfade ohne
    Vektoren dauert rund eine Sekunde, und gebraucht wird er nur beim
    Verschieben.
    """
    paths, offset = [], None
    while True:
        found, offset = client.scroll(
            collection_name=collection, limit=batch, offset=offset,
            with_payload=["file_path"], with_vectors=False,
        )
        paths.extend((p.payload or {}).get("file_path") or "" for p in found)
        if offset is None:
            return common_root(paths)
