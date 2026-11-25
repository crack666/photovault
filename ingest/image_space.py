"""Wo der Bildvektor liegt -- und welcher gerade gelesen wird (#66).

Der CLIP-Vektor (768 d) liegt als `clip` in `photos`. SigLIP 2 (1152 d)
bekommt eine **Beistell-Collection** `photos_siglip2` mit denselben
Punkt-IDs, einem Vektor `siglip2` und einer kleinen Payload (`kind`,
`scene_tags` des Modells).

Warum nicht ein vierter Vektor in `photos`: Qdrant kann einer bestehenden
Collection keinen benannten Vektor hinzufuegen (gemessen 12.11.2026 an
Qdrant 1.20: `PATCH /collections/x` mit neuem Namen antwortet `Not existing
vector name error`). Es bliebe nur, `photos` neu anzulegen und umzukopieren
-- mit allen Namen, Notizen und Gesichtern des Menschen darin, und einem
Fenster, in dem Schreibzugriffe verloren gehen. Die Beistell-Collection
laesst `photos` unberuehrt, und der alte CLIP-Vektor bleibt, wo er ist.

Gelesen wird der neue Raum erst, wenn er **voll** ist (`active`): solange
der Re-Embed in Etappen laeuft, saehe eine Aehnlichkeitssuche im neuen Raum
nur einen Teil des Bestands.

**Geschrieben wird, was gelesen wird** (`ingest_key`): ein Ingest rechnet mit
dem Modell des gerade gelesenen Raums. Rechnete er fest mit SigLIP 2, stuende
auf einer Installation mit altem Bestand jedes neue Foto in einem Raum, aus
dem noch niemand liest -- unsichtbar fuer "Mehr davon" und fuer die Karte.
Nur ein leerer Index faengt gleich mit SigLIP 2 an, wenn eine Grafikkarte da
ist. Alte Bestaende wechseln ueber den Lauf "Bildsuche auf SigLIP 2
umstellen" (Jobs) und damit als Ganzes.
"""
from __future__ import annotations

import logging
import time

from ingest.scene_tagger import IMAGE_MODELS, ImageModel, image_model

logger = logging.getLogger(__name__)

#: Ab diesem Anteil der Punkte mit CLIP-Vektor gilt der neue Raum als voll.
#: Nicht 1.0: ein einzelnes Foto, das nach dem Re-Embed neu hinzukommt,
#: soll die Suche nicht auf CLIP zurueckwerfen. Fehlende zieht
#: `python -m tools.reembed_image` nach.
FULL_SHARE = 0.98

#: Wie lange `active` sein Ergebnis wiederverwendet. Zwei `count` je
#: Aehnlichkeitsanfrage waeren billig, aber nicht umsonst.
CACHE_S = 60.0

_cache: dict[str, tuple[float, ImageModel]] = {}


def ensure_collection(qc, spec: ImageModel) -> None:
    """Die Beistell-Collection anlegen, falls sie fehlt; Groesse pruefen."""
    from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

    if spec.collection == "photos":
        return
    try:
        info = qc.get_collection(spec.collection)
    except Exception:
        logger.info("Creating collection '%s' (%s, %d d)...", spec.collection, spec.vector, spec.dim)
        qc.create_collection(
            collection_name=spec.collection,
            vectors_config={spec.vector: VectorParams(size=spec.dim, distance=Distance.COSINE)},
        )
        for field in ("kind", "scene_tags"):
            try:
                qc.create_payload_index(spec.collection, field_name=field,
                                        field_schema=PayloadSchemaType.KEYWORD)
            except Exception as e:  # pragma: no cover -- Index ist Beiwerk
                logger.debug("Index %s: %s", field, e)
        return
    vectors = getattr(info.config.params, "vectors", None) or {}
    cfg = vectors.get(spec.vector) if isinstance(vectors, dict) else None
    size = getattr(cfg, "size", None)
    if size != spec.dim:
        raise RuntimeError(
            f"Collection '{spec.collection}': Vektor '{spec.vector}' hat Groesse {size}, "
            f"erwartet {spec.dim}."
        )


def write(qc, spec: ImageModel, items: list[tuple[str, list[float], dict]]) -> None:
    """[(Punkt-ID, Vektor, Payload)] in den Raum des Modells schreiben.

    `photos`: nur der Vektor (`update_vectors`), die Payload dort gehoert
    dem Ingest. Beistell-Collection: Punkt samt kleiner Payload.
    """
    if not items:
        return
    if spec.collection == "photos":
        from qdrant_client.models import PointVectors

        qc.update_vectors(collection_name="photos", wait=True, points=[
            PointVectors(id=pid, vector={spec.vector: vec}) for pid, vec, _pl in items
        ])
        return
    from qdrant_client.models import PointStruct

    qc.upsert(collection_name=spec.collection, wait=True, points=[
        PointStruct(id=pid, vector={spec.vector: vec}, payload=pl) for pid, vec, pl in items
    ])


def existing_ids(qc, spec: ImageModel) -> set[str]:
    """Punkt-IDs, die im Raum des Modells schon einen Vektor haben."""
    from qdrant_client.models import Filter, HasVectorCondition

    flt = Filter(must=[HasVectorCondition(has_vector=spec.vector)])
    out, offset = set(), None
    try:
        qc.get_collection(spec.collection)
    except Exception:
        return out
    while True:
        batch, offset = qc.scroll(collection_name=spec.collection, scroll_filter=flt,
                                  limit=2048, offset=offset, with_payload=False,
                                  with_vectors=False)
        out.update(str(p.id) for p in batch)
        if offset is None:
            return out


def forget(qc, ids: list[str]) -> None:
    """Geloeschte Fotos auch aus den Beistell-Collections nehmen.

    Zeigen kann die Suche sie ohnehin nicht (`similar` gleicht gegen `photos`
    ab), aber sie belegten Plaetze in der Trefferliste und zaehlten fuer die
    Abdeckung mit -- und ein geloeschtes Foto soll keinen Vektor zuruecklassen.
    Fehler sind hier Beiwerk: das Loeschen selbst ist schon geschehen.
    """
    if not ids:
        return
    for spec in IMAGE_MODELS.values():
        if spec.collection == "photos":
            continue
        try:
            if not qc.collection_exists(spec.collection):
                continue
            for i in range(0, len(ids), 256):
                qc.delete(collection_name=spec.collection,
                          points_selector=[str(x) for x in ids[i:i + 256]], wait=True)
        except Exception as e:
            logger.warning("%s: Vektoren geloeschter Fotos bleiben stehen: %s", spec.collection, e)


def fill(qc, spec: ImageModel) -> tuple[int, int]:
    """(Punkte mit Vektor im neuen Raum, Punkte mit CLIP-Vektor in `photos`).

    Punkte ohne CLIP-Vektor (nach dem Wechsel eingelesen) zaehlen nur links:
    sie koennen im alten Raum nicht fehlen.
    """
    from qdrant_client.models import Filter, HasVectorCondition

    base = qc.count(collection_name="photos", exact=True,
                    count_filter=Filter(must=[HasVectorCondition(has_vector="clip")])).count
    if spec.collection == "photos":
        return base, base
    try:
        have = qc.count(collection_name=spec.collection, exact=True).count
    except Exception:
        have = 0
    return have, base


def active(qc, preferred: str | None = None, *, now: float | None = None) -> ImageModel:
    """Der Bildraum, aus dem Aehnlichkeitssuche und Atlas lesen.

    Das eingestellte Modell (`PHOTOVAULT_IMAGE_MODEL`, sonst SigLIP 2),
    sobald sein Raum voll ist -- sonst CLIP. Faellt Qdrant beim Zaehlen
    aus, ist CLIP die sichere Antwort: den Raum gibt es sicher.
    """
    key = preferred or _preferred_key()
    spec = image_model(key)
    if spec.collection == "photos":
        return spec
    t = time.monotonic() if now is None else now
    hit = _cache.get(spec.key)
    if hit and t - hit[0] < CACHE_S:
        return hit[1]
    try:
        have, base = fill(qc, spec)
        # `have` zuerst: ein leerer Raum ist nie voll (0 >= 0), ein Raum ohne
        # CLIP-Gegenstueck (frische Installation, gleich SigLIP 2) dagegen schon.
        chosen = spec if have and have >= FULL_SHARE * base else IMAGE_MODELS["clip"]
    except Exception as e:
        logger.warning("Bildraum %s nicht zaehlbar, lese clip: %s", spec.key, e)
        chosen = IMAGE_MODELS["clip"]
    _cache[spec.key] = (t, chosen)
    return chosen


def ingest_key(qc, collection: str = "photos") -> str:
    """Mit welchem Bildmodell ein Ingest rechnet: Umgebung, sonst der gelesene Raum.

    Siehe Modulkopf. Geht beim Nachsehen etwas schief, ist es CLIP wie bisher.
    """
    import os

    if os.environ.get("PHOTOVAULT_IMAGE_MODEL"):
        return image_model().key
    if collection != "photos":
        return "clip"
    try:
        # Frisch zaehlen: ein Ingest entscheidet einmal fuer seinen ganzen Lauf.
        _cache.clear()
        key = active(qc).key
        if key != "clip":
            return key
        leer = qc.count(collection_name="photos", exact=True).count == 0
    except Exception as e:
        logger.warning("Bildraum nicht bestimmbar, rechne mit clip: %s", e)
        return "clip"
    return "siglip2" if leer and _cuda() else "clip"


def _cuda() -> bool:
    """Ohne Grafikkarte bleibt es bei CLIP: SigLIP 2 So400m ist auf dem
    Prozessor ein Mehrfaches langsamer (docs/performance.md)."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _preferred_key() -> str:
    """Fuers Lesen: die Umgebung, sonst SigLIP 2 -- es gewinnt ueberall (#59).

    SigLIP 2 ist hier ohne Risiko die Vorgabe: gelesen wird es ohnehin erst,
    wenn es voll ist.
    """
    import os

    return os.environ.get("PHOTOVAULT_IMAGE_MODEL") or "siglip2"
