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
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from api.qdrant_util import PHOTOS, client
from ingest.spaces import assign

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "static" / "atlas"
#: 2: `clusters[].title` (Sprachmodell) und die Themen-Anordnung unter
#: `themes`. 3: Kontinente sind die Inseln des Layouts (variabel viele, plus
#: ein Eintrag `loose` fuer die Streuung), und jeder traegt `anchors` --
#: Jahre, Personen, benannte Serien als zweite Ebene. Additiv.
FORMAT_VERSION = 3

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

#: Zeitwoerter beschreiben kein Motiv. Sie stehen in jeder Caption
#: ("aufgenommen am 12. März 2023"), sind aber je Monat selten genug, um die
#: Dokumentfrequenz-Schranke zu unterlaufen -- und wurden so Kontinentname:
#: `august` in zwei von 40, `märz` in zwei, dazu `september`, `juni`,
#: `montag`. Die Zeit hat die Karte laengst, als Jahresbaender.
STOPWORDS |= set(
    """
    januar februar märz april mai juni juli august september oktober november dezember
    montag dienstag mittwoch donnerstag freitag samstag sonntag woche wochenende
    täglichen täglich morgens mittags abends nachts
    """.split()
)

#: Unterlagen und Fuellwoerter. `holzoberfläche` wurde Name eines Haufens
#: aus 480 Nahaufnahmen von Dingen: es stand in 37 Beschreibungen, immer als
#: das, worauf etwas *liegt* ("auf einer hellen Holzoberfläche"), und
#: gewann, weil es im ganzen Bestand so selten ist, dass die PMI explodiert.
#: Ein Unterlage-Wort beschreibt nie das Motiv -- anders als `tisch`, der
#: bei `flasche · bier · glas · tisch` die Szene *ist* und deshalb bleibt.
#: `szene` und `einen` sind Floskeln dieses Caption-Modells, die beim
#: Nachruecken sichtbar wurden.
STOPWORDS |= set(
    """
    oberfläche holzoberfläche tischplatte untergrund unterlage laminatboden fußboden
    szene einen
    """.split()
)

#: Mindestanteil der Captions eines Kontinents, in denen ein Wort stehen
#: muss. Gemessen an allen 40 Kontinenten: 10 % waere ein Netto-Verlust --
#: es nimmt `abiball · silvester`, `funken`, `videospiel`, `straße · gebäude`
#: mit, lauter unterscheidende Ereignis- und Motivwoerter, die eben auch bei
#: 5-10 % liegen, und laesst Fuellwoerter nachruecken. 5 % bleibt.
MIN_SHARE = 0.05

#: Personennamen stehen nie im Kontinent-Namen -- weder als Rangwort noch
#: im Titel. Cluster-Zugehoerigkeit ist Naehe; ob eine Person auf einem Foto
#: ist, ist eine Tatsache. Eine Schublade hiess "Mira Faller", waehrend
#: sie auf zwei von drei Fotos fehlte, und keine Schwelle heilt das: bei
#: 60 % fehlt sie auf 40 %. Personen sind deshalb eine eigene Schicht --
#: Anker am Schwerpunkt *ihrer* Fotos im Kontinent (`anchors`), per
#: Konstruktion nur dort, wo sie bestaetigt sind.

_RE_WORD = re.compile(r"[a-zA-ZÄÖÜäöüß]{4,}")

#: Zweites Netz unter der Wortliste: was in mehr als einem Fuenftel aller
#: Captions steht, ist Floskel. Als Schwelle *und* Liste, weil die Floskeln
#: vom Caption-Modell abhaengen -- eine reine Liste veraltet mit dem naechsten
#: Modell, eine reine Schwelle wuerde haeufige Familiennamen mitnehmen --
#: der haeufigste steht in 16 % aller Captions und traegt trotzdem Bedeutung.
MAX_DOC_FREQ = 0.20

#: Ab so vielen Fotos taugt ein Szenen-Tag als Auswahl. Der Caption-Lauf
#: ergaenzt eigene Tags, und der Schwanz besteht aus Einzelfaellen
#: („hindernisparcours", „besprechungsnest", „milchreis") -- an diesem Bestand
#: 11 205 verschiedene, von denen 206 mindestens hundertmal vorkommen. Ohne
#: Schwelle waere die Liste unbenutzbar und die Karte um 700 kB groesser.
MIN_TAG_PHOTOS = 100

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
FLAG_VIDEO = 1 << 9  #: ein Video -- die Kachel zeigt sein Poster, nicht das Ganze


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
            # Was im Papierkorb liegt, gehoert nicht mehr auf die Karte --
            # sonst waehlt man es beim naechsten Aufraeumen wieder mit aus.
            if payload.get("trashed_at"):
                continue
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
                    "file_path": payload.get("file_path") or "",
                    "kind": payload.get("kind") or "photo",
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


#: Kleinste Insel, die ein Kontinent wird. Gemessen an dieser Karte: 40
#: ergibt rund 95 Inseln (zu viele Schilder), 100 rund 20 mit zwei Inseln
#: ueber 3.500. 60 liegt dazwischen; was darunter liegt, wird Streuung und
#: bekommt kein Schild -- ehrlicher als ein erzwungener Kontinent.
ISLAND_MIN = 60
#: Wie dicht Punkte liegen muessen, um als Insel zu gelten. Gemessen auf
#: den echten UMAP-Koordinaten (nicht den gerundeten aus der JSON -- die
#: kippen dieselbe Karte in eine andere Loesung): 76 Inseln, 32 %
#: Streuung, groesste 1.125 Fotos. Die Streuung liegt wirklich zwischen
#: den Inseln, auf duennen Bruecken, und bekommt kein Schild; die Inseln
#: sind dafuer die, die man sieht. In float64, damit die Rechnung nicht an
#: der Genauigkeit haengt.
ISLAND_MIN_SAMPLES = 5


def island_clusters(coords: np.ndarray, min_size: int = ISLAND_MIN,
                    min_samples: int = ISLAND_MIN_SAMPLES) -> tuple[np.ndarray, int]:
    """Die Inseln, die man auf der Karte *sieht* -- als Kontinente.

    k-means teilte im 768-dimensionalen Raum in genau k Stuecke. Der
    Betrachter sieht aber die 2D-Karte, und dort Inseln, die k-means nicht
    kennt: gemessen deckten sich Insel und Kontinent im Median nur zu 56 %
    (Themen) bzw. 70 % (visuell), ein Drittel der Inseln lag mehrheitlich
    in einem anderen Kontinent als ihr Schild sagte. Deshalb stand die
    Beschriftung nicht dort, wo der Haufen war, den man sah.

    HDBSCAN auf den Koordinaten findet die sichtbaren Inseln, variabel
    gross, und laesst Streuung als Streuung stehen. Zurueck kommt
    (labels, loose): Streuung traegt den Index `loose`, der letzte -- ein
    eigener Eintrag ohne Schild, damit jeder Index gueltig bleibt und die
    Oberflaeche keine Sonderfaelle braucht.
    """
    from sklearn.cluster import HDBSCAN

    lab = HDBSCAN(min_cluster_size=min_size, min_samples=min_samples, copy=True).fit(
        np.asarray(coords, dtype=np.float64)).labels_
    n = int(lab.max()) + 1
    out = np.where(lab < 0, n, lab).astype(np.int64)
    logger.info("%d Inseln, %d Punkte Streuung (%.0f%%)", n, int((lab < 0).sum()),
                100 * float((lab < 0).mean()))
    return out, n


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

    # Namensteile aller bestaetigten Personen: die stehen nie im Namen
    # eines Kontinents. Personen sind eine eigene Schicht (siehe
    # `anchors_for`), weil Naehe nicht traegt, wer auf einem Foto ist.
    name_tokens = person_tokens(meta)

    out: list[dict] = []
    for c in range(k):
        size = int((labels == c).sum())
        local = n_caps[c] or 1
        scored = []
        for term, seen_here in hits[c].items():
            # Ein Wort aus einer einzigen Caption beschreibt ein Foto, keinen
            # Kontinent -- und unter MIN_SHARE der Captions ist es Beifang.
            if seen_here < 3 or seen_here / local < MIN_SHARE:
                continue
            if term in name_tokens:
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
                "terms": distinct_stems([t for _, t in scored], top_n),
                "cap_share": round(int(captioned[c]) / max(size, 1), 3),
            }
        )
    return out


def person_tokens(meta: list[dict]) -> set[str]:
    """Alle Namensteile bestaetigter Personen, klein: `marie`, `kempter`, ..."""
    out: set[str] = set()
    for m in meta:
        for name in m.get("person_names") or []:
            out.update(_RE_WORD.findall(name.lower()))
    return out


#: Ab so vielen Fotos bekommt ein Jahr oder eine Person im Kontinent einen
#: Anker. Das ist eine Frage der Dichte auf der Karte, keine
#: Wahrheitsbehauptung: der Anker sitzt ohnehin nur dort, wo die Fotos
#: sind, auf denen die Person bestaetigt ist. Weniger ergibt Schilderwald,
#: mehr laesst kleine, echte Gruppen unbeschriftet.
ANCHOR_MIN = 15
#: Benannte Serien sind seltener und tragen den Namen, den ein Mensch
#: gegeben hat -- die duerfen kleiner sein.
EVENT_ANCHOR_MIN = 8
#: Hoechstens so viele Personen-Anker je Insel, die haeufigsten zuerst. Im
#: 4.300er-Klumpen haetten sonst dreissig Namen gestanden.
PERSON_ANCHORS_MAX = 8


def anchors_for(labels: np.ndarray, coords: np.ndarray, meta: list[dict], c: int,
                event_of_photo: list[int] | None = None, events: list[dict] | None = None,
                ) -> list[dict]:
    """Die zweite Ebene eines Kontinents: Wegweiser aus Tatsachen.

    Ein Kontinent aus Vektornaehe kann "Wintersport" heissen, aber nicht
    "Jonas" -- Naehe traegt nicht, wer auf einem Foto ist. Drinnen liegt
    aber Struktur, die *Tatsachen* sind: welches Jahr, welche Person, welche
    benannte Serie. Jeder Anker sitzt am Schwerpunkt genau der Fotos, die
    ihn tragen. "Wintersport 2022 · Jonas · Nele" -- und die Fotos mit
    beiden liegen dazwischen, nahe beiden.

    Gezeichnet werden sie beim Hineinzoomen; von weitem nur der Kontinent.
    """
    idx = np.nonzero(labels == c)[0]
    if not len(idx):
        return []
    out: list[dict] = []

    def anker(kind: str, label: str, member: np.ndarray, ref) -> None:
        centroid = coords[member].mean(axis=0)
        out.append({"kind": kind, "label": label, "n": int(len(member)),
                    "x": round(float(centroid[0]), 4), "y": round(float(centroid[1]), 4),
                    "ref": ref})

    # Jahre
    jahre: dict[str, list[int]] = collections.defaultdict(list)
    for i in idx:
        y = (meta[i]["taken_at"] or "")[:4]
        if y:
            jahre[y].append(int(i))
    for y, member in sorted(jahre.items()):
        if len(member) >= ANCHOR_MIN:
            anker("year", y, np.asarray(member), int(y))

    # Personen -- bestaetigte, nicht erwaehnte
    personen: dict[str, list[int]] = collections.defaultdict(list)
    for i in idx:
        for name in meta[i].get("person_names") or []:
            personen[name].append(int(i))
    gesetzt = 0
    for name, member in sorted(personen.items(), key=lambda kv: -len(kv[1])):
        if len(member) >= ANCHOR_MIN and gesetzt < PERSON_ANCHORS_MAX:
            anker("person", name, np.asarray(member), name)
            gesetzt += 1

    # Benannte Serien
    if event_of_photo is not None and events:
        je_serie: dict[int, list[int]] = collections.defaultdict(list)
        for i in idx:
            e = event_of_photo[i]
            if e >= 0 and events[e].get("name"):
                je_serie[e].append(int(i))
        for e, member in sorted(je_serie.items(), key=lambda kv: -len(kv[1])):
            if len(member) >= EVENT_ANCHOR_MIN:
                anker("event", str(events[e]["name"]), np.asarray(member), int(e))
    return out


def confirmed_people(labels: np.ndarray, meta: list[dict], c: int, top: int = 3) -> list[tuple[str, float]]:
    """Die haeufigsten bestaetigten Personen eines Kontinents mit Anteil.

    Fuer den Titel-Prompt: das Modell soll wissen, dass "Mira Faller"
    auf 32 % der Fotos ist, bevor es die Schublade nach ihr benennt.
    """
    idx = np.nonzero(labels == c)[0]
    if not len(idx):
        return []
    zaehler: collections.Counter = collections.Counter()
    for i in idx:
        for name in meta[i].get("person_names") or []:
            zaehler[name] += 1
    return [(name, n / len(idx)) for name, n in zaehler.most_common(top)]


def distinct_stems(terms: list[str], top_n: int) -> list[str]:
    """Die ersten `top_n` Begriffe, ohne dass einer den Stamm eines anderen
    wiederholt.

    Vier Plaetze hat ein Kontinentname, und in 9 von 40 war einer davon
    verschenkt: `kinder · kindern`, `autos · auto`, `schlafen · schläft`,
    `verschneite · verschneiten`, `holzoberfläche · oberfläche`. Zwei
    Formen desselben Worts sagen nicht mehr als eine.

    Kein Stemmer -- der muesste deutsche Flexion kennen und kaeme mit dem
    naechsten Caption-Modell aus dem Tritt. Stattdessen der gemeinsame
    Anfang: teilen sich zwei Begriffe die ersten vier Buchstaben und ist
    einer im anderen enthalten oder unterscheiden sie sich nur in der
    Endung, bleibt der besser bewertete. `kind`/`kinder` faellt zusammen,
    `oberfläche`/`holzoberfläche` ebenso (Teilwort am Ende); `garten`/`gas`
    nicht.
    """
    def verwandt(a: str, b: str) -> bool:
        if a == b:
            return True
        kurz, lang = (a, b) if len(a) <= len(b) else (b, a)
        if len(kurz) < 4:
            return False
        # Teilwort: `oberfläche` in `holzoberfläche`, `kind` in `kinder`.
        if kurz in lang and (lang.startswith(kurz) or lang.endswith(kurz)):
            return True
        # Flexion: gleicher Anfang, Rest ist nur Endung (<= 3 Zeichen je Seite).
        gemeinsam = 0
        for x, y in zip(a, b):
            if x != y:
                break
            gemeinsam += 1
        return gemeinsam >= 5 and len(a) - gemeinsam <= 3 and len(b) - gemeinsam <= 3

    out: list[str] = []
    for t in terms:
        if any(verwandt(t, d) for d in out):
            continue
        out.append(t)
        if len(out) >= top_n:
            break
    return out


def describe_clusters(labels: np.ndarray, coords: np.ndarray, meta: list[dict],
                      heads: set[int], k: int, loose: int | None = None,
                      event_of_photo: list[int] | None = None,
                      events: list[dict] | None = None) -> list[dict]:
    """Je Kontinent: Rangwoerter, Schwerpunkt, Leitbild, Jahre, Anker.

    Einmal fuer die visuelle Anordnung, einmal fuer die Themen -- derselbe
    Eintrag, damit die Oberflaeche beide gleich behandeln kann. `loose` ist
    der Index der Streuung: ein Eintrag ohne Schild und ohne Anker, damit
    jeder Index gueltig bleibt.
    """
    label_info = label_clusters(labels, meta, k)
    out = []
    for c in range(k):
        idx = np.nonzero(labels == c)[0]
        if not len(idx) or c == loose:
            centroid = coords[idx].mean(axis=0) if len(idx) else np.asarray([0.5, 0.5])
            out.append({"i": c, "terms": [], "cap_share": 0.0, "from_tags": False,
                        "n": int(len(idx)), "x": round(float(centroid[0]), 4),
                        "y": round(float(centroid[1]), 4), "cover": None, "years": [],
                        "loose": c == loose, "anchors": []})
            continue
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
        out.append(
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
                "loose": False,
                "anchors": anchors_for(labels, coords, meta, c, event_of_photo, events),
            }
        )
    return out


def load_second(qc: Any, ids: list[str], space: str, batch: int = 512) -> np.ndarray:
    """Einen zweiten Vektor zu denselben Fotos -- NaN-Zeilen, wo er fehlt.

    Getrennt von `load_points`, damit die Fotomenge der Karte die der
    ersten Anordnung bleibt. Eine Anordnung mit anderen Fotos als die
    andere waere keine zweite Sicht auf dieselbe Karte, sondern eine
    zweite Karte.
    """
    dim = None
    rows: list[list[float] | None] = [None] * len(ids)
    pos = {pid: i for i, pid in enumerate(ids)}
    for start in range(0, len(ids), batch):
        for p in qc.retrieve(collection_name=PHOTOS, ids=ids[start:start + batch],
                             with_payload=False, with_vectors=[space]):
            vec = (p.vector or {}).get(space)
            if vec:
                rows[pos[str(p.id)]] = vec
                dim = dim or len(vec)
    if dim is None:
        return np.full((len(ids), 1), np.nan, dtype=np.float32)
    out = np.full((len(ids), dim), np.nan, dtype=np.float32)
    for i, r in enumerate(rows):
        if r is not None:
            out[i] = r
    logger.info("%d von %d Punkten mit %s-Vektor", sum(r is not None for r in rows), len(ids), space)
    return out


#: Wieviele visuelle Nachbarn den Platz eines Fotos ohne Beschreibung
#: bestimmen.
THEME_VOTERS = 10
#: Unter so vielen beschriebenen Fotos gibt es keine Themen-Anordnung --
#: UMAP ueber eine Handvoll Punkte ist keine Karte.
THEME_MIN = 50


def theme_layout(Xn: np.ndarray, Xt: np.ndarray, meta: list[dict]) -> np.ndarray:
    """Positionen der Themen-Anordnung. Die Kontinente kommen danach aus
    den Inseln dieses Layouts, wie bei der visuellen Anordnung.

    Grundlage ist der Text-Vektor -- aber nur bei Fotos *mit* Beschreibung.
    Ohne Beschreibung besteht er allein aus Metadaten (Ordner, Datum,
    Namen), und im Text-Raum ballen sich solche Fotos zu Haufen, die
    woertlich nach dem Ordner heissen: `oneplus · nord`. Gemessen an einem
    Probebau: sieben Kontinente ohne eine einzige Beschreibung.

    Fotos ohne Beschreibung bekommen deshalb Platz und Kontinent von ihren
    *visuellen* Nachbarn -- den zehn naechsten im Bild-Raum, die eine
    Beschreibung haben. Sie landen dort, wo Fotos liegen, die aussehen wie
    sie. Das ist eine Schaetzung, und die Oberflaeche zeichnet sie
    gedaempft; aber eine begruendete Schaetzung ist besser als ein Haufen,
    der nach einem Ordner heisst.
    """
    hat_text = ~np.isnan(Xt).any(axis=1)
    hat_caption = np.asarray([bool(m["caption"]) for m in meta])
    fest = hat_text & hat_caption
    if fest.sum() < THEME_MIN:
        raise SystemExit(f"Nur {int(fest.sum())} Fotos mit Beschreibung und Text-Vektor -- "
                         f"zu wenig fuer eine Themen-Anordnung.")

    Xtn = Xt[fest] / (np.linalg.norm(Xt[fest], axis=1, keepdims=True) + 1e-9)
    coords_fest = to_unit(project(Xtn))

    coords = np.zeros((len(meta), 2), dtype=np.float32)
    fest_idx = np.nonzero(fest)[0]
    coords[fest_idx] = coords_fest

    lose_idx = np.nonzero(~fest)[0]
    if len(lose_idx):
        from sklearn.neighbors import NearestNeighbors

        nn = NearestNeighbors(n_neighbors=min(THEME_VOTERS, len(fest_idx)), metric="cosine")
        nn.fit(Xn[fest_idx])
        _, nachbarn = nn.kneighbors(Xn[lose_idx])
        for j, row in zip(lose_idx, nachbarn):
            coords[j] = coords_fest[row].mean(axis=0)
        logger.info("%d Fotos ohne Beschreibung ueber visuelle Nachbarn platziert", len(lose_idx))
    return coords


def apply_titles(clusters: list[dict], labels: np.ndarray, meta: list[dict],
                 step: Any = lambda _n: None, ask: Any = None) -> int:
    """Jedem Kontinent einen Titel geben; wo keiner kommt, bleibt `title` leer.

    `ask` ist austauschbar (Tests). Ohne `ask` wird der Captioner gefragt --
    ueber denselben Weg wie die Captions, damit es eine Stelle gibt, die
    weiss, wie das Modell erreicht wird.
    """
    from ingest.cluster_titles import title_clusters

    if ask is None:
        import os

        from ingest.captioner import CAPTION_MODEL, Captioner

        # Standard: dasselbe Modell wie die Captions. An diesem Rechner ist
        # das ein 27B mit 262k Kontext -- gemessen rund eine Minute je Titel
        # bei voller GPU, 100 Titel je Kartenbau. Wer einen schnelleren
        # Pool-Alias hat (`fast`), setzt ihn hier; die Aufgabe ist klein.
        modell = os.environ.get("PHOTOVAULT_TITLE_MODEL") or CAPTION_MODEL
        ask = Captioner(model=modell).ask_json

    rng = random.Random(7)

    def samples_of(c: int) -> list[str]:
        idx = [i for i in np.nonzero(labels == c)[0] if meta[i]["caption"]]
        rng.shuffle(idx)
        return [meta[i]["caption"] for i in idx[:8]]

    # Die Streuung hat kein Thema -- sie zu befragen kostete einen Aufruf
    # fuer ein "Diverse Aufnahmen", das nirgends gezeichnet wird.
    echte = [c for c in clusters if not c.get("loose") and c.get("n", 0) > 0]
    titles = title_clusters(echte, samples_of, ask, step=step,
                            people_of=lambda c: confirmed_people(labels, meta, c))
    n = 0
    for c in clusters:
        c["title"] = None
    for c, t in zip(echte, titles):
        c["title"] = t
        n += t is not None
    logger.info("%d von %d Kontinenten betitelt", n, len(echte))
    return n


def fallback_terms(labels: np.ndarray, meta: list[dict], c: int) -> list[str]:
    """Notnagel fuer Cluster ohne Captions: die haeufigsten Szenen-Tags.

    Bewusst schwaecher gewichtet -- die Tags sind grob und gelegentlich falsch.
    """
    idx = np.nonzero(labels == c)[0]
    tags = collections.Counter(t for i in idx for t in meta[i]["tags"])
    return [t for t, _ in tags.most_common(3)]


# --------------------------------------------------------------------------
# Bereiche
# --------------------------------------------------------------------------

def split_spaces(meta: list[dict]) -> tuple[str, list[str], list[int]]:
    """Die erste Ordnerebene unter der gemeinsamen Wurzel ist der Bereich.

    Kein neues Feld, keine zweite Wahrheit: der Bereich *ist*, wo die Datei
    liegt. Verschiebt man ein Foto, wechselt es den Bereich -- und genau das
    ist der Zweck. An diesem Bestand ergibt das `Handys` (der Dump, aus dem
    aufgeraeumt wird), `Fotos` (die Bibliothek) und `Sonstiges` (was
    herausgezogen wurde).

    Der Bereich beantwortet die Frage, die eine Bedeutungskarte allein nicht
    beantworten kann: Screenshots und Dokumente *sind* interessant -- nur
    nicht zwischen den Fotos von Menschen.
    """
    # Gerechnet wird das in ingest/spaces.py -- dieselbe Funktion, die das
    # Payload-Feld `space` fuellt, damit Karte und Suche nicht auseinanderlaufen.
    root, names, out = assign(m["file_path"] or "" for m in meta)
    logger.info("Bereiche unter %s: %s", root or "/",
                ", ".join(f"{n} {out.count(i)}" for i, n in enumerate(names)))
    return root, names, out


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
    if m.get("kind") == "video":
        flags |= FLAG_VIDEO
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


#: Die Schritte, in der Reihenfolge, in der sie laufen. Anteile aus den
#: gemessenen Zeiten an 17 370 Fotos -- ein Balken, der sich gleichmaessig
#: fuellt, ist eine Luege, wenn UMAP zwei Drittel der Zeit braucht.
PHASES = (
    ("laden", 0.10),
    ("umap", 0.55),
    ("stapel", 0.10),
    ("bereiche", 0.02),
    ("serien", 0.08),
    ("kontinente", 0.12),
    ("schreiben", 0.03),
)


def progress_reporter(job) -> Any:
    """Meldet, was *jetzt* laeuft — nicht, was gerade fertig wurde.

    Der erste Entwurf meldete den abgeschlossenen Schritt, und weil UMAP allein
    die Haelfte der Zeit braucht, stand dort eine halbe Minute lang „laden",
    waehrend schon projiziert wurde. Der Anteil zaehlt dagegen nur, was hinter
    uns liegt.

    Eigene Funktion, weil die Namen sonst mit denen im Rechenteil kollidieren:
    `order` hiess dort schon eine Sortierung aus numpy, und `step("schreiben")`
    starb an `'numpy.ndarray' object has no attribute 'index'`.
    """
    phase_order = [name for name, _ in PHASES]
    phase_share = dict(PHASES)

    def step(name: str) -> None:
        if job is None:
            return
        i = phase_order.index(name)
        job.update(processed=int(sum(phase_share[n] for n in phase_order[:i]) * 100),
                   phase=name)

    return step


def build(space: str, k: int, limit: int | None, dup_threshold: float, out_dir: Path,
          track: bool = True, themes_on: bool = True, titles: bool = True) -> dict:
    """Rechnen und dabei Bescheid geben.

    Von der Jobs-Seite aus gestartet, versprach die Antwort „der Fortschritt
    erscheint in dieser Liste" -- dieser Lauf meldete sich dort aber nie.

    Das Melden umschliesst den *ganzen* Rechenteil. Im ersten Entwurf deckte
    das `try` nur die erste Haelfte ab; ein Fehler danach liess den Job auf
    ewig „running" stehen, bis er nach zwei Minuten als `stale` galt.
    """
    qc = client()
    job = None
    if track:
        try:
            from ingest.jobs import JobTracker

            job = JobTracker(qc, kind="atlas", source=space)
            job.update(total=100, processed=0, phase="laden", force=True)
        except Exception as e:  # Tracking darf den Lauf nie stoppen.
            logger.debug("Job-Tracking nicht verfuegbar: %s", e)

    try:
        payload = compute(space, k, limit, dup_threshold, out_dir,
                          step=progress_reporter(job), qc=qc,
                          themes_on=themes_on, titles=titles)
    except BaseException as e:
        # Auch SystemExit: „umap-learn fehlt" gehoert in die Liste, nicht nur
        # ins Protokoll.
        if job is not None:
            job.finish("error", phase=str(e).splitlines()[0][:200] or type(e).__name__,
                       errors=1)
        raise
    if job is not None:
        job.finish("done", processed=100, total=100,
                   phase=f"{payload['n']} Fotos, {len(payload['clusters'])} Kontinente")
    return payload


def compute(space: str, k: int, limit: int | None, dup_threshold: float, out_dir: Path,
            step: Any = lambda _name: None, qc: Any = None,
            themes_on: bool = True, titles: bool = True) -> dict:
    """Die eigentliche Rechnung. Weiss nichts von Jobs."""
    qc = qc or client()

    step("laden")
    X, meta = load_points(qc, space, limit)
    if len(X) < max(k, 50):
        raise SystemExit(f"Nur {len(X)} Punkte mit {space}-Vektor -- zu wenig fuer eine Karte.")

    step("umap")
    Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    coords = to_unit(project(Xn))

    step("stapel")
    roots = find_stacks(Xn, dup_threshold)
    heads = pick_stack_heads(roots, meta)
    sizes = collections.Counter(roots.tolist())

    step("bereiche")
    root, spaces, space_of_photo = split_spaces(meta)
    step("serien")
    events, event_of_photo = build_events(meta, coords)
    step("kontinente")
    # Kontinente sind die Inseln der Karte, die man sieht -- nicht k
    # Stuecke aus dem 768-dimensionalen Raum. `--clusters` waehlt noch das
    # alte Verfahren, fuer den Vergleich.
    if k:
        labels = kmeans_clusters(Xn, k)
        loose = None
    else:
        labels, loose = island_clusters(coords)
        k = loose + 1
    clusters = describe_clusters(labels, coords, meta, heads, k, loose=loose,
                                 event_of_photo=event_of_photo, events=events)

    channels = sorted({m["channel"] for m in meta})
    chan_index = {c: i for i, c in enumerate(channels)}

    # Zweite Anordnung: Themen. Dieselben Fotos, aber Naehe heisst hier
    # "wird aehnlich beschrieben" -- Text-Vektor statt Bild-Vektor. Die
    # Kontinente sind auch hier die Inseln des Layouts.
    themes = None
    if themes_on:
        step("themen")
        Xt = load_second(qc, [m["id"] for m in meta], "text")
        coords2 = theme_layout(Xn, Xt, meta)
        labels2, loose2 = island_clusters(coords2)
        themes = {
            "k": loose2 + 1,
            "clusters": describe_clusters(labels2, coords2, meta, heads, loose2 + 1, loose=loose2,
                                          event_of_photo=event_of_photo, events=events),
            "x": [round(float(v), 4) for v in coords2[:, 0]],
            "y": [round(float(v), 4) for v in coords2[:, 1]],
            "cl": [int(v) for v in labels2],
        }

    if titles:
        step("titel")
        apply_titles(clusters, labels, meta, step)
        if themes:
            apply_titles(themes["clusters"], np.asarray(themes["cl"]), meta, step)

    # Personen als Index statt als Name je Foto: 114 Namen einmal, danach
    # kleine Zahlen. Ohne das waere ein Drittel der Datei Wiederholung.
    people = sorted({n for m in meta for n in m["person_names"]})
    person_index = {n: i for i, n in enumerate(people)}

    # Szenen-Tags mit in die Karte. Die Kontinente heissen zwar teils
    # "screenshot, dokument", aber Screenshots liegen in *neun* davon --
    # sie einzeln anzuklicken ist Arbeit, die eine Auswahl nach Tag erspart.
    # Haeufigste zuerst, damit die Liste in der UI schon sortiert ist.
    tag_counts = collections.Counter(t for m in meta for t in m["tags"])
    tags = [t for t, n in tag_counts.most_common() if n >= MIN_TAG_PHOTOS]
    tag_index = {t: i for i, t in enumerate(tags)}

    payload = {
        "version": FORMAT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "space": space,
        "n": len(meta),
        "dup_threshold": dup_threshold,
        "channels": channels,
        "persons": people,
        "tags": tags,
        "root": root,
        "spaces": spaces,
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
        "sp": space_of_photo,
        "tg": [[tag_index[t] for t in m["tags"] if t in tag_index] for m in meta],
    }
    if themes:
        payload["themes"] = themes

    step("schreiben")
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
    counts = collections.Counter(payload["sp"])
    bereiche = " · ".join(
        f"{name} {counts[i]}" for i, name in enumerate(payload["spaces"]) if counts[i]
    )
    print(f"  Bereiche unter {payload['root'] or '/'}: {bereiche}")
    per_tag = collections.Counter(t for row in payload["tg"] for t in row)
    top = ", ".join(f"{payload['tags'][i]} {n}" for i, n in per_tag.most_common(6))
    print(f"  Szenen ab {MIN_TAG_PHOTOS} Fotos: {len(payload['tags'])}, häufigste {top}")
    print(f"  Serien            {len(payload['events']):>6}")
    print(f"  benannte Person   {share(FLAG_PERSON)}")
    print(f"  Beschreibung      {share(FLAG_CAPTION)}")
    print(f"  Datum aus EXIF    {share(FLAG_EXIF_DATE)}")
    print(f"  benannte Serie    {share(FLAG_EVENT)}")
    print(f"  Gesichter offen   {share(FLAG_FACES_UNNAMED)}")
    print(f"  im Stapel         {share(FLAG_IN_STACK)}")
    visible = sum(1 for f in flags if not (f & FLAG_IN_STACK) or (f & FLAG_STACK_HEAD))
    print(f"  sichtbar nach Falten: {visible} von {n} ({100 * visible / n:.1f}%)\n")
    def zeige(name: str, clusters: list[dict]) -> None:
        lose = next((c for c in clusters if c.get("loose")), None)
        echte = [c for c in clusters if not c.get("loose")]
        print(f"\n{'n':>6} {'cap':>5}  {name}: {len(echte)} Inseln"
              + (f", Streuung {lose['n']} Fotos ohne Kontinent" if lose else ""))
        for c in sorted(echte, key=lambda c: -c["n"]):
            mark = "~" if c["from_tags"] else " "
            titel = c.get("title")
            kopf = f"{titel}   [{', '.join(c['terms'])}]" if titel else ", ".join(c["terms"])
            anker = c.get("anchors") or []
            zusatz = f"   +{len(anker)} Anker" if anker else ""
            print(f"{c['n']:>6} {c['cap_share'] * 100:>4.0f}%{mark} {kopf}{zusatz}")

    zeige("Kontinent (visuell)", payload["clusters"])
    if payload.get("themes"):
        zeige("Schublade (Themen)", payload["themes"]["clusters"])
    print("\n  ~ = aus scene_tags, weil noch keine Captions in diesem Cluster")
    if not any(c.get("title") for c in payload["clusters"]):
        print("  Keine Titel vom Sprachmodell -- die Karte zeigt die Rangwoerter.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", choices=("clip", "text"), default="clip",
                    help="Vektorraum fuer das Layout (Standard: clip)")
    ap.add_argument("--clusters", type=int, default=0,
                    help="k-means mit so vielen Kontinenten statt der Inseln (Vergleich)")
    ap.add_argument("--limit", type=int, help="nur die ersten N Fotos (zum Ausprobieren)")
    ap.add_argument("--dup-threshold", type=float, default=DUP_THRESHOLD)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--no-track", action="store_true",
                    help="nicht in die Job-Liste schreiben")
    ap.add_argument("--no-themes", action="store_true",
                    help="ohne die Themen-Anordnung")
    ap.add_argument("--no-titles", action="store_true",
                    help="keine Titel vom Sprachmodell, nur Rangwoerter")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.no_titles:
        # Wie die anderen CLI-Einstiege: LiteLLM ist der Chokepoint. Ohne
        # das fiel der Titler auf Ollama zurueck, das den Pool-Alias `local`
        # nicht kennt -- 404, und 0 von 100 Kontinenten betitelt.
        from ingest.ollama_client import apply_llm_env

        apply_llm_env()
    payload = build(args.space, args.clusters, args.limit, args.dup_threshold, args.out,
                    track=not args.no_track, themes_on=not args.no_themes,
                    titles=not args.no_titles)
    report(payload)


if __name__ == "__main__":
    main()
