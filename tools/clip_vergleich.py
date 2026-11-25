"""CLIP ViT-L/14 gegen SigLIP 2 messen: Szenen-Tags und deutsche Bildsuche (#59).

Der Index rechnet Szenen-Tags und den 768d-Bildvektor mit OpenAI-CLIP
ViT-L/14 von 2021, Prompts englisch. SigLIP 2 (Feb. 2025) ist mehrsprachig
trainiert; ob das hier etwas bringt, sagt kein Paper, sondern dieser Lauf.
Ein Wechsel kostet einen Re-Embed aller Medien und einen neuen Atlas --
er kommt nur nach einer Tabelle.

Die Referenz ist *nicht* CLIP. Fotos mit Beschreibung tragen Tags und
`caption_de` aus dem Vision-Modell (Qwen), also unabhaengig von beiden
Kandidaten. Aus diesem Text leitet `referenz_tags` je Foto ab, welche der
`SCENE_CONCEPTS` zu sehen sind -- ueber deutsche Wortanfaenge, nicht ueber
ein Modell. Die alten CLIP-Tags eines Fotos zaehlen nicht mit: sie waeren
die Antwort eines der beiden Kandidaten.

Gemessen wird, jeweils fuer beide Modelle mit denselben Bildern:

* **Tags**: dieselbe Regel wie `SceneTagger` (Prompt-Mittel, Softmax mit der
  Temperatur des Modells, Top-5, Mindestwahrscheinlichkeit), Precision /
  Recall / F1 gegen die Referenz. Dazu schwellenfrei `top1` (bestes Konzept
  ist in der Referenz) und mAP -- die Schwellen sind auf CLIP eingestellt
  und sollen SigLIP nicht benachteiligen.
* **Beschreibung -> Bild**: `caption_de` als Anfrage, das eigene Foto unter
  allen Kandidaten finden (Recall@1/5/10). So sucht jemand, der einen Inhalt
  beschreibt, ohne dass das Foto schon eine Beschreibung haette.
* **Kurze deutsche Anfragen** ("Hund", "Strand") gegen dieselbe Menge,
  Trefferquote der ersten zehn gegen die Referenz-Tags.
* **Videos**: Frames wie im Ingest (`sample_frames`, Medoid), Referenz aus
  den wenigen Videos mit Beschreibung und optional aus einer Label-Datei
  (`--video-labels`, JSON `{file_path: [label, ...]}`).

    python -m tools.clip_vergleich --n 300 --videos 60 --out data/clip_vergleich.json

GPU: beide Modelle laufen nacheinander in fp16 (So400m ~2,3 GB VRAM). Vorher
das GPU-Tor fragen -- der Lauf fragt es nicht selbst.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: (open_clip-Name, pretrained) -- der erste ist der heutige Stand.
MODELLE = [
    ("ViT-L-14", "openai"),
    ("ViT-SO400M-14-SigLIP2", "webli"),
]

#: Konzept -> Muster im deutschen Text (Beschreibung + Qwen-Tags, klein).
#: `\b` vorne: "meer" soll nicht in "Meerschweinchen" stecken, darum dort
#: ein Ende; Wortanfaenge decken Komposita wie "Strandkorb" mit ab.
REFERENZ_MUSTER: dict[str, str] = {
    "strand": r"\bstrand",
    "meer": r"\b(meer\b|meeres|ozean|ostsee|nordsee|brandung)",
    "berge": r"\b(berg(e|en|landschaft|gipfel|panorama)?\b|gebirg|alpen|gipfel)",
    "wald": r"\bwald|\bwäld",
    "stadt": r"\b(stadt|straße|innenstadt|altstadt|großstadt)",
    "innenraum": r"\b(innenraum|wohnzimmer|küche|zimmer|schlafzimmer|innen\b|drinnen)",
    "draussen": r"\b(im freien|draußen|freien\b|garten|park\b|wiese)",
    "schiff": r"\b(schiff|boot|fähre|segel|kreuzfahrt)",
    "geburtstag": r"\bgeburtstag",
    "hochzeit": r"\b(hochzeit|braut|bräutigam|trauung)",
    "abschluss": r"\b(abschluss|abiball|abitur|zeugnis|absolvent)",
    "weihnachten": r"\b(weihnacht|christbaum|tannenbaum|adventskranz)",
    "silvester": r"\bsilvester|\bneujahr",
    "feuerwerk": r"\bfeuerwerk",
    "party": r"\b(party|feier\b|feiern|feiert|disco|club\b)",
    "konzert": r"\b(konzert|bühne|band\b|festival)",
    "restaurant": r"\b(restaurant|gaststätte|lokal\b|bistro|café\b)",
    "essen": r"\b(essen|speise|gericht|teller|mahlzeit|pizza|nudeln|frühstück|abendessen)",
    "grillen": r"\b(grill|barbecue)",
    "torte": r"\b(torte|kuchen)",
    "getraenke": r"\b(getränk|bier|wein\b|weinglas|cocktail|glas\b|gläser|sekt|flasche)",
    "tanzen": r"\b(tanz|tänz)",
    "sport": r"\b(sport|fußball|turnier|training|klettert|kletter)",
    "schwimmen": r"\b(schwimm|pool\b|freibad|hallenbad)",
    "wandern": r"\b(wander|wanderweg)",
    "skifahren": r"\b(ski\b|skifahr|skipiste|piste)",
    "radfahren": r"\b(fahrrad|radfahr|rad\b|radler)",
    "urlaub": r"\b(urlaub|ferien|tourist|sehenswürdig)",
    "portraet": r"\b(porträt|portrait|selfie|nahaufnahme)",
    "gruppenfoto": r"\b(gruppenfoto|gruppe\b|gruppenbild)",
    "kinder": r"\b(kind\b|kinder|junge\b|mädchen|kleinkind)",
    "baby": r"\b(baby|säugling|neugebor)",
    "paar": r"\b(paar\b|pärchen|umarm)",
    "hund": r"\b(hund|welpe)",
    "katze": r"\b(katze|kater\b|kätzchen)",
    "auto": r"\b(auto\b|autos\b|fahrzeug|wagen\b|pkw)",
    "landschaft": r"\b(landschaft|panorama|aussicht)",
    "gebaeude": r"\b(gebäude|architektur|kirche|haus\b|fassade|schloss|burg\b|turm\b)",
    "sonnenuntergang": r"\b(sonnenuntergang|sonnenaufgang|abendrot)",
    "nacht": r"\b(nacht|nachts|abends\b|dunkel)",
    "schnee": r"\b(schnee|verschneit|winterlandschaft)",
    "regen": r"\b(regen\b|regnet|regenschirm)",
    "dokument": r"\b(dokument|text\b|seite\b|brief\b|formular|rechnung|urkunde|notizen|papier|handschrift)",
    # Nicht "bildschirm": eine Videowand auf der Buehne ist kein Screenshot.
    "screenshot": r"\bscreenshot",
}

#: Kurze deutsche Anfragen, wie jemand im Suchfeld tippt -> Konzept der Referenz.
ANFRAGEN: dict[str, str] = {
    "Strand": "strand",
    "das Meer": "meer",
    "Berge": "berge",
    "im Wald": "wald",
    "Hochzeit": "hochzeit",
    "Geburtstag": "geburtstag",
    "Weihnachten": "weihnachten",
    "Feuerwerk": "feuerwerk",
    "Party": "party",
    "Essen auf dem Teller": "essen",
    "Grillen": "grillen",
    "Torte": "torte",
    "Bier trinken": "getraenke",
    "Schwimmbad": "schwimmen",
    "Skifahren": "skifahren",
    "Fahrrad": "radfahren",
    "Gruppenfoto": "gruppenfoto",
    "Kinder spielen": "kinder",
    "ein Baby": "baby",
    "Hund": "hund",
    "Katze": "katze",
    "Auto": "auto",
    "Sonnenuntergang": "sonnenuntergang",
    "Schnee": "schnee",
    "ein Dokument": "dokument",
}


def referenz_tags(caption: str | None, tags: list[str] | None) -> set[str]:
    """Konzepte, die Beschreibung und Vision-Tags nennen."""
    text = " ".join([caption or "", " ".join(tags or [])]).lower()
    return {k for k, muster in REFERENZ_MUSTER.items() if re.search(muster, text)}


def tags_nach_regel(probs, labels: list[str], top_k: int, min_prob: float,
                    min_ratio: float) -> list[str]:
    """Dieselbe Auswahl wie `SceneTagger.encode_tensors`, auf einer Zeile."""
    floor = max(min_prob, min_ratio / len(labels))
    order = sorted(range(len(labels)), key=lambda i: -float(probs[i]))[:top_k]
    return [labels[i] for i in order if float(probs[i]) >= floor]


def average_precision(scores, labels: list[str], relevant: set[str]) -> float:
    """AP einer Konzept-Rangfolge gegen die Referenz -- schwellenfrei."""
    if not relevant:
        return 0.0
    order = sorted(range(len(labels)), key=lambda i: -float(scores[i]))
    hits, summe = 0, 0.0
    for rang, i in enumerate(order, start=1):
        if labels[i] in relevant:
            hits += 1
            summe += hits / rang
    return summe / len(relevant)


def prf(vorhersagen: list[list[str]], referenzen: list[set[str]]) -> dict:
    """Mikro-Precision/Recall/F1 ueber alle Fotos."""
    tp = sum(len(set(v) & r) for v, r in zip(vorhersagen, referenzen))
    fp = sum(len(set(v) - r) for v, r in zip(vorhersagen, referenzen))
    fn = sum(len(r - set(v)) for v, r in zip(vorhersagen, referenzen))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f, "tags_je_bild": (tp + fp) / max(1, len(vorhersagen))}


# --------------------------------------------------------------- Daten


def stichprobe(qc, n: int, videos: int, seed: int) -> list[dict]:
    """Fotos mit Beschreibung (zufaellig, fester Seed) plus Videos.

    Videos: alle mit Beschreibung, dann zufaellige ohne bis `videos`.
    """
    from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

    from api.qdrant_util import PHOTOS, not_trashed

    def alle(flt) -> list[dict]:
        out, offset = [], None
        while True:
            batch, offset = qc.scroll(PHOTOS, scroll_filter=flt, limit=1024, offset=offset,
                                      with_payload=["file_path", "kind", "caption_de", "scene_tags"],
                                      with_vectors=False)
            out += [p.payload for p in batch]
            if offset is None:
                return out

    hat_caption = IsEmptyCondition(is_empty=PayloadField(key="caption_de"))
    ist_video = FieldCondition(key="kind", match=MatchValue(value="video"))
    rng = random.Random(seed)
    fotos = alle(Filter(must=[not_trashed()], must_not=[hat_caption, ist_video]))
    fotos.sort(key=lambda p: p["file_path"])
    auswahl = rng.sample(fotos, min(n, len(fotos)))
    if videos:
        vids = alle(Filter(must=[not_trashed(), ist_video]))
        vids.sort(key=lambda p: p["file_path"])
        mit = [v for v in vids if v.get("caption_de")]
        ohne = [v for v in vids if not v.get("caption_de")]
        auswahl += mit + rng.sample(ohne, max(0, min(len(ohne), videos - len(mit))))
    return auswahl


def lade_bilder(eintraege: list[dict]) -> list[tuple[dict, list]]:
    """(Eintrag, [PIL-Bilder]); ein Foto = ein Bild, ein Video = seine Frames."""
    from ingest.pipeline import _load_image
    from ingest.video_clip import sample_frames

    out = []
    for e in eintraege:
        fp = e["file_path"]
        try:
            if e.get("kind") == "video":
                bilder = [img for _, img in sample_frames(fp)]
            else:
                _raw, rgb, _warn = _load_image(fp)
                bilder = [rgb] if rgb is not None else []
        except Exception as ex:  # eine Datei, nicht der Lauf
            logger.warning("uebersprungen: %s (%s)", fp, ex)
            bilder = []
        if bilder:
            out.append((e, bilder))
    return out


# --------------------------------------------------------------- Modelle


def miss_modell(name: str, pretrained: str, daten: list[tuple[dict, list]],
                model_dir: str, batch: int = 32) -> dict:
    """Bild- und Textvektoren eines Modells, plus Zeit und VRAM."""
    import numpy as np
    import open_clip
    import torch

    from ingest.scene_tagger import SCENE_CONCEPTS
    from ingest.video_clip import pick_medoid

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.perf_counter()
    model, _, prep = open_clip.create_model_and_transforms(
        name, pretrained=pretrained, cache_dir=model_dir, device=dev,
        precision="fp16" if dev == "cuda" else "fp32")
    model.eval()
    tok = open_clip.get_tokenizer(name, cache_dir=model_dir)
    laden_s = time.perf_counter() - t0
    if dev == "cuda":
        torch.cuda.reset_peak_memory_stats()
    # Wie ingest/scene_tagger: fp16 auf der GPU. Nicht aus dem ersten Parameter
    # ablesen -- open_clip laesst z. B. class_embedding in fp32 (Fehler am
    # 10.11.2026: "Input type FloatTensor and weight type HalfTensor").
    dtype = torch.float16 if dev == "cuda" else torch.float32

    def text(saetze: list[str]):
        with torch.no_grad():
            f = model.encode_text(tok(saetze).to(dev)).float()
        return torch.nn.functional.normalize(f, dim=-1)

    # Bilder: alle Frames flach, dann je Eintrag zurueckfalten.
    flach = [img for _, bilder in daten for img in bilder]
    feats = []
    if dev == "cuda":
        torch.cuda.synchronize()
    t1 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(flach), batch):
            x = torch.stack([prep(im) for im in flach[i:i + batch]]).to(dev, dtype=dtype)
            feats.append(torch.nn.functional.normalize(model.encode_image(x).float(), dim=-1).cpu())
    if dev == "cuda":
        torch.cuda.synchronize()
    bild_s = time.perf_counter() - t1
    alle = torch.cat(feats).numpy()
    vektoren, pos = [], 0
    for _, bilder in daten:
        block = alle[pos:pos + len(bilder)]
        pos += len(bilder)
        vektoren.append(block[pick_medoid(block.tolist())])
    bildvek = np.stack(vektoren)

    labels = list(SCENE_CONCEPTS)
    konzepte = []
    for lab in labels:
        m = text(SCENE_CONCEPTS[lab]).mean(dim=0)
        konzepte.append(m / m.norm())
    konzeptvek = torch.stack(konzepte).cpu().numpy()
    captions = [e.get("caption_de") or "" for e, _ in daten]
    capvek = text([c for c in captions if c]).cpu().numpy() if any(captions) else None
    anfragevek = text(list(ANFRAGEN)).cpu().numpy()
    skala = float(model.logit_scale.exp().item())
    vram = torch.cuda.max_memory_allocated() / 2**30 if dev == "cuda" else 0.0
    del model
    if dev == "cuda":
        torch.cuda.empty_cache()
    return {"name": f"{name} ({pretrained})", "bild": bildvek, "konzepte": konzeptvek,
            "labels": labels, "captions": capvek, "anfragen": anfragevek, "skala": skala,
            "laden_s": laden_s, "bilder_je_s": len(flach) / bild_s if bild_s else 0.0,
            "vram_gib": vram, "dim": int(bildvek.shape[1])}


#: Gitter fuer `--schwellen` (#66): (top_k, min_ratio, min_prob).
GITTER_TOP_K = (3, 4, 5)
GITTER_MIN_RATIO = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
GITTER_MIN_PROB = (0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20)


def schwellen_suche(probs, labels: list[str], refs: list, ist_video: list[bool]) -> dict:
    """Die Ingest-Regel mit der besten F1 ueber Fotos und Videos zusammen.

    Gewertet wird die Summe beider F1, damit die 45 Videos gegen 300 Fotos
    nicht untergehen -- Videos sind der Bestand, in dem die Tags wirklich
    zaehlen (ohne Beschreibung kommen sie nur vom Bildmodell).
    """
    def f1(maske, regel):
        idx = [i for i, ok in enumerate(maske) if ok and refs[i]]
        if not idx:
            return None
        vor = [tags_nach_regel(probs[i], labels, *regel) for i in idx]
        return prf(vor, [refs[i] for i in idx])

    fotos = [not v for v in ist_video]
    bestes = None
    for k in GITTER_TOP_K:
        for ratio in GITTER_MIN_RATIO:
            for mp in GITTER_MIN_PROB:
                regel = (k, mp, ratio)
                a, b = f1(fotos, regel), f1(ist_video, regel)
                wert = (a["f1"] if a else 0.0) + (b["f1"] if b else 0.0)
                if bestes is None or wert > bestes["wert"] + 1e-9:
                    bestes = {"wert": wert, "top_k": k, "min_prob": mp, "min_ratio": ratio,
                              "fotos": a, "videos": b}
    return bestes or {}


def auswerten(m: dict, daten: list[tuple[dict, list]], video_labels: dict,
              suche: bool = False) -> dict:
    """Alle Kennzahlen eines Modells aus seinen Vektoren."""
    import numpy as np

    from ingest.scene_tagger import DEFAULT_MIN_PROB, DEFAULT_MIN_RATIO, DEFAULT_TOP_K

    labels = m["labels"]
    logits = m["skala"] * (m["bild"] @ m["konzepte"].T)
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)

    refs = []
    for e, _ in daten:
        if e.get("kind") == "video" and e["file_path"] in video_labels:
            refs.append(set(video_labels[e["file_path"]]))
        elif e.get("caption_de"):
            refs.append(referenz_tags(e.get("caption_de"), e.get("scene_tags")))
        else:
            refs.append(None)  # keine Referenz -> zaehlt nicht

    def tagmasse(maske) -> dict:
        idx = [i for i, ok in enumerate(maske) if ok and refs[i]]
        if not idx:
            return {"n": 0}
        vor = [tags_nach_regel(probs[i], labels, DEFAULT_TOP_K, DEFAULT_MIN_PROB,
                               DEFAULT_MIN_RATIO) for i in idx]
        r = [refs[i] for i in idx]
        out = prf(vor, r)
        out["top1"] = float(np.mean([labels[int(np.argmax(probs[i]))] in refs[i] for i in idx]))
        out["mAP"] = float(np.mean([average_precision(probs[i], labels, refs[i]) for i in idx]))
        out["n"] = len(idx)
        return out

    ist_video = [e.get("kind") == "video" for e, _ in daten]
    erg = {"modell": m["name"], "dim": m["dim"], "bilder_je_s": m["bilder_je_s"],
           "vram_gib": m["vram_gib"], "laden_s": m["laden_s"],
           "tags_fotos": tagmasse([not v for v in ist_video]),
           "tags_videos": tagmasse(ist_video)}
    if suche:
        erg["schwellen"] = schwellen_suche(probs, labels, refs, ist_video)

    # Beschreibung -> eigenes Bild, gegen alle Kandidaten (Fotos + Videos).
    mit_cap = [i for i, (e, _) in enumerate(daten) if e.get("caption_de")]
    if m["captions"] is not None and mit_cap:
        sim = m["captions"] @ m["bild"].T  # [n_cap, n_alle]
        # Spalten so umordnen, dass Anfrage q zu Spalte q gehoert.
        rest = [i for i in range(len(daten)) if i not in set(mit_cap)]
        sim = sim[:, mit_cap + rest]
        diag = sim[np.arange(len(mit_cap)), np.arange(len(mit_cap))]
        rang = (sim > diag[:, None]).sum(axis=1)
        erg["beschreibung_zu_bild"] = {f"R@{k}": float((rang < k).mean()) for k in (1, 5, 10)}
        erg["beschreibung_zu_bild"]["n"] = len(mit_cap)
        erg["beschreibung_zu_bild"]["kandidaten"] = len(daten)

    # Kurze deutsche Anfragen: Anteil Referenztreffer unter den ersten zehn.
    sim = m["anfragen"] @ m["bild"].T
    hat_ref = np.array([r is not None for r in refs])
    werte, je = [], {}
    for q, (text, konzept) in enumerate(ANFRAGEN.items()):
        relevant = np.array([bool(r) and konzept in r for r in refs])
        if relevant.sum() == 0:
            continue
        s = np.where(hat_ref, sim[q], -np.inf)  # nur Bilder, ueber die wir urteilen koennen
        top = np.argsort(-s)[:10]
        wert = float(relevant[top].sum() / min(10, relevant.sum()))
        werte.append(wert)
        je[text] = round(wert, 2)
    erg["anfragen_de"] = {"treffer@10": float(np.mean(werte)) if werte else 0.0,
                          "n_anfragen": len(werte), "je_anfrage": je}
    return erg


def tabelle(ergebnisse: list[dict]) -> str:
    """Markdown, wie es in docs/performance.md steht."""
    def f(x):
        return f"{x:.2f}" if isinstance(x, float) else str(x)

    zeilen = ["| Kennzahl | " + " | ".join(e["modell"] for e in ergebnisse) + " |",
              "|---|" + "---|" * len(ergebnisse)]

    def zeile(titel, weg):
        werte = []
        for e in ergebnisse:
            v = e
            for k in weg:
                v = v.get(k, {}) if isinstance(v, dict) else {}
            werte.append(f(v) if v != {} else "–")
        zeilen.append(f"| {titel} | " + " | ".join(werte) + " |")

    zeile("Fotos: Tags F1 (Ingest-Regel)", ["tags_fotos", "f1"])
    zeile("Fotos: Tags Precision", ["tags_fotos", "precision"])
    zeile("Fotos: Tags Recall", ["tags_fotos", "recall"])
    zeile("Fotos: Tags je Bild", ["tags_fotos", "tags_je_bild"])
    zeile("Fotos: bestes Konzept richtig (top1)", ["tags_fotos", "top1"])
    zeile("Fotos: mAP (schwellenfrei)", ["tags_fotos", "mAP"])
    zeile("Videos: Tags F1", ["tags_videos", "f1"])
    zeile("Videos: top1", ["tags_videos", "top1"])
    zeile("Videos: mAP", ["tags_videos", "mAP"])
    zeile("Beschreibung (de) → Bild R@1", ["beschreibung_zu_bild", "R@1"])
    zeile("Beschreibung (de) → Bild R@5", ["beschreibung_zu_bild", "R@5"])
    zeile("Beschreibung (de) → Bild R@10", ["beschreibung_zu_bild", "R@10"])
    zeile("Kurze deutsche Anfragen, Treffer@10", ["anfragen_de", "treffer@10"])
    zeile("Bilder/s (fp16, Stapel 32, inkl. Vorverarbeitung)", ["bilder_je_s"])
    zeile("VRAM-Spitze GiB", ["vram_gib"])
    zeile("Vektorgröße", ["dim"])
    return "\n".join(zeilen)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=300, help="Fotos mit Beschreibung")
    ap.add_argument("--videos", type=int, default=60)
    ap.add_argument("--seed", type=int, default=59)
    ap.add_argument("--video-labels", help="JSON {file_path: [konzept, ...]}")
    ap.add_argument("--model-dir", default=os.environ.get(
        "MODEL_DIR", str(Path.home() / ".cache" / "photovault-models")))
    ap.add_argument("--out", help="Ergebnisse als JSON")
    ap.add_argument("--liste", help="nur die Stichprobe als JSON schreiben, nichts rechnen")
    ap.add_argument("--schwellen", action="store_true",
                    help="je Modell die Ingest-Regel mit der besten Tag-F1 suchen (#66)")
    ap.add_argument("--nur", help="nur dieses Modell (open_clip-Name)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from api.qdrant_util import client

    eintraege = stichprobe(client(), args.n, args.videos, args.seed)
    if args.liste:
        Path(args.liste).write_text(json.dumps(eintraege, ensure_ascii=False, indent=1))
        print(f"{len(eintraege)} Eintraege -> {args.liste}")
        return 0
    video_labels = json.loads(Path(args.video_labels).read_text()) if args.video_labels else {}
    daten = lade_bilder(eintraege)
    logger.info("%d von %d Eintraegen geladen", len(daten), len(eintraege))
    ergebnisse = []
    for name, pre in MODELLE:
        if args.nur and name != args.nur:
            continue
        logger.info("Modell %s (%s)", name, pre)
        ergebnisse.append(auswerten(miss_modell(name, pre, daten, args.model_dir), daten,
                                    video_labels, suche=args.schwellen))
    md = tabelle(ergebnisse)
    print(md)
    for e in ergebnisse:
        b = e.get("schwellen")
        if b:
            print(f"{e['modell']} beste Regel: top_k={b['top_k']} min_ratio={b['min_ratio']} "
                  f"min_prob={b['min_prob']} -> F1 Fotos "
                  f"{(b['fotos'] or {}).get('f1', 0):.2f}, Videos {(b['videos'] or {}).get('f1', 0):.2f}")
    for e in ergebnisse:
        print(e["modell"], json.dumps(e["anfragen_de"]["je_anfrage"], ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(ergebnisse, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
