"""Gesichter zu Personen gruppieren.

Der fruehere Ansatz war ein greedy Single-Pass: jedes Gesicht wandert in den
erstbesten Cluster, dessen Zentroid nahe genug liegt. Das hat zwei Fehler, die
sich nicht ueber den Schwellwert beheben lassen -- es ist reihenfolgeabhaengig,
und zwei Cluster derselben Person finden nie zusammen, weil Cluster nie
verglichen werden. An 1288 Gesichtern der NAS-Stichprobe ergab das 856 Cluster
mit 671 Einzelgaengern, bei realistisch 30-60 Personen.

Stattdessen agglomerativ mit Average-Linkage: alle Cluster werden paarweise
verglichen, das aehnlichste Paar verschmilzt, und das wiederholt sich, bis kein
Paar mehr ueber dem Schwellwert liegt. Average-Linkage (statt Single-Linkage)
verhindert Ketten, bei denen A-B und B-C nahe sind, A und C aber nicht.

Zusaetzlich fliegen winzige und unsichere Detektionen vorher raus: ein 20-Pixel-
Gesicht im Hintergrund liefert kein brauchbares Embedding und verwaessert sonst
jeden Cluster, in dem es landet.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.35
#: Detektionen darunter sind fuer eine Identitaet nicht belastbar.
MIN_DET_SCORE = 0.55
MIN_FACE_PX = 40
#: Obergrenze fuer die O(n^2)-Matrix. Darueber wird nach Qualitaet vorsortiert.
#:
#: 6000 war noetig, solange jeder Merge das Maximum ueber die ganze Matrix
#: suchte -- das ist O(n^3). Mit dem Zeilenmaximum unten sind 17000 Gesichter
#: in 5 s geclustert; die Grenze schuetzt jetzt nur noch vor dem Speicher
#: (n^2 float32: 25000 Gesichter sind 2,3 GiB).
MAX_FACES = 25000


def _norm(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    return vec if n < 1e-9 else vec / n


def _face_px(payload: dict[str, Any]) -> float:
    box = payload.get("box") or []
    if len(box) != 4:
        return 0.0
    w = max(0.0, float(box[2]) - float(box[0]))
    h = max(0.0, float(box[3]) - float(box[1]))
    return min(w, h)


def usable(item: dict[str, Any], min_score: float, min_px: int) -> bool:
    payload = item.get("payload") or {}
    score = payload.get("score")
    if score is not None and float(score) < min_score:
        return False
    px = _face_px(payload)
    if px and px < min_px:
        return False
    return bool(item.get("vector"))


def cluster_faces(
    items: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
    min_score: float = MIN_DET_SCORE,
    min_px: int = MIN_FACE_PX,
    max_faces: int = MAX_FACES,
) -> list[dict[str, Any]]:
    """Each item: {id, vector, payload}. Returns clusters largest-first.

    Jeder Cluster: {centroid, members}. Die Reihenfolge der Eingabe hat keinen
    Einfluss auf das Ergebnis.
    """
    usable_items = [it for it in items if usable(it, min_score, min_px)]
    if not usable_items:
        return []

    if len(usable_items) > max_faces:
        usable_items.sort(
            key=lambda it: (
                -(float((it.get("payload") or {}).get("score") or 0.0)),
                -_face_px(it.get("payload") or {}),
            )
        )
        logger.warning(
            "Clustering limited to the %d strongest of %d faces",
            max_faces,
            len(usable_items),
        )
        usable_items = usable_items[:max_faces]

    # Stabile, eingabeunabhaengige Reihenfolge.
    usable_items.sort(key=lambda it: str(it.get("id")))
    vectors = np.stack([_norm(np.asarray(it["vector"], dtype=np.float32)) for it in usable_items])
    n = len(usable_items)

    if n == 1:
        return [{"centroid": vectors[0], "members": [usable_items[0]]}]

    sim = (vectors @ vectors.T).astype(np.float32)
    np.fill_diagonal(sim, -np.inf)

    members: list[list[int]] = [[i] for i in range(n)]
    sizes = np.ones(n, dtype=np.float32)
    active = np.ones(n, dtype=bool)

    # Je Zeile das beste Gegenstueck merken. Ohne das kostet jeder Merge eine
    # Suche ueber die ganze Matrix -- bei 17000 Gesichtern das Dreifache an
    # Groessenordnung und der Grund, warum frueher nur 6000 geclustert wurden.
    row_best = np.argmax(sim, axis=1)
    row_val = sim[np.arange(n), row_best].copy()

    while True:
        candidates = np.where(active, row_val, -np.inf)
        a = int(np.argmax(candidates))
        if float(candidates[a]) < threshold:
            break
        b = int(row_best[a])
        if a > b:
            a, b = b, a

        # Average-Linkage nach Lance-Williams: gewichtetes Mittel der beiden Zeilen.
        merged = (sim[a] * sizes[a] + sim[b] * sizes[b]) / (sizes[a] + sizes[b])
        merged[a] = merged[b] = -np.inf
        merged[~active] = -np.inf

        sim[a, :] = merged
        sim[:, a] = merged
        sim[a, a] = -np.inf
        sim[b, :] = -np.inf
        sim[:, b] = -np.inf

        members[a].extend(members[b])
        members[b] = []
        sizes[a] += sizes[b]
        active[b] = False

        row_best[a] = int(np.argmax(sim[a]))
        row_val[a] = sim[a, row_best[a]]
        # Nur Zeilen, deren Favorit gerade verschwunden ist, muessen neu suchen.
        for i in np.where(active & ((row_best == a) | (row_best == b)))[0]:
            if i == a:
                continue
            row_best[i] = int(np.argmax(sim[i]))
            row_val[i] = sim[i, row_best[i]]

    clusters: list[dict[str, Any]] = []
    for i in range(n):
        if not active[i] or not members[i]:
            continue
        idx = members[i]
        centroid = _norm(vectors[idx].mean(axis=0))
        clusters.append(
            {
                "centroid": centroid,
                "members": [usable_items[j] for j in idx],
            }
        )
    clusters.sort(key=lambda c: (-len(c["members"]), str(c["members"][0]["id"])))
    return clusters


def person_id_from_name(name: str) -> str:
    import re

    s = name.strip().lower()
    trans = str.maketrans(
        {
            "ä": "ae",
            "ö": "oe",
            "ü": "ue",
            "ß": "ss",
            "á": "a",
            "à": "a",
            "é": "e",
            "è": "e",
        }
    )
    s = s.translate(trans)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "person"
