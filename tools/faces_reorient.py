"""Gesichter auf gedrehten Fotos neu erkennen, Namen per Kasten-Ueberlappung mitnehmen.

Bis photovault#37 sah der Detektor das rohe Bild, die Oberflaeche das nach
EXIF-Ausrichtung gedrehte. Auf Fotos mit Ausrichtung != 1 konnten die
gespeicherten Kaesten deshalb in Roh-Koordinaten liegen -- der Zuschnitt zeigte
einen Zaun statt eines Kindes, und ein kopfstehendes Gesicht fand der Detektor
oft gar nicht. Seit dem Fix (#38) dreht der Ingest vorher.

Ob ein Foto rohe Kaesten traegt, steht nirgends -- und die Annahme "alle
gedrehten Fotos" war falsch: der Trockenlauf vom 29.10.2026 fand die Kaesten
auf allen 945 solchen Fotos schon aufrecht. Deshalb wird es je Foto gemessen
(`choose_frame`). Was bleibt, sind Fotos, auf denen frueher kein Gesicht
gefunden wurde und heute eines.

Was der Lauf je Foto tut:

1. Ausrichtung aus der Datei lesen (nur der Kopf). 1 oder keine: nichts tun.
2. Gesichter neu erkennen -- auf dem aufrechten Bild, wie der Ingest heute.
3. Die alten Kaesten einmal so, wie sie sind, und einmal aus Roh- in aufrechte
   Koordinaten umgerechnet mit den neuen per IoU (Ueberlappung) paaren,
   gierig nach groesster Ueberlappung, jedes Gesicht hoechstens einmal. Die
   Lesart mit mehr Paaren gilt. Ab `--min-iou` (Vorgabe 0,3) wandert
   `person_id`/`person_name` auf das neue Gesicht.
4. Decken sich alte und neue Kaesten eins zu eins, wird nur gestempelt.
   Sonst: neue Gesichter schreiben, die alten Punkte dieses Fotos loeschen,
   auf dem Foto `face_count`, `face_boxes`, `person_ids`/`person_names` neu
   setzen. Beides stempelt `face_orientation_fixed` (ein zweiter Lauf
   ueberspringt das Foto).

Benannte Gesichter sind der heikle Teil. Findet ein benanntes altes Gesicht
keinen Partner, verliert es seinen Namen -- solche Fotos werden **nicht**
geschrieben, sondern gelistet, ausser mit `--allow-lost-names`.

Sicherheiten: Trockenlauf ist der Standard (`--dry-run`); geschrieben wird
nur mit `--apply`. Der Trockenlauf rechnet trotzdem auf der Grafikkarte (die
neue Erkennung ist die Aussage), schreibt aber nichts nach Qdrant. Vorher das
GPU-Tor fragen (`gpu-tor.ps1`), fuer den ganzen Lauf reservieren.

    python -m tools.faces_reorient --dry-run --limit 50     # zaehlen, Beispiele
    python -m tools.faces_reorient --apply --limit 20       # eine Handvoll
    python -m tools.faces_reorient --apply                  # Rest

Danach die API neu laden (Personen-Index und Cluster sind dort gecacht).
"""
from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional

from ingest.identity import uid_of
from ingest.media import is_video
from ingest.qdrant_writer import QdrantWriter

logger = logging.getLogger(__name__)

BATCH = 256
#: Ab dieser Ueberlappung gilt ein neuer Kasten als dasselbe Gesicht. Ein
#: Gesichtskasten desselben Kopfes liegt bei 0,6-0,9; 0,3 laesst Spiel fuer
#: einen auf dem gedrehten Bild etwas anders geschnittenen Kasten, ohne den
#: Nachbarn zu treffen.
MIN_IOU = 0.3
#: Stempel auf dem Foto: neu erkannt, mit dieser Ausrichtung.
MARKER = "face_orientation_fixed"
#: Was der Lauf vom Foto liest.
PHOTO_FIELDS = ["file_path", "photo_uid", "photo_id", "content_sha256", "kind",
                "face_count", MARKER]


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------

def upright_point(x: float, y: float, orientation: int, width: int, height: int):
    """Ein Punkt des rohen Bildes (Breite `width`, Hoehe `height`) im aufrechten.

    Dieselben Abbildungen wie `PIL.ImageOps.exif_transpose`; der Test prueft
    das an Pixeln, nicht an dieser Tabelle.
    """
    w, h = width, height
    if orientation == 2:
        return w - x, y
    if orientation == 3:
        return w - x, h - y
    if orientation == 4:
        return x, h - y
    if orientation == 5:
        return y, x
    if orientation == 6:
        return h - y, x
    if orientation == 7:
        return h - y, w - x
    if orientation == 8:
        return y, w - x
    return x, y


def upright_box(box, orientation: int, width: int, height: int) -> list[int]:
    """Kasten `[x1, y1, x2, y2]` aus Roh- in aufrechte Koordinaten."""
    x1, y1, x2, y2 = (float(v) for v in box)
    ax, ay = upright_point(x1, y1, orientation, width, height)
    bx, by = upright_point(x2, y2, orientation, width, height)
    return [int(round(min(ax, bx))), int(round(min(ay, by))),
            int(round(max(ax, bx))), int(round(max(ay, by)))]


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = (float(v) for v in a)
    bx1, by1, bx2, by2 = (float(v) for v in b)
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def pair_by_iou(old: list[list[int]], new: list[list[int]],
                min_iou: float = MIN_IOU) -> dict[int, tuple[int, float]]:
    """Alte auf neue Kaesten: `{index_alt: (index_neu, iou)}`.

    Gierig nach groesster Ueberlappung, jedes Gesicht hoechstens einmal --
    zwei Koepfe nebeneinander duerfen nicht beide auf denselben neuen fallen.
    """
    scored = sorted(
        ((iou(o, n), i, j) for i, o in enumerate(old) for j, n in enumerate(new)),
        reverse=True,
    )
    pairs: dict[int, tuple[int, float]] = {}
    used: set[int] = set()
    for score, i, j in scored:
        if score < min_iou:
            break
        if i in pairs or j in used:
            continue
        pairs[i] = (j, score)
        used.add(j)
    return pairs


#: Die gespeicherten Kaesten liegen schon aufrecht / noch roh.
AS_IS = "schon aufrecht"
RAW = "roh"


def choose_frame(stored: list[list[int]], new: list[list[int]], orientation: int,
                 width: int, height: int, min_iou: float = MIN_IOU,
                 ) -> tuple[str, dict[int, tuple[int, float]]]:
    """In welchem Raum liegen die gespeicherten Kaesten? `(raum, paare)`.

    Nicht jedes Foto mit Ausrichtung != 1 traegt rohe Kaesten: der
    Trockenlauf vom 29.10.2026 fand auf allen 945 solchen Fotos die Kaesten
    schon aufrecht (752 von 752 gepaart, 408 Fotos pixelgleich), gedreht
    passten nur 69. Blind gedreht fielen 8 Namen auf 7 Fotos still auf das
    falsche Gesicht. Deshalb wird je Foto gemessen: beide Lesarten gegen die
    neue Erkennung paaren, gedreht wird nur, wenn das mehr Paare ergibt
    (bei Gleichstand die groessere Summe der Ueberlappung).
    """
    as_is = pair_by_iou(stored, new, min_iou)
    turned = pair_by_iou([upright_box(b, orientation, width, height) for b in stored],
                         new, min_iou)

    def score(pairs):
        return len(pairs), sum(s for _, s in pairs.values())

    if score(turned) > score(as_is):
        return RAW, turned
    return AS_IS, as_is


# --------------------------------------------------------------------------
# Ein Foto
# --------------------------------------------------------------------------

def read_orientation(path: str) -> tuple[int, int, int] | None:
    """`(ausrichtung, breite, hoehe)` des rohen Bildes; liest nur den Kopf."""
    from PIL import Image

    try:
        with Image.open(path) as im:
            orientation = int(im.getexif().get(0x0112) or 1)
            width, height = im.size
    except Exception as e:
        logger.debug("Ausrichtung nicht lesbar (%s): %s", path, e)
        return None
    return orientation, width, height


@dataclass
class Plan:
    """Was fuer ein Foto geschehen soll -- im Trockenlauf das Ergebnis."""

    point_id: Any
    photo_id: str
    path: str
    orientation: int
    content_sha256: Optional[str]
    new_faces: list[dict] = field(default_factory=list)
    old_ids: list[str] = field(default_factory=list)
    named_old: int = 0
    transferred: list[tuple[str, float]] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    #: In welchem Raum die gespeicherten Kaesten lagen (`choose_frame`).
    frame: str = ""
    #: Alte und neue Kaesten decken sich eins zu eins: nur stempeln.
    stamp_only: bool = False


def old_faces(client, faces_collection: str, photo_id: str) -> list:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    out, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=faces_collection,
            scroll_filter=Filter(must=[FieldCondition(key="photo_id",
                                                      match=MatchValue(value=photo_id))]),
            limit=BATCH, offset=offset, with_payload=True, with_vectors=False,
        )
        out.extend(p for p in batch if (p.payload or {}).get("frame_ss") is None)
        if offset is None:
            return out


def plan_photo(client, embedder, point, faces_collection: str = "faces",
               min_iou: float = MIN_IOU) -> tuple[str, Optional[Plan]]:
    """Befund und Plan fuer einen Foto-Punkt. Schreibt nichts."""
    payload = point.payload or {}
    path = payload.get("file_path") or ""
    if not path or (payload.get("kind") or "photo") != "photo" or is_video(path):
        return "kein Foto", None
    if payload.get(MARKER):
        return "schon neu erkannt", None
    head = read_orientation(path)
    if head is None:
        return "nicht lesbar", None
    orientation, width, height = head
    if orientation == 1:
        return "Ausrichtung 1", None
    if orientation not in range(2, 9):
        return "Ausrichtung unbekannt", None

    photo_id = uid_of(payload)
    old = old_faces(client, faces_collection, photo_id)
    detected = embedder.process(path).get("faces") or []
    plan = Plan(point_id=point.id, photo_id=photo_id, path=path, orientation=orientation,
                content_sha256=payload.get("content_sha256"))
    plan.new_faces = [dict(f) for f in detected]
    plan.old_ids = [str(p.id) for p in old]

    # Alle alten Gesichter paaren, nicht nur die benannten: sonst greift ein
    # benanntes nach dem Kasten seines unbenannten Nachbarn.
    stored = [(p.payload or {}).get("box") or [0, 0, 0, 0] for p in old]
    new_boxes = [f.get("box") or [0, 0, 0, 0] for f in plan.new_faces]
    plan.frame, pairs = choose_frame(stored, new_boxes, orientation, width, height, min_iou)
    plan.named_old = sum(1 for p in old if (p.payload or {}).get("person_id"))
    if plan.frame == AS_IS and len(pairs) == len(old) == len(plan.new_faces):
        # Jeder alte Kasten hat seinen neuen, keiner kommt dazu: der Index
        # stimmt schon, nur der Stempel fehlt. Nichts neu schreiben.
        plan.stamp_only = True
        plan.transferred = [((p.payload or {}).get("person_name")
                             or (p.payload or {}).get("person_id"), pairs[i][1])
                            for i, p in enumerate(old) if (p.payload or {}).get("person_id")]
        return "Kaesten stimmen schon", plan
    for i, p in enumerate(old):
        pl = p.payload or {}
        if not pl.get("person_id"):
            continue
        hit = pairs.get(i)
        if hit is None:
            plan.lost.append(pl.get("person_name") or pl.get("person_id"))
            continue
        j, score = hit
        plan.new_faces[j]["person_id"] = pl.get("person_id")
        plan.new_faces[j]["person_name"] = pl.get("person_name")
        plan.transferred.append((pl.get("person_name") or pl.get("person_id"), score))
    return ("Namen gingen verloren" if plan.lost else "neu erkannt"), plan


def apply_plan(client, plan: Plan, collection: str = "photos",
               faces_collection: str = "faces") -> None:
    """Neue Gesichter schreiben, alte loeschen, Foto nachziehen."""
    from qdrant_client.models import PointIdsList

    if plan.stamp_only:
        client.set_payload(collection_name=collection, payload={MARKER: plan.orientation},
                           points=[plan.point_id], wait=True)
        return

    writer = QdrantWriter.on(client, collection, faces_collection)
    record = SimpleNamespace(photo_id=plan.photo_id, file_path=plan.path,
                             content_sha256=plan.content_sha256, faces=plan.new_faces)
    writer.upsert_faces(record)

    new_ids = []
    pairs: dict[str, str] = {}
    for i, face in enumerate(plan.new_faces):
        if not face.get("embedding"):
            continue
        fid = QdrantWriter.face_point_id(plan.photo_id, i, face.get("box"))
        new_ids.append(fid)
        pid = face.get("person_id")
        if pid:
            # `upsert_faces` behaelt Namen nur fuer dieselbe Punkt-ID; die
            # neuen Kaesten sind neue Punkte.
            client.set_payload(collection_name=faces_collection,
                               payload={"person_id": pid,
                                        "person_name": face.get("person_name")},
                               points=[fid], wait=True)
            if not pid.startswith("_"):
                pairs.setdefault(pid, face.get("person_name") or pid)

    gone = [fid for fid in plan.old_ids if fid not in set(new_ids)]
    if gone:
        client.delete(collection_name=faces_collection,
                      points_selector=PointIdsList(points=gone), wait=True)

    ids = sorted(pairs)
    client.set_payload(
        collection_name=collection,
        payload={
            "face_count": len(new_ids),
            "face_boxes": [f.get("box") or [] for f in plan.new_faces if f.get("embedding")],
            # Wie `sync_photo_persons`: aus den Gesichtern abgeleitet.
            "person_ids": ids,
            "person_names": [pairs[k] for k in ids],
            MARKER: plan.orientation,
        },
        points=[plan.point_id],
        wait=True,
    )


# --------------------------------------------------------------------------
# Lauf
# --------------------------------------------------------------------------

def run(client, embedder, apply: bool = False, collection: str = "photos",
        faces_collection: str = "faces", limit: Optional[int] = None,
        allow_lost_names: bool = False, min_iou: float = MIN_IOU,
        sample: int = 5) -> Counter:
    tally: Counter = Counter()
    examples: list[str] = []
    seen = 0
    offset = None
    stop = False
    while not stop:
        batch, offset = client.scroll(
            collection_name=collection, limit=BATCH, offset=offset,
            with_payload=PHOTO_FIELDS, with_vectors=False,
        )
        for point in batch:
            if limit and not apply and seen >= limit:
                stop = True
                break
            seen += 1
            verdict, plan = plan_photo(client, embedder, point, faces_collection, min_iou)
            tally[verdict] += 1
            if plan is None:
                continue
            tally["Namen uebertragen"] += len(plan.transferred)
            tally["Namen ohne Partner"] += len(plan.lost)
            if plan.old_ids:
                tally[f"alte Kaesten {plan.frame}"] += 1
            line = (f"{plan.path}  Ausrichtung {plan.orientation}, Kaesten {plan.frame}: "
                    f"{len(plan.old_ids)} -> {len(plan.new_faces)} Gesichter, "
                    f"{len(plan.transferred)}/{plan.named_old} Namen")
            if plan.lost:
                line += f", verloren: {', '.join(map(str, plan.lost))}"
            if plan.lost or len(examples) < sample:
                examples.append(line)
            if not apply:
                continue
            if plan.lost and not allow_lost_names:
                tally["nicht geschrieben (Namen)"] += 1
                continue
            apply_plan(client, plan, collection, faces_collection)
            if plan.stamp_only:
                tally["nur gestempelt"] += 1
                continue
            tally["geschrieben"] += 1
            if limit and tally["geschrieben"] >= limit:
                stop = True
                break
        if offset is None:
            break

    logger.info("%s ueber %d Fotos:", "Angewandt" if apply else "Trockenlauf", seen)
    for key, n in tally.most_common():
        logger.info("   %-28s %6d", key, n)
    for line in examples:
        logger.info("     %s", line)
    if not apply and tally.get("neu erkannt", 0) + tally.get("Namen gingen verloren", 0):
        logger.info("Mit --apply werden die Fotos unter \"neu erkannt\" geschrieben, "
                    "\"Kaesten stimmen schon\" nur gestempelt; "
                    "\"Namen gingen verloren\" nur mit --allow-lost-names.")
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--collection", default=os.environ.get("PHOTOVAULT_COLLECTION", "photos"))
    parser.add_argument("--faces-collection", default="faces")
    parser.add_argument("--model-dir", default=os.environ.get(
        "MODEL_DIR", os.path.expanduser("~/.cache/photovault-models")))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Nur erkennen und berichten, nichts schreiben (Standard).")
    mode.add_argument("--apply", action="store_true",
                      help="Tatsaechlich schreiben.")
    parser.add_argument("--allow-lost-names", action="store_true",
                        help="Auch Fotos schreiben, auf denen ein benanntes Gesicht "
                             "keinen Partner findet (der Name geht dort verloren).")
    parser.add_argument("--min-iou", type=float, default=MIN_IOU)
    parser.add_argument("--limit", type=int, default=None,
                        help="Trockenlauf: so viele Fotos ansehen; --apply: so viele schreiben")
    parser.add_argument("--sample", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from qdrant_client import QdrantClient

    from ingest.face_embedder import FaceEmbedder

    run(QdrantClient(url=args.qdrant_url), FaceEmbedder(args.model_dir),
        apply=args.apply, collection=args.collection,
        faces_collection=args.faces_collection, limit=args.limit,
        allow_lost_names=args.allow_lost_names, min_iou=args.min_iou,
        sample=args.sample)


if __name__ == "__main__":
    main()
