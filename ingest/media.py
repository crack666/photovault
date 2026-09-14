"""Bild oder Video — eine Kennung, zwei Dateifamilien.

Der Scanner hat bisher nur Bildendungen gekannt. Videos lagen daneben im
selben Ordner und fielen still unter den Tisch. Die Unterscheidung gehoert
an eine Stelle, nicht in Scanner, Thumbs und Suche je extra.
"""
from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".webp", ".bmp",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp", ".3g2",
}

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

KIND_PHOTO = "photo"
KIND_VIDEO = "video"

_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".3gp": "video/3gpp",
    ".3g2": "video/3gpp2",
}


def kind_of(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return KIND_VIDEO
    return KIND_PHOTO


def is_video(path: str | Path) -> bool:
    return kind_of(path) == KIND_VIDEO


def video_mime(path: str | Path) -> str:
    return _VIDEO_MIME.get(Path(path).suffix.lower(), "application/octet-stream")
