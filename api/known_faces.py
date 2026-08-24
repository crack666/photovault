"""Unbenannte Gesichter, die zu bereits benannten Personen gehören.

Nach 93 benannten Personen liegen **4868 von 16722** unbenannten Gesichtern
innerhalb von 0,50 Kosinus-Ähnlichkeit an einer dieser Personen — knapp ein
Drittel des Stapels ist gar nicht unbekannt.

Die gehören nicht in die Cluster-Ansicht. Dort erscheinen sie als dutzende
kleiner Gruppen derselben Person, und jede einzeln zu benennen ist Arbeit für
eine Antwort, die längst gegeben wurde. Sinnvoller ist eine Rückfrage je
Person: „Ist das Sophie Meyer? 700 Gesichter."

Bewusst nur ein **Vorschlag**, nie eine automatische Zuordnung: ein falsch
zugeordnetes Gesicht verfälscht danach jede Suche und jede Caption, und
niemand würde es je bemerken.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Ab hier gilt ein Gesicht als Kandidat. Gemessen am Bestand: 0,50 erfasst
#: 29 % der unbenannten Gesichter, 0,60 noch 21,5 %. Tiefer zu gehen bringt
#: kaum mehr und holt Fremde herein.
DEFAULT_THRESHOLD = 0.50

#: Unter so vielen Kandidaten lohnt keine eigene Rückfrage -- die gehen den
#: gewohnten Weg über die Cluster.
MIN_BATCH = 3

#: Keine echten Personen, sondern Ablagen für Aussortiertes. Sie taugen nicht
#: als Rückfrage ("Ist das Übersprungen?"), und ihre Mittelvektoren sind
#: bedeutungslos: dort liegen Ohren, Hinterköpfe und Unscharfes beieinander,
#: die einander nur darin ähneln, kein Gesicht zu sein.
PSEUDO_PERSONS = frozenset({"_ignored", "_skipped"})


def _norm(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    return vec / n if n else vec


def centroids(labeled: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], np.ndarray]:
    """Je Person einen Mittelvektor aus ihren bestätigten Gesichtern."""
    grouped: dict[tuple[str, str], list[np.ndarray]] = defaultdict(list)
    for item in labeled:
        payload = item.get("payload") or {}
        pid = payload.get("person_id")
        if not pid or pid in PSEUDO_PERSONS:
            continue
        vec = item.get("vector")
        if vec is None:
            continue
        grouped[(pid, payload.get("person_name") or pid)].append(
            _norm(np.asarray(vec, dtype=np.float32))
        )
    if not grouped:
        return [], np.zeros((0, 1), dtype=np.float32)
    keys = list(grouped)
    mat = np.stack([_norm(np.mean(np.stack(grouped[k]), axis=0)) for k in keys])
    return keys, mat


def candidates(
    unlabeled: list[dict[str, Any]],
    labeled: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
    min_batch: int = MIN_BATCH,
) -> list[dict[str, Any]]:
    """Kandidaten je Person, größte Gruppe zuerst.

    Jedes Gesicht geht an höchstens eine Person -- die ähnlichste. Sonst
    tauchte dasselbe Gesicht in mehreren Rückfragen auf, und zwei „Ja" wären
    ein Widerspruch, den niemand aufloest.
    """
    keys, mat = centroids(labeled)
    if not keys or not unlabeled:
        return []

    usable = [it for it in unlabeled if it.get("vector") is not None]
    if not usable:
        return []
    vecs = np.stack([_norm(np.asarray(it["vector"], dtype=np.float32)) for it in usable])
    sim = vecs @ mat.T
    best_idx = sim.argmax(axis=1)
    best_val = sim.max(axis=1)

    buckets: dict[int, list[tuple[float, dict]]] = defaultdict(list)
    for item, idx, score in zip(usable, best_idx, best_val):
        if score >= threshold:
            buckets[int(idx)].append((float(score), item))

    out = []
    for idx, rows in buckets.items():
        if len(rows) < min_batch:
            continue
        rows.sort(key=lambda r: -r[0])
        pid, name = keys[idx]
        out.append({
            "person_id": pid,
            "name": name,
            "count": len(rows),
            # Die aehnlichsten zuerst: wer die Rueckfrage oben bestaetigt,
            # sieht sofort, ob die Gruppe stimmt.
            "faces": [{"face_id": str(item.get("id")), "score": round(score, 3),
                       "payload": item.get("payload") or {}}
                      for score, item in rows],
            "best": round(rows[0][0], 3),
            "worst": round(rows[-1][0], 3),
        })
    out.sort(key=lambda b: -b["count"])
    return out
