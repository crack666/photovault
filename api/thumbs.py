"""Thumbnail-Cache.

Die Originale liegen auf dem NAS. Ein Bildraster mit 48 Treffern wuerde sonst
48 SMB-Reads a mehrere MB ausloesen -- pro Seitenaufruf. Einmal erzeugte
Thumbnails landen deshalb auf der lokalen Platte und werden von dort bedient.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Wo die Vorschaubilder liegen -- im Arbeitsverzeichnis, nicht unter ~/.cache.
#:
#: Zwei Gruende. Im Container ist `~` das Heimatverzeichnis *im Container*:
#: der Cache stirbt dort bei jedem Neubau des Images, und 14.593 Bilder
#: muessen wieder ueber die Leitung. Und auf dem Rechner will man sehen,
#: wieviel das Werkzeug belegt, ohne in einem versteckten Ordner zu suchen.
#:
#: Abgeleitet vom Modulort, nicht vom Arbeitsverzeichnis: uvicorn wird nicht
#: immer aus dem Projektordner gestartet, und ein Cache, der je nach Aufruf
#: woanders liegt, ist kein Cache.
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data" / "thumbs"
CACHE_DIR = Path(os.environ.get("PHOTOVAULT_THUMB_CACHE", DEFAULT_CACHE))

#: Der alte Ort. Wird beim Lesen weiter beruecksichtigt, damit ein Umzug
#: nicht bedeutet, dass 651 MB neu gerechnet werden -- `tools/thumbs.py
#: --move` schiebt sie herueber.
LEGACY_CACHE = Path.home() / ".cache" / "photovault-thumbs"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
ALLOWED_SIZES = (160, 320, 640, 1280)


def normalize_size(size: int) -> int:
    """Auf feste Stufen runden -- sonst legt jede Pixelbreite einen Cache-Eintrag an."""
    for allowed in ALLOWED_SIZES:
        if size <= allowed:
            return allowed
    return ALLOWED_SIZES[-1]


def _rel(key: str, size: int) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # Zweistufig, damit kein Verzeichnis mit 50k Eintraegen entsteht.
    return Path(digest[:2]) / f"{digest}_{size}.jpg"


def _cache_path(key: str, size: int) -> Path:
    return CACHE_DIR / _rel(key, size)


def _find_cached(key: str, size: int) -> Path | None:
    """Ein vorhandenes Vorschaubild -- neuer Ort zuerst, alter als Rueckfall.

    Ohne den Rueckfall waere der Umzug des Cache-Ortes ein stiller Neuaufbau
    von 651 MB ueber das Netzlaufwerk. So merkt man ihn nicht.
    """
    rel = _rel(key, size)
    neu = CACHE_DIR / rel
    if neu.is_file():
        return neu
    alt = LEGACY_CACHE / rel
    if LEGACY_CACHE != CACHE_DIR and alt.is_file():
        return alt
    return None


WARN_TRUNCATED = "truncated"
WARN_UNREADABLE = "unreadable"


def get_thumb(
    file_path: str,
    size: int = 320,
    box: list | None = None,
    pad: float = 0.35,
    image=None,
) -> bytes:
    """JPEG liefern; beim ersten Mal erzeugen. `box` schneidet ein Gesicht aus.

    `image` ist ein bereits geladenes PIL-Image -- der Ingest reicht es durch,
    statt die Datei ein weiteres Mal zu dekodieren.
    """
    data, _warn = make_thumb(file_path, size=size, box=box, pad=pad, image=image)
    return data


def make_thumb(
    file_path: str,
    size: int = 320,
    box: list | None = None,
    pad: float = 0.35,
    image=None,
) -> tuple[bytes, str | None]:
    """Wie get_thumb, plus Warnung wenn die Datei unvollständig oder unlesbar ist."""
    size = normalize_size(size)
    key = f"{file_path}|{box}|{pad}" if box else file_path
    vorhanden = _find_cached(key, size)
    if vorhanden is not None:
        try:
            return vorhanden.read_bytes(), None
        except OSError:
            pass

    cached = _cache_path(key, size)
    data, warn = _render(file_path, size, box, pad, image)
    if warn is None:
        warn = jpeg_truncation_hint(file_path)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cached)
    except OSError as e:
        logger.debug("Thumb cache write failed for %s: %s", file_path, e)
    return data, warn


def jpeg_truncation_hint(file_path: str) -> str | None:
    """JPEG ohne Endemarkierung in den letzten Bytes — typisch abgebrochener Transfer."""
    suffix = Path(file_path).suffix.lower()
    if suffix not in {".jpg", ".jpeg"}:
        return None
    try:
        with open(file_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            n = fh.tell()
            if n < 4:
                return WARN_UNREADABLE
            fh.seek(max(0, n - 64))
            tail = fh.read(64)
    except OSError:
        return WARN_UNREADABLE
    if b"\xff\xd9" not in tail:
        return WARN_TRUNCATED
    return None


def _render(file_path: str, size: int, box: list | None, pad: float, image=None) -> tuple[bytes, str | None]:
    from PIL import Image, ImageFile, ImageOps

    # Abgebrochene Kamera-JPEGs (Transfer, volle Karte) sollen eine Vorschau
    # liefern, keine 500er-Kachel. Pillow lässt den Rest der Datei weg.
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    warn = None

    if image is None:
        src = Path(file_path)
        if src.suffix.lower() not in IMAGE_EXT or not src.is_file():
            raise FileNotFoundError(file_path)
        image = Image.open(src)
        try:
            image.load()
        except OSError as e:
            logger.warning("truncated image, using what decoded: %s (%s)", file_path, e)
            warn = WARN_TRUNCATED
            if getattr(image, "im", None) is None:
                raise
    try:
        # Handyfotos tragen die Ausrichtung im EXIF; ohne das steht die Haelfte quer.
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    image = image.convert("RGB")

    if box and len(box) == 4:
        w, h = image.size
        x1, y1, x2, y2 = (int(v) for v in box)
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        px, py = int(bw * pad), int(bh * pad)
        image = image.crop(
            (max(0, x1 - px), max(0, y1 - py), min(w, x2 + px), min(h, y2 + py))
        )

    image.thumbnail((size, size))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=82)
    return buf.getvalue(), warn


def drop_cached(file_path: str) -> int:
    """Alle Vorschaubilder zu einer Datei wegwerfen.

    Beim endgueltigen Loeschen bleibt sonst der Cache als Geisterbild zurueck:
    das Foto ist weg, aber die Oberflaeche zeigt es weiter, bis der Eintrag
    zufaellig verdraengt wird.

    Geraeumt werden beide Orte. Bliebe am alten eines liegen, waere es nach
    dem Loeschen weiter zu sehen -- genau das Geisterbild, das diese Funktion
    verhindern soll.
    """
    gone = 0
    for size in ALLOWED_SIZES:
        for basis in {CACHE_DIR, LEGACY_CACHE}:
            target = basis / _rel(file_path, size)
            try:
                target.unlink()
                gone += 1
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug("Thumb %s nicht loeschbar: %s", target, e)
    return gone


def cache_stats() -> dict:
    if not CACHE_DIR.is_dir():
        return {"files": 0, "bytes": 0}
    files = bytes_ = 0
    for p in CACHE_DIR.rglob("*.jpg"):
        files += 1
        try:
            bytes_ += p.stat().st_size
        except OSError:
            pass
    return {"files": files, "bytes": bytes_}
