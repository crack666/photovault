"""Die Karte des Archivs rechnen.

Erzeugt aus den Bildvektoren eine 2D-Landschaft: aehnliche Fotos liegen
nebeneinander, Kontinente bekommen einen Namen. Das Ergebnis ist eine
statische Datei unter ``web/static/atlas/`` -- die UI laedt sie direkt ueber
den vorhandenen StaticFiles-Mount, es braucht **keinen API-Neustart**.

    python -m tools.atlas_build                 # aus CLIP-Vektoren
    python -m tools.atlas_build --space text    # aus den grounded Textvektoren

Drei Entscheidungen stecken darin, alle an diesem Bestand gemessen:

**Layout aus CLIP, Beschriftung aus Captions.** Der CLIP-Vektor liegt fuer
jedes Foto vor und bedeutet „sieht aehnlich aus" -- das ist es, was eine Karte
raeumlich ausdruecken soll. Die `scene_tags` taugen dagegen nicht als
Beschriftung: an denselben Clustern gemessen nennen die Captions Ort, Anlass
und Personen („abistreich, schuelern, schulhof"), die Tags dagegen
`gruppenfoto, party, gruppe` -- und liegen mitunter daneben (ein Cluster aus
Apple-Store-Fotos war als `kinder, geburtstag, urlaub` getaggt).

**Cluster ohne Captions bekommen keinen erfundenen Namen.** Solange der
Vision-Lauf nicht durch ist, ist die Abdeckung ungleich verteilt. Ein Cluster,
dessen Beschriftung aus drei Prozent seiner Fotos stammt, sagt das (``cap_share``)
und faellt in der UI blass aus, statt Gewissheit vorzutaeuschen.

**Nahduplikate werden gestapelt, nicht geloescht.** BURST-Serien und
Feuerwerk-Salven liegen im Vektorraum praktisch aufeinander. Gestapelt
verschwinden sie aus der Uebersicht, bleiben aber im Index -- geloescht wird
nur, was ein Mensch bestaetigt.
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from api.qdrant_util import PHOTOS, client

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "static" / "atlas"
FORMAT_VERSION = 1

#: Ab hier gelten zwei Fotos als dieselbe Aufnahme. 0.95 traf an diesem
#: Bestand BURST-Serien und Salven, ohne benachbarte Motive einzusammeln;
#: 0.92 faengt mehr ein, faltet aber auch Aehnliches zusammen.
DUP_THRESHOLD = 0.95

#: Floskeln, die in fast jeder Caption stehen und deshalb nichts trennen.
#: Mit echten Umlauten -- die Captions schreiben „hält", nicht „haelt", eine
#: transkribierte Liste liefe ins Leere. Gemessen an 3 285 Captions.
STOPWORDS = set(
    """
    der die das den dem des ein eine einer eines einem und oder aber im in an auf mit von zu zum
    zur bei für ist sind war waren wird werden hat haben es sie er ihm ihr sich als am vor
    hinter neben über unter während dabei steht stehen sitzt sitzen hält halten trägt tragen
    person personen mann frau männer frauen junge junger jungen weitere weiteren anderen andere
    zwei drei vier fünf sechs sieben acht neun zehn vordergrund hintergrund bild foto aufnahme
    zeigt zeigen links rechts mitte oben unten dunkel hell keine dass deren dessen etwa mehrere
    gruppe ihre ihren seine seinen einige etwas nicht auch noch nur schon wieder sowie darunter
    blickt blicken schaut schauen lächelt lächeln posiert posieren befindet befinden liegt liegen
    sehen sieht erkennen erkennbar sichtbar sichtbare sichtbaren aufgenommen wurde wurden
    kamera hand hände gemeinsam jeweils scheint wirkt handelt trägt getragen weiteres
    """.split()
)

_RE_WORD = re.compile(r"[a-zA-ZÄÖÜäöüß]{4,}")

#: Zweites Netz unter der Wortliste: was in mehr als einem Fuenftel aller
#: Captions steht, ist Floskel. Als Schwelle *und* Liste, weil die Floskeln
#: vom Caption-Modell abhaengen -- eine reine Liste veraltet mit dem naechsten
#: Modell, eine reine Schwelle wuerde haeufige Familiennamen mitnehmen --
#: der haeufigste steht in 16 % aller Captions und traegt trotzdem Bedeutung.
MAX_DOC_FREQ = 0.20

# Bitmaske fuer den Zustands-Layer. Die UI faerbt danach ein.
FLAG_PERSON = 1 << 0  #: mindestens eine Person bestaetigt
FLAG_CAPTION = 1 << 1  #: Beschreibung vorhanden
FLAG_EXIF_DATE = 1 << 2  #: Datum aus EXIF, nicht geraten
FLAG_EVENT = 1 << 3  #: gehoert zu einer benannten Serie
FLAG_GPS = 1 << 4  #: Koordinaten vorhanden
FLAG_NO_CLOCK = 1 << 5  #: Datum ohne echte Uhrzeit
FLAG_FACES_UNNAMED = 1 << 6  #: Gesichter erkannt, keines benannt
FLAG_IN_STACK = 1 << 7  #: Teil eines Nahduplikat-Stapels
FLAG_STACK_HEAD = 1 << 8  #: das gezeigte Bild dieses Stapels


# --------------------------------------------------------------------------
# Laden
# --------------------------------------------------------------------------

def load_points(qc: Any, space: str, limit: int | None = None) -> tuple[np.ndarray, list[dict]]:
    """Vektoren und die Payload-Felder holen, die die Karte braucht.

    Fotos ohne den gewuenschten Vektor fallen raus -- sie koennen nicht
    platziert werden, und ein geratener Platz waere schlimmer als keiner.
    """
    vectors: list[list[float]] = []
    meta: list[dict] = []
    offset = None
    t0 = time.time()
    while True:
        batch, offset = qc.scroll(
            collection_name=PHOTOS,
            limit=512,
            offset=offset,
            with_payload=True,
            with_vectors=[space],
        )
        for point in batch:
            vec = (point.vector or {}).get(space)
            if not vec:
                continue
            payload = point.payload or {}
            vectors.append(vec)
            meta.append(
                {
                    "id": str(point.id),
                    "taken_at": payload.get("taken_at"),
                    "channel": payload.get("channel") or "?",
                    "caption": payload.get("caption_de") or "",
                    "tags": payload.get("scene_tags") or [],
                    "person_ids": payload.get("person_ids") or [],
                    "person_names": payload.get("person_names") or [],
                    "event_name": payload.get("event_name"),
                    "date_source": payload.get("date_source"),
                    "gps": payload.get("gps"),
                    "face_count": int(payload.get("face_count") or 0),
                    "folder": payload.get("folder_name") or "",
                }
            )
        if offset is None or (limit and len(meta) >= limit):
            break
    if limit:
        vectors, meta = vectors[:limit], meta[:limit]
    X = np.asarray(vectors, dtype=np.float32)
    logger.info("%d Punkte mit %s-Vektor in %.1fs", len(X), space, time.time() - t0)
    return X, meta


# --------------------------------------------------------------------------
# Projektion
# --------------------------------------------------------------------------

def project(X: np.ndarray, seed: int = 42, neighbors: int = 20, min_dist: float = 0.12) -> np.ndarray:
    """768d/2560d auf die Flaeche bringen.

    UMAP, weil es -- anders als t-SNE -- auch die grobe Anordnung der Cluster
    zueinander erhaelt. Bei einer Karte zaehlt genau das: der Betrachter soll
    sich merken koennen, wo etwas liegt.
    """
    try:
        import umap
    except ImportError as exc:  # pragma: no cover - Umgebungsfrage
        raise SystemExit(
            "umap-learn fehlt. Installieren mit:\n"
            "    pip install 'photovault[atlas]'\n"
            "  oder pip install umap-learn scikit-learn"
        ) from exc

    t0 = time.time()
    reducer = umap.UMAP(
        n_components=2,
        n_neighbors=neighbors,
        min_dist=min_dist,
        metric="cosine",
        random_state=seed,
        verbose=False,
    )
    coords = reducer.fit_transform(X).astype(np.float32)
    logger.info("UMAP in %.1fs", time.time() - t0)
    return coords


def to_unit(coords: np.ndarray) -> np.ndarray:
    """Auf 0..1 normieren, quadratisch -- die UI soll nicht rechnen muessen.

    Beide Achsen bekommen denselben Massstab, sonst verzerrt die Karte und
    Abstaende bedeuten in x etwas anderes als in y.
    """
    lo = coords.min(axis=0)
    span = float((coords.max(axis=0) - lo).max()) or 1.0
    out = (coords - lo) / span
    # zentrieren, damit die kuerzere Achse nicht am Rand klebt
    return out + (1.0 - out.max(axis=0)) / 2.0


# --------------------------------------------------------------------------
# Nahduplikate
# --------------------------------------------------------------------------

def find_stacks(Xn: np.ndarray, threshold: float, block: int = 2048) -> np.ndarray:
    """Fotos derselben Aufnahme zusammenfassen (Union-Find ueber Cosinus).

    Blockweise, weil eine volle 17k x 17k-Matrix gut ein Gigabyte waere. Nur
    das obere Dreieck wird betrachtet -- jedes Paar genau einmal.
    """
    n = len(Xn)
    parent = np.arange(n, dtype=np.int32)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    t0 = time.time()
    for start in range(0, n, block):
        sims = Xn[start : start + block] @ Xn.T
        for row_i in range(sims.shape[0]):
            gi = start + row_i
            hits = np.nonzero(sims[row_i, gi + 1 :] >= threshold)[0] + gi + 1
            for j in hits:
                a, b = find(gi), find(int(j))
                if a != b:
                    parent[max(a, b)] = min(a, b)
    roots = np.array([find(i) for i in range(n)], dtype=np.int32)
    sizes = collections.Counter(roots.tolist())
    stacked = sum(v for v in sizes.values() if v > 1)
    logger.info(
        "Stapel: %d Gruppen, %d Fotos gefaltet (%.1f%%) in %.1fs",
        sum(1 for v in sizes.values() if v > 1),
        stacked,
        100 * stacked / max(n, 1),
        time.time() - t0,
    )
    return roots


def pick_stack_heads(roots: np.ndarray, meta: list[dict]) -> set[int]:
    """Aus jedem Stapel das Bild waehlen, das oben liegt.

    Bevorzugt wird, was am meisten Kontext traegt: bestaetigte Personen, dann
    eine Caption, dann die eigene Aufnahme statt der herumgereichten Kopie.
    Sonst saehe man die WhatsApp-Version des eigenen Fotos.
    """
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(roots.tolist()):
        groups[r].append(i)

    def score(i: int) -> tuple:
        m = meta[i]
        return (
            len(m["person_ids"]) > 0,
            bool(m["caption"]),
            m["channel"] == "camera",
            m["date_source"] == "exif",
            m["face_count"],
        )

    return {max(members, key=score) for members in groups.values()}


# --------------------------------------------------------------------------
# Kontinente benennen
# --------------------------------------------------------------------------

def kmeans_clusters(coords_hi: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA

    reduced = PCA(n_components=min(50, coords_hi.shape[1]), random_state=seed).fit_transform(coords_hi)
    return KMeans(n_clusters=k, n_init=4, random_state=seed).fit_predict(reduced)


def label_clusters(labels: np.ndarray, meta: list[dict], k: int, top_n: int = 4) -> list[dict]:
    """Jedem Kontinent Woerter geben -- aus den Captions, per tf-idf.

    tf-idf und nicht blosse Haeufigkeit, weil sonst jeder Cluster „kinder"
    hiesse: gesucht ist, was *diesen* Haufen von den anderen unterscheidet.

    Die Dokumentfrequenz wird ueber die **einzelnen Captions** geschaetzt, nicht
    ueber die 40 Cluster-Klumpen. Bei nur 40 Dokumenten ist jedes Fuellwort
    selten genug, um als kennzeichnend durchzugehen -- ein Cluster hiess
    daraufhin „liegt". Ueber ein paar tausend Captions gemessen faellt so
    etwas von selbst heraus, und die Stoppwortliste muss nicht jede
    Verbform kennen.
    """
    # Termzaehlung von Hand: die Captions sind deutsch, und die
    # Standard-Tokenizer zerlegen Umlaute unterschiedlich je nach Version.
    per_caption: list[tuple[int, collections.Counter]] = []
    captioned = np.zeros(k, dtype=np.int32)
    for i, m in enumerate(meta):
        if not m["caption"]:
            continue
        words = [w for w in _RE_WORD.findall(m["caption"].lower()) if w not in STOPWORDS]
        if words:
            per_caption.append((int(labels[i]), collections.Counter(words)))
        captioned[labels[i]] += 1

    doc_freq: collections.Counter = collections.Counter()
    for _, cnt in per_caption:
        doc_freq.update(cnt.keys())
    n_docs = max(len(per_caption), 1)

    # Wie oft steht ein Wort in den Captions dieses Clusters?
    hits: list[collections.Counter] = [collections.Counter() for _ in range(k)]
    n_caps = [0] * k
    for cluster, cnt in per_caption:
        n_caps[cluster] += 1
        hits[cluster].update(cnt.keys())

    out: list[dict] = []
    for c in range(k):
        size = int((labels == c).sum())
        local = n_caps[c] or 1
        scored = []
        for term, seen_here in hits[c].items():
            # Ein Wort aus einer einzigen Caption beschreibt ein Foto, keinen
            # Kontinent -- und unter 5 % der Captions ist es Beifang.
            if seen_here < 3 or seen_here / local < 0.05:
                continue
            p_all = doc_freq[term] / n_docs
            if p_all > MAX_DOC_FREQ:
                continue
            p_here = seen_here / local
            # Gewichtete pointwise mutual information: haeufig *hier* und
            # zugleich selten anderswo. Floskeln wie „aufgenommen" stehen
            # ueberall gleich oft, ihr Verhaeltnis ist 1 und faellt auf 0.
            scored.append((p_here * math.log(p_here / p_all), term))
        scored.sort(reverse=True)
        out.append(
            {
                "terms": [t for _, t in scored[:top_n]],
                "cap_share": round(int(captioned[c]) / max(size, 1), 3),
            }
        )
    return out


def fallback_terms(labels: np.ndarray, meta: list[dict], c: int) -> list[str]:
    """Notnagel fuer Cluster ohne Captions: die haeufigsten Szenen-Tags.

    Bewusst schwaecher gewichtet -- die Tags sind grob und gelegentlich falsch.
    """
    idx = np.nonzero(labels == c)[0]
    tags = collections.Counter(t for i in idx for t in meta[i]["tags"])
    return [t for t, _ in tags.most_common(3)]


# --------------------------------------------------------------------------
# Ereignisse
# --------------------------------------------------------------------------

def build_events(meta: list[dict], coords: np.ndarray) -> tuple[list[dict], list[int]]:
    """Dieselben Serien wie im Tab „Serien", aber als Punkte auf der Karte.

    Der Maßstabswechsel ist der Punkt: 17 370 Einzelfotos sind nicht
    stöberbar, 1 222 Gelegenheiten schon. Ein Ereignis liegt dort, wo seine
    Fotos im Schnitt liegen -- eine Serie, die inhaltlich auseinanderfaellt,
    landet also zwischen den Kontinenten und faellt dadurch auf.
    """
    from ingest.events import cluster

    events = cluster((m["id"], m["taken_at"], m["channel"]) for m in meta)
    where = {m["id"]: i for i, m in enumerate(meta)}
    of_photo = [-1] * len(meta)
    out: list[dict] = []

    for ev in events:
        idx = [where[pid] for pid in ev.photo_ids if pid in where]
        if not idx:
            continue
        e = len(out)
        for i in idx:
            of_photo[i] = e
        centroid = coords[idx].mean(axis=0)
        # Streuung mitgeben: eine Serie, deren Fotos weit auseinanderliegen,
        # ist entweder gemischt oder falsch geschnitten. Das ist eine Aussage,
        # keine Deko.
        spread = float(np.sqrt(((coords[idx] - centroid) ** 2).sum(axis=1)).mean())
        cover = max(idx, key=lambda i: (
            bool(meta[i]["person_ids"]), bool(meta[i]["caption"]),
            meta[i]["channel"] == "camera", meta[i]["face_count"],
        ))
        names = collections.Counter(
            n for i in idx for n in meta[i]["person_names"]
        )
        title = next((meta[i]["event_name"] for i in idx if meta[i]["event_name"]), None)
        out.append({
            "i": e,
            "n": len(idx),
            "x": round(float(centroid[0]), 4),
            "y": round(float(centroid[1]), 4),
            "spread": round(spread, 4),
            "cover": meta[cover]["id"],
            "name": title,
            "channel": ev.channel,
            "start": ev.start.isoformat() if ev.start else None,
            "end": ev.end.isoformat() if ev.end else None,
            "day_level": bool(ev.day_level),
            "folder": collections.Counter(meta[i]["folder"] for i in idx).most_common(1)[0][0],
            "people": [n for n, _ in names.most_common(3)],
        })
    logger.info("Ereignisse: %d Serien ueber %d Fotos", len(out), sum(1 for e in of_photo if e >= 0))
    return out, of_photo


# --------------------------------------------------------------------------
# Zusammensetzen
# --------------------------------------------------------------------------

def photo_flags(m: dict, in_stack: bool, is_head: bool) -> int:
    flags = 0
    if m["person_ids"]:
        flags |= FLAG_PERSON
    if m["caption"]:
        flags |= FLAG_CAPTION
    if m["date_source"] == "exif":
        flags |= FLAG_EXIF_DATE
    if m["event_name"]:
        flags |= FLAG_EVENT
    if m["gps"]:
        flags |= FLAG_GPS
    if (m["taken_at"] or "").endswith("T00:00:00Z"):
        flags |= FLAG_NO_CLOCK
    if m["face_count"] and not m["person_ids"]:
        flags |= FLAG_FACES_UNNAMED
    if in_stack:
        flags |= FLAG_IN_STACK
        if is_head:
            flags |= FLAG_STACK_HEAD
    return flags


def day_number(taken_at: str | None) -> int:
    """Tag seit 1970 -- kompakter als ein ISO-String und reicht fuer die Achse."""
    if not taken_at:
        return -1
    try:
        dt = datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    except ValueError:
        return -1
    return int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).days)


def build(space: str, k: int, limit: int | None, dup_threshold: float, out_dir: Path) -> dict:
    qc = client()
    X, meta = load_points(qc, space, limit)
    if len(X) < k:
        raise SystemExit(f"Nur {len(X)} Punkte mit {space}-Vektor -- zu wenig fuer eine Karte.")

    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    coords = to_unit(project(Xn))
    roots = find_stacks(Xn, dup_threshold)
    heads = pick_stack_heads(roots, meta)
    sizes = collections.Counter(roots.tolist())

    events, event_of_photo = build_events(meta, coords)
    labels = kmeans_clusters(Xn, k)
    label_info = label_clusters(labels, meta, k)

    channels = sorted({m["channel"] for m in meta})
    chan_index = {c: i for i, c in enumerate(channels)}

    clusters = []
    for c in range(k):
        idx = np.nonzero(labels == c)[0]
        centroid = coords[idx].mean(axis=0)
        # Leitbild = das Foto, das dem Schwerpunkt am naechsten liegt und
        # nicht in einem Stapel versteckt ist.
        order = np.argsort(((coords[idx] - centroid) ** 2).sum(axis=1))
        cover = next((int(idx[o]) for o in order if int(idx[o]) in heads), int(idx[order[0]]))
        info = label_info[c]
        terms = info["terms"] or fallback_terms(labels, meta, c)
        years = collections.Counter(
            (meta[i]["taken_at"] or "")[:4] for i in idx if meta[i]["taken_at"]
        )
        clusters.append(
            {
                "i": c,
                "terms": terms,
                "cap_share": info["cap_share"],
                "from_tags": not info["terms"],
                "n": int(len(idx)),
                "x": round(float(centroid[0]), 4),
                "y": round(float(centroid[1]), 4),
                "cover": meta[cover]["id"],
                "years": [[y, n] for y, n in years.most_common(3)],
            }
        )

    # Personen als Index statt als Name je Foto: 114 Namen einmal, danach
    # kleine Zahlen. Ohne das waere ein Drittel der Datei Wiederholung.
    people = sorted({n for m in meta for n in m["person_names"]})
    person_index = {n: i for i, n in enumerate(people)}

    payload = {
        "version": FORMAT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "space": space,
        "n": len(meta),
        "dup_threshold": dup_threshold,
        "channels": channels,
        "persons": people,
        "clusters": clusters,
        "events": events,
        "ids": [m["id"] for m in meta],
        "x": [round(float(v), 4) for v in coords[:, 0]],
        "y": [round(float(v), 4) for v in coords[:, 1]],
        "t": [day_number(m["taken_at"]) for m in meta],
        "cl": [int(v) for v in labels],
        "ch": [chan_index[m["channel"]] for m in meta],
        "st": [int(r) if sizes[int(r)] > 1 else -1 for r in roots],
        "fl": [
            photo_flags(m, sizes[int(roots[i])] > 1, i in heads) for i, m in enumerate(meta)
        ],
        "fc": [min(m["face_count"], 255) for m in meta],
        "pe": [[person_index[n] for n in m["person_names"]] for m in meta],
        "ev": event_of_photo,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "atlas.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    logger.info("%s geschrieben (%.1f MB)", target, target.stat().st_size / 1e6)
    return payload


def report(payload: dict) -> None:
    n = payload["n"]
    flags = payload["fl"]

    def share(bit: int) -> str:
        hit = sum(1 for f in flags if f & bit)
        return f"{hit:>6} ({100 * hit / n:4.1f}%)"

    print(f"\nKarte: {n} Fotos aus {payload['space']}-Vektoren, {len(payload['clusters'])} Kontinente")
    print(f"  benannte Person   {share(FLAG_PERSON)}")
    print(f"  Beschreibung      {share(FLAG_CAPTION)}")
    print(f"  Datum aus EXIF    {share(FLAG_EXIF_DATE)}")
    print(f"  benannte Serie    {share(FLAG_EVENT)}")
    print(f"  Gesichter offen   {share(FLAG_FACES_UNNAMED)}")
    print(f"  im Stapel         {share(FLAG_IN_STACK)}")
    visible = sum(1 for f in flags if not (f & FLAG_IN_STACK) or (f & FLAG_STACK_HEAD))
    print(f"  sichtbar nach Falten: {visible} von {n} ({100 * visible / n:.1f}%)\n")
    print(f"{'n':>6} {'cap':>5}  Kontinent")
    for c in sorted(payload["clusters"], key=lambda c: -c["n"]):
        mark = "~" if c["from_tags"] else " "
        print(f"{c['n']:>6} {c['cap_share'] * 100:>4.0f}%{mark} {', '.join(c['terms'])}")
    print("\n  ~ = aus scene_tags, weil noch keine Captions in diesem Cluster")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", choices=("clip", "text"), default="clip",
                    help="Vektorraum fuer das Layout (Standard: clip)")
    ap.add_argument("--clusters", type=int, default=40, help="Anzahl Kontinente")
    ap.add_argument("--limit", type=int, help="nur die ersten N Fotos (zum Ausprobieren)")
    ap.add_argument("--dup-threshold", type=float, default=DUP_THRESHOLD)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    payload = build(args.space, args.clusters, args.limit, args.dup_threshold, args.out)
    report(payload)


if __name__ == "__main__":
    main()
