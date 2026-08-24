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

CACHE_DIR = Path(
    os.environ.get("PHOTOVAULT_THUMB_CACHE", Path.home() / ".cache" / "photovault-thumbs")
)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
ALLOWED_SIZES = (160, 320, 640, 1280)


def normalize_size(size: int) -> int:
    """Auf feste Stufen runden -- sonst legt jede Pixelbreite einen Cache-Eintrag an."""
    for allowed in ALLOWED_SIZES:
        if size <= allowed:
            return allowed
    return ALLOWED_SIZES[-1]


def _cache_path(key: str, size: int) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # Zweistufig, damit kein Verzeichnis mit 50k Eintraegen entsteht.
    return CACHE_DIR / digest[:2] / f"{digest}_{size}.jpg"


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
    size = normalize_size(size)
    key = f"{file_path}|{box}|{pad}" if box else file_path
    cached = _cache_path(key, size)
    if cached.is_file():
        try:
            return cached.read_bytes()
        except OSError:
            pass

    data = _render(file_path, size, box, pad, image)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cached)
    except OSError as e:
        logger.debug("Thumb cache write failed for %s: %s", file_path, e)
    return data


def _render(file_path: str, size: int, box: list | None, pad: float, image=None) -> bytes:
    from PIL import Image, ImageOps

    if image is None:
        src = Path(file_path)
        if src.suffix.lower() not in IMAGE_EXT or not src.is_file():
            raise FileNotFoundError(file_path)
        image = Image.open(src)
    # Handyfotos tragen die Ausrichtung im EXIF; ohne das steht die Haelfte quer.
    image = ImageOps.exif_transpose(image).convert("RGB")

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
    image.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


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
