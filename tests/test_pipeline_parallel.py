"""Der parallele Ingest-Pfad -- Ende zu Ende, mit einem Foto und einem Video.

Das ist der Pfad, der jedes Foto und jedes Video einliest, und er hatte
keinen Test, der ihn durchlaeuft. Heute wurde er umgebaut: Videos liefern
ihre drei Caption-Frames als Liste in den GPU-Stapel, der Medoid wird
Vektor und Poster, und das Vorschaubild entsteht erst nach der Wahl. Ein
Fehler in dieser Verdrahtung traefe den naechsten Ingest komplett.

`_run_parallel` nimmt alle Mitspieler als Argumente -- das ist der
Nahtpunkt. Modelle, EXIF, Ordner-Parser, Writer sind hier kleine Fakes;
`run_parallel` selbst (Leser, GPU-Thread, Schreiber) laeuft echt. ffmpeg
wird durch feste Frames ersetzt, damit der Test ohne Werkzeug und ohne
Netzlaufwerk laeuft.
"""
from __future__ import annotations

import time

import pytest
from PIL import Image

from ingest.pipeline import IngestConfig, IngestPipeline


class _Exif:
    def extract(self, fp, image=None):
        return {"date": "2024-05-01", "date_source": "exif", "date_confidence": 1.0,
                "datetime": None, "gps": None, "raw": {}}


class _Folder:
    def parse(self, fp):
        return {"folder_name": "Test", "subfolder": None, "folder_type": "album",
                "people": [], "sequence": 1, "location_hint": None,
                "date_hint": None, "date_hint_source": None}


class _Faces:
    def _ensure_loaded(self):
        pass

    def process(self, fp, bgr=None, image=None):
        return {"count": 0, "primary_embedding": None, "boxes": [], "faces": []}


class _Matcher:
    def suggest(self, emb):
        return []


class _Scene:
    """Vorverarbeitung und Einbettung, nachvollziehbar statt gelernt.

    Jeder Tensor traegt seine Herkunft; `encode_tensors` gibt je Tensor einen
    Vektor zurueck, der an der Herkunft haengt. So laesst sich hinterher
    pruefen, *welcher* Frame den Video-Vektor stellt.
    """

    def __init__(self):
        self.stapel: list[int] = []

    def _ensure_loaded(self):
        pass

    def preprocess(self, image):
        return {"tag": image.info.get("tag", "foto")}

    def encode_tensors(self, tensors):
        self.stapel.append(len(tensors))
        out = []
        for t in tensors:
            tag = t["tag"]
            vec = {"foto": [1.0, 0.0], "f0": [1.0, 0.0], "f1": [0.7, 0.7], "f2": [0.0, 1.0]}[tag]
            out.append({"tags": [tag], "embedding": vec})
        return out


class _Text:
    def embed_batch(self, docs):
        return [[0.5, 0.5] for _ in docs]


class _Normalizer:
    def normalize(self, record):
        pass


class _Writer:
    def __init__(self):
        self.records = []
        self.space_root = None

    def upsert(self, record):
        self.records.append(record)


class _Job:
    def update(self, **kw):
        pass


def _frame(tag):
    img = Image.new("RGB", (16, 16), (10, 20, 30))
    img.info["tag"] = tag
    return img


@pytest.fixture
def dateien(tmp_path):
    foto = tmp_path / "bild.jpg"
    Image.new("RGB", (32, 24), (200, 100, 50)).save(foto, "JPEG")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"kein echtes video, aber bytes fuer den Hash")
    return foto, video


def _pipeline(tmp_path, workers=2):
    cfg = IngestConfig(source=str(tmp_path), workers=workers, gpu_batch=4,
                       thumbs=False, skip_caption=True, progress_every=0,
                       model_dir=str(tmp_path))
    p = IngestPipeline(cfg)
    p.job = _Job()
    return p


def _lauf(pipeline, files, scene, writer):
    pipeline._run_parallel(
        files, {}, time.time(),
        exif_ext=_Exif(), folder_parser=_Folder(), face_emb=_Faces(),
        scene=scene, captioner=None, text_emb=_Text(),
        normalizer=_Normalizer(), writer=writer, matcher=_Matcher(),
    )


def test_foto_und_video_durch_das_fliessband(dateien, tmp_path, monkeypatch):
    foto, video = dateien
    # ffmpeg ersetzen: drei Frames mit Herkunft, 9 s lang.
    monkeypatch.setattr("ingest.video.probe", lambda p: {"duration": 9.0})
    monkeypatch.setattr("ingest.video_clip.sample_frames",
                        lambda p, d=None: [(0.9, _frame("f0")), (4.5, _frame("f1")), (8.1, _frame("f2"))])
    scene, writer = _Scene(), _Writer()
    p = _pipeline(tmp_path)

    _lauf(p, [str(foto), str(video)], scene, writer)

    assert p.progress.errors == 0
    assert sorted(r.kind for r in writer.records) == ["photo", "video"]
    by_kind = {r.kind: r for r in writer.records}

    f = by_kind["photo"]
    assert f.clip_embedding == [1.0, 0.0]
    assert f.content_sha256 and f.date == "2024-05-01"

    v = by_kind["video"]
    assert v.duration_s == 9.0
    # Der Medoid der drei Frames ist der mittlere -- er stellt Vektor,
    # Etiketten und den Poster-Zeitpunkt.
    assert v.clip_embedding == [0.7, 0.7]
    assert v.scene_tags == ["f1"]
    assert v.poster_ss == 4.5
    # Alle vier Tensoren (ein Foto, drei Frames) gingen in einen Stapel.
    assert sum(scene.stapel) == 4
    # Nichts vom Bildmaterial bleibt am Record haengen.
    assert v._frames is None and v._clip is None


def test_video_ohne_frames_wird_als_metadaten_gefuehrt(dateien, tmp_path, monkeypatch):
    """ffmpeg scheitert: das Video bekommt keinen Vektor, aber Datum, Album
    und Pfad -- es faellt nicht still aus dem Index."""
    _, video = dateien
    monkeypatch.setattr("ingest.video.probe", lambda p: {"duration": 9.0})
    monkeypatch.setattr("ingest.video_clip.sample_frames",
                        lambda p, d=None: (_ for _ in ()).throw(RuntimeError("ffmpeg")))
    scene, writer = _Scene(), _Writer()
    p = _pipeline(tmp_path)

    _lauf(p, [str(video)], scene, writer)

    assert len(writer.records) == 1
    v = writer.records[0]
    assert v.kind == "video" and v.clip_embedding is None and v.poster_ss is None
    assert v.file_warning == "unreadable"
    assert v.date == "2024-05-01"
    assert scene.stapel in ([], [0])


def test_kurzer_clip_hat_einen_frame_und_bekommt_ihn_als_poster(dateien, tmp_path, monkeypatch):
    _, video = dateien
    monkeypatch.setattr("ingest.video.probe", lambda p: {"duration": 1.5})
    monkeypatch.setattr("ingest.video_clip.sample_frames", lambda p, d=None: [(0.0, _frame("f2"))])
    scene, writer = _Scene(), _Writer()
    p = _pipeline(tmp_path)

    _lauf(p, [str(video)], scene, writer)

    v = writer.records[0]
    assert v.clip_embedding == [0.0, 1.0] and v.poster_ss == 0.0
