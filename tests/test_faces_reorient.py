"""`tools/faces_reorient.py`: alte Kaesten auf gedrehten Fotos ersetzen (#37).

Nie gegen die echte Datenbank: Qdrant laeuft hier als `:memory:`-Client, der
Detektor ist ein Doppel, das feste Kaesten in aufrechten Koordinaten liefert.
Die Umrechnung roh -> aufrecht wird an Pixeln gegen `exif_transpose` geprueft,
nicht gegen die eigene Tabelle.
"""
from __future__ import annotations

import uuid

import pytest
from PIL import Image, ImageOps

from tools import faces_reorient as fr

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

W, H = 60, 40


def _bild(pfad, ausrichtung: int, groesse=(W, H)):
    im = Image.new("RGB", groesse, (0, 0, 0))
    exif = im.getexif()
    exif[274] = ausrichtung
    im.save(pfad, exif=exif)
    return str(pfad)


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ausrichtung", range(1, 9))
def test_umrechnung_stimmt_mit_exif_transpose_ueberein(ausrichtung, tmp_path):
    """Ein weisser Kasten im rohen Bild muss im gedrehten genau dort liegen,
    wo `upright_box` ihn hinrechnet."""
    raw = Image.new("RGB", (W, H), (0, 0, 0))
    box = [5, 3, 17, 11]          # x1, y1, x2, y2 (x2/y2 exklusiv)
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            raw.putpixel((x, y), (255, 255, 255))
    exif = raw.getexif()
    exif[274] = ausrichtung
    pfad = tmp_path / "roh.png"     # verlustfrei, damit die Kanten scharf bleiben
    raw.save(pfad, exif=exif)
    with Image.open(pfad) as gelesen:
        assert gelesen.getexif().get(274) == ausrichtung
        gedreht = ImageOps.exif_transpose(gelesen)
    weiss = [(x, y) for x in range(gedreht.width) for y in range(gedreht.height)
             if gedreht.getpixel((x, y))[0] > 200]
    ist = [min(x for x, _ in weiss), min(y for _, y in weiss),
           max(x for x, _ in weiss) + 1, max(y for _, y in weiss) + 1]
    assert fr.upright_box(box, ausrichtung, W, H) == ist


def test_iou_und_paarung_nehmen_jedes_gesicht_nur_einmal():
    assert fr.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert fr.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    alt = [[0, 0, 10, 10], [1, 0, 11, 10]]      # zwei Kaesten, beide nahe am selben
    neu = [[0, 0, 10, 10], [40, 40, 50, 50]]
    paare = fr.pair_by_iou(alt, neu)
    assert paare == {0: (0, 1.0)}, "der zweite alte Kasten bekommt nicht denselben neuen"


# --------------------------------------------------------------------------
# Lauf gegen einen Qdrant im Speicher
# --------------------------------------------------------------------------

PHOTO_ID = "a" * 64


class Detektor:
    """Doppel fuer `FaceEmbedder`: feste Gesichter in aufrechten Koordinaten."""

    def __init__(self, faces):
        self.faces = faces
        self.calls = []

    def process(self, file_path, image=None, bgr=None):
        self.calls.append(file_path)
        return {"faces": [dict(f) for f in self.faces]}


def _vec(seed: float):
    return [seed] + [0.01] * 511


@pytest.fixture
def qdrant():
    from qdrant_client import QdrantClient

    q = QdrantClient(location=":memory:")
    q.create_collection("photos", vectors_config={})
    q.create_collection("faces", vectors_config=VectorParams(size=512, distance=Distance.COSINE))
    return q


def _foto(q, path, ausrichtung_box_namen):
    """Ein Foto-Punkt plus alte Gesichter in Roh-Koordinaten."""
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, PHOTO_ID))
    q.upsert("photos", points=[PointStruct(id=point_id, vector={}, payload={
        "file_path": path, "photo_uid": PHOTO_ID, "photo_id": PHOTO_ID, "kind": "photo",
        "face_count": len(ausrichtung_box_namen), "person_ids": ["p1"],
        "person_names": ["Kind"]})])
    ids = []
    for i, (box, pid, name) in enumerate(ausrichtung_box_namen):
        fid = fr.QdrantWriter.face_point_id(PHOTO_ID, i, box)
        payload = {"face_id": fid, "photo_id": PHOTO_ID, "file_path": path, "box": box,
                   "frame_ss": None}
        if pid:
            payload.update(person_id=pid, person_name=name)
        q.upsert("faces", points=[PointStruct(id=fid, vector=_vec(0.5 + i), payload=payload)])
        ids.append(fid)
    return point_id, ids


def _faces(q):
    pts, _ = q.scroll("faces", limit=100, with_payload=True)
    return pts


def _photo(q, point_id):
    return q.retrieve("photos", ids=[point_id], with_payload=True)[0].payload


# Ausrichtung 3 (180 Grad), 60x40: der rohe Kasten [5, 3, 17, 11] liegt
# aufrecht bei [43, 29, 55, 37].
ROH = [5, 3, 17, 11]
AUFRECHT = [43, 29, 55, 37]


def test_trockenlauf_aendert_nichts(tmp_path, qdrant):
    p = _bild(tmp_path / "kopf.jpg", 3)
    point_id, old_ids = _foto(qdrant, p, [(ROH, "p1", "Kind")])
    det = Detektor([{"box": AUFRECHT, "embedding": _vec(0.9), "score": 0.9},
                    {"box": [2, 2, 12, 12], "embedding": _vec(0.2), "score": 0.8}])
    vorher = sorted(str(f.id) for f in _faces(qdrant))

    tally = fr.run(qdrant, det, apply=False)

    assert tally["neu erkannt"] == 1
    assert tally["Namen uebertragen"] == 1
    assert sorted(str(f.id) for f in _faces(qdrant)) == vorher
    assert fr.MARKER not in _photo(qdrant, point_id)


def test_apply_ersetzt_die_kaesten_und_nimmt_den_namen_mit(tmp_path, qdrant):
    p = _bild(tmp_path / "kopf.jpg", 3)
    point_id, old_ids = _foto(qdrant, p, [(ROH, "p1", "Kind"), ([40, 0, 50, 8], None, None)])
    det = Detektor([{"box": AUFRECHT, "embedding": _vec(0.9), "score": 0.9},
                    {"box": [2, 2, 12, 12], "embedding": _vec(0.2), "score": 0.8}])

    tally = fr.run(qdrant, det, apply=True)

    assert tally["geschrieben"] == 1
    faces = _faces(qdrant)
    assert not {str(f.id) for f in faces} & set(old_ids), "alte Punkte sind weg"
    assert sorted(f.payload["box"] for f in faces) == sorted([AUFRECHT, [2, 2, 12, 12]])
    benannt = [f for f in faces if f.payload.get("person_id")]
    assert [(f.payload["box"], f.payload["person_name"]) for f in benannt] == [(AUFRECHT, "Kind")]
    foto = _photo(qdrant, point_id)
    assert foto["face_count"] == 2
    assert foto["person_ids"] == ["p1"] and foto["person_names"] == ["Kind"]
    assert foto[fr.MARKER] == 3

    # Zweiter Lauf: gestempelt, kein Detektor-Aufruf mehr.
    det.calls.clear()
    again = fr.run(qdrant, det, apply=True)
    assert again["schon neu erkannt"] == 1 and det.calls == []


def test_name_ohne_partner_wird_nicht_still_verloren(tmp_path, qdrant):
    p = _bild(tmp_path / "kopf.jpg", 3)
    point_id, old_ids = _foto(qdrant, p, [(ROH, "p1", "Kind")])
    det = Detektor([{"box": [2, 2, 12, 12], "embedding": _vec(0.2), "score": 0.8}])

    tally = fr.run(qdrant, det, apply=True)

    assert tally["Namen gingen verloren"] == 1
    assert tally["nicht geschrieben (Namen)"] == 1
    assert {str(f.id) for f in _faces(qdrant)} == set(old_ids)
    assert _faces(qdrant)[0].payload["person_name"] == "Kind"

    tally = fr.run(qdrant, det, apply=True, allow_lost_names=True)
    assert tally["geschrieben"] == 1
    assert _photo(qdrant, point_id)["person_ids"] == []


def test_aufrechte_fotos_bleiben_unberuehrt(tmp_path, qdrant):
    p = _bild(tmp_path / "normal.jpg", 1)
    point_id, old_ids = _foto(qdrant, p, [(ROH, "p1", "Kind")])
    det = Detektor([])

    tally = fr.run(qdrant, det, apply=True)

    assert tally["Ausrichtung 1"] == 1
    assert det.calls == [], "kein Detektor fuer Fotos, die schon stimmen"
    assert {str(f.id) for f in _faces(qdrant)} == set(old_ids)


def test_writer_und_lauf_rechnen_dieselbe_punkt_id():
    """Der Lauf setzt Namen auf die ID, die `upsert_faces` vergibt -- weicht
    die Formel ab, landen Namen auf Punkten, die es nicht gibt."""
    a = fr.QdrantWriter.face_point_id("x", 0, [1, 2, 3, 4])
    assert a == str(uuid.uuid5(uuid.NAMESPACE_DNS, "x:0:[1, 2, 3, 4]"))
    assert fr.QdrantWriter.face_point_id("x", 0, [1, 2, 3, 4], 1.5) == str(
        uuid.uuid5(uuid.NAMESPACE_DNS, "x:1.5:0:[1, 2, 3, 4]"))
