"""ffmpeg als Grenze: Probe, Poster, spaeter Frames und Ton.

Die Originale bleiben unangetastet. Was hier entsteht, landet im lokalen
Cache (Vorschaubild) oder im Speicher -- nie neben der Datei auf dem NAS.
Ohne ffmpeg gibt es Metadaten aus Pfad und Dateizeit, aber kein Poster.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")


class VideoToolMissing(RuntimeError):
    """ffmpeg/ffprobe nicht im PATH."""


def have_ffmpeg() -> bool:
    return bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))


def probe(file_path: str) -> dict[str, Any]:
    """Dauer, Codec, Container-Zeit -- oder leeres Dict, wenn nichts geht."""
    if not shutil.which(FFPROBE):
        raise VideoToolMissing("ffprobe nicht gefunden")
    try:
        raw = subprocess.run(
            [
                FFPROBE, "-hide_banner", "-loglevel", "error",
                "-print_format", "json", "-show_format", "-show_streams",
                file_path,
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("ffprobe failed for %s: %s", file_path, e)
        return {}
    if raw.returncode != 0:
        logger.warning("ffprobe exit %s for %s: %s", raw.returncode, file_path,
                       (raw.stderr or "")[:200])
        return {}
    try:
        data = json.loads(raw.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    fmt = data.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration") or 0) or None
    except (TypeError, ValueError):
        duration = None
    codec = None
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            codec = stream.get("codec_name")
            if duration is None:
                try:
                    duration = float(stream.get("duration") or 0) or None
                except (TypeError, ValueError):
                    pass
            break
    tags = fmt.get("tags") or {}
    created = tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate")
    return {"duration": duration, "codec": codec, "created": created}


def poster_offset(duration: float | None, ratio: float = 0.1) -> float:
    """Nicht Frame 0: der ist oft schwarz oder ein Blitz."""
    if not duration or duration <= 0:
        return 0.0
    if duration < 3:
        return 0.0
    return max(0.0, min(duration * ratio, duration - 0.15))


def poster_jpeg(file_path: str, duration: float | None = None) -> bytes:
    if not shutil.which(FFMPEG):
        raise VideoToolMissing("ffmpeg nicht gefunden")
    if duration is None:
        duration = probe(file_path).get("duration")
    ss = poster_offset(duration)
    try:
        raw = subprocess.run(
            [
                FFMPEG, "-hide_banner", "-loglevel", "error",
                "-ss", f"{ss:.3f}", "-i", file_path,
                "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg",
                "-",
            ],
            capture_output=True, timeout=90, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"ffmpeg poster failed: {e}") from e
    if raw.returncode != 0 or not raw.stdout:
        err = (raw.stderr or b"").decode("utf-8", "replace")[:200]
        raise RuntimeError(f"ffmpeg poster exit {raw.returncode}: {err}")
    return raw.stdout


def poster_image(file_path: str, duration: float | None = None):
    from PIL import Image

    data = poster_jpeg(file_path, duration)
    return Image.open(BytesIO(data))
