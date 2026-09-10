"""Ob das Fotoarchiv gerade lesbar ist — nicht ob *eine* Datei fehlt.

Wenn der Mount weg ist, sieht jede Datei aus wie „nicht da“. Die Routen
fuer Vorschaubilder haben das als 404 beantwortet, ohne `Cache-Control`,
und der Browser hat den JSON-Fehler koerper als Bild interpretiert: bei
allen Personen dasselbe unkenntliche Kreisbild, das auch nach Rueckkehr
der NAS nicht neu geladen wird.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

from ingest.netfs import is_transient
from ingest.spaces import photo_root, under_root

NO_STORE = {"Cache-Control": "no-store"}


class ArchiveUnavailable(OSError):
    """Das Archiv ist gerade nicht erreichbar — nicht eine fehlende Datei."""


def why_unavailable(file_path: str | None = None) -> str | None:
    """Warum das Archiv gerade nicht lesbar ist, oder `None`.

    Ohne Pfad: der Mount selbst. Mit Pfad nur, wenn die Datei unter der
    Wurzel liegen sollte — eine Testdatei in `/tmp` ist kein Archivproblem.
    Ein gefuellter lokaler Ordner zaehlt als da (Entwicklung ohne NAS);
    ein leeres Mountpoint-Verzeichnis nicht.
    """
    root = photo_root()
    if not root:
        return None
    if file_path and not under_root(file_path, root):
        return None
    try:
        if os.path.ismount(root):
            return None
        p = Path(root)
        if p.is_dir() and next(p.iterdir(), None) is not None:
            return None
        if not p.exists():
            return f"{root} ist nicht gemountet"
        return f"{root} ist nicht gemountet"
    except OSError as e:
        if is_transient(e):
            return f"{root} nicht erreichbar"
        return f"{root}: {e}"


def media_http_error(exc: BaseException, path: str | None = None) -> HTTPException:
    """404/503/500 fuer Bildrouten — immer `no-store`.

    Sonst haelt der Browser den Fehler fest und zeigt ihn weiter als Bild,
    auch wenn das Archiv laengst wieder da ist. `max-age=86400` gilt nur
    fuer echte JPEGs.
    """
    if isinstance(exc, ArchiveUnavailable) or (
        isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError)
        and is_transient(exc)
    ):
        detail = why_unavailable(path) or str(exc) or "Fotoarchiv nicht erreichbar"
        return HTTPException(503, detail, headers=NO_STORE)
    if isinstance(exc, FileNotFoundError):
        reason = why_unavailable(path)
        if reason:
            return HTTPException(503, reason, headers=NO_STORE)
        return HTTPException(404, "image missing", headers=NO_STORE)
    return HTTPException(500, str(exc) or "Bild nicht lesbar", headers=NO_STORE)
