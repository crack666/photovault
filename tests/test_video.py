"""ffmpeg-Poster und Probe — ohne NAS, mit einem synthetischen Clip."""
import subprocess
from pathlib import Path

import pytest

from ingest.video import have_ffmpeg, poster_image, probe, sample_images, sample_jpegs, frame_image

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg fehlt")


def _clip(path: Path, seconds: float = 1.2) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_probe_reads_duration(tmp_path):
    clip = _clip(tmp_path / "a.mp4")
    info = probe(str(clip))
    assert info.get("duration") == pytest.approx(1.2, abs=0.35)
    assert info.get("codec")


def test_poster_is_an_image(tmp_path):
    clip = _clip(tmp_path / "b.mp4")
    img = poster_image(str(clip))
    assert img.size[0] > 0 and img.size[1] > 0


def test_short_clip_yields_one_caption_jpeg(tmp_path):
    clip = _clip(tmp_path / "short.mp4", seconds=1.2)
    frames = sample_jpegs(str(clip))
    assert len(frames) == 1
    assert frames[0][:2] == b"\xff\xd8"


def test_longer_clip_yields_three_caption_jpegs(tmp_path):
    clip = _clip(tmp_path / "long.mp4", seconds=4.0)
    frames = sample_jpegs(str(clip))
    assert len(frames) == 3
    assert all(f[:2] == b"\xff\xd8" for f in frames)


def test_frame_image_is_a_pil_at_the_asked_offset(tmp_path):
    clip = _clip(tmp_path / "f.mp4", seconds=4.0)
    img = frame_image(str(clip), 1.2)
    assert img.size[0] > 0 and img.size[1] > 0


def test_sample_images_carry_their_offset(tmp_path):
    clip = _clip(tmp_path / "s.mp4", seconds=4.0)
    frames = sample_images(str(clip))
    assert len(frames) == 3
    offsets = [ss for ss, _img in frames]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == 3
