"""Scene Tagging + Visual Embedding via CLIP.

Zero-shot-CLIP liefert *keine* kalibrierten Wahrscheinlichkeiten. Ein absoluter
Schwellwert auf rohe Cosinus-Scores produziert darum Rauschen: bei 129 Kategorien
und Schwelle 0.18 kamen im Test 7 Tags pro Foto heraus, darunter "neugeborenes"
auf 25 % aller Fotos einer Abiball-/Silvester-Stichprobe.

Drei Korrekturen:
1. Prompt-Templates statt nackter Woerter -- CLIP ist auf Bildunterschriften
   trainiert, einzelne Substantive sind out-of-distribution.
2. Softmax ueber die Konzepte + relative Schwelle, statt absolutem Cosinus.
3. Synonyme (deutsch/englisch) sind *ein* Konzept mit einem Ausgabe-Label,
   nicht zwei konkurrierende Kategorien.

Welches Bildmodell rechnet, ist eine Einstellung (`IMAGE_MODELS`, #66):
`clip` (OpenAI ViT-L/14, 768 d, Vektor `clip` in `photos`) oder `siglip2`
(ViT-SO400M-14-SigLIP2, 1152 d, Vektor `siglip2` in der Beistell-Collection
`photos_siglip2`). Die Regel ist fuer beide dieselbe; Temperatur und
Schwellen gehoeren zum Modell.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# label -> Formulierungen, die dasselbe Konzept beschreiben (DE + EN).
# Das Label ist die Ausgabe; die Formulierungen sind nur Prompts fuer CLIP.
SCENE_CONCEPTS: dict[str, list[str]] = {
    "strand": ["a photo of a beach", "people on a sandy beach"],
    "meer": ["a photo of the sea", "the ocean"],
    "berge": ["a photo of mountains", "a mountain landscape"],
    "wald": ["a photo of a forest", "trees in a forest"],
    "stadt": ["a photo of a city street", "buildings in a city"],
    "innenraum": ["a photo taken indoors", "the inside of a room"],
    "draussen": ["a photo taken outdoors", "an outdoor scene"],
    "schiff": ["a photo of a boat", "a ship on the water"],
    "geburtstag": ["a birthday party", "a birthday cake with candles"],
    "hochzeit": ["a wedding", "a bride and groom"],
    "abschluss": ["a graduation ceremony", "students in graduation gowns"],
    "weihnachten": ["a christmas celebration", "a christmas tree"],
    "silvester": ["a new year's eve party", "fireworks at midnight"],
    "feuerwerk": ["fireworks in the night sky", "a firework display"],
    "party": ["a party with people celebrating", "a nightclub party"],
    "konzert": ["a concert", "a band playing on stage"],
    "restaurant": ["people eating at a restaurant", "a dinner table with food"],
    "essen": ["a photo of food", "a plate of food"],
    "grillen": ["a barbecue", "grilling food outdoors"],
    "torte": ["a cake", "a decorated cake"],
    "getraenke": ["drinks and glasses", "people holding beer glasses"],
    "tanzen": ["people dancing", "a dance floor"],
    "sport": ["people playing sports", "a sports match"],
    "schwimmen": ["people swimming", "a swimming pool"],
    "wandern": ["people hiking", "a hiking trail"],
    "skifahren": ["skiing in the snow", "a ski slope"],
    "radfahren": ["riding a bicycle", "cyclists on a road"],
    "urlaub": ["a vacation photo", "tourists sightseeing"],
    "portraet": ["a portrait of one person", "a close-up of a face"],
    "gruppenfoto": ["a group photo of many people", "a large group posing"],
    "kinder": ["children playing", "a photo of kids"],
    "baby": ["a baby", "a newborn infant"],
    "paar": ["a couple together", "two people embracing"],
    "hund": ["a dog", "a photo of a dog"],
    "katze": ["a cat", "a photo of a cat"],
    "auto": ["a car", "a photo of a car"],
    "landschaft": ["a landscape photo", "scenic countryside"],
    "gebaeude": ["a building", "architecture"],
    "sonnenuntergang": ["a sunset", "the sun setting over the horizon"],
    "nacht": ["a photo taken at night", "a dark night scene"],
    "schnee": ["snow", "a snowy winter scene"],
    "regen": ["rain", "a rainy day"],
    "dokument": ["a scanned document", "a page of text"],
    "screenshot": ["a screenshot of a computer screen", "a phone screenshot"],
}

DEFAULT_TOP_K = 5
# Ein Tag muss mindestens so wahrscheinlich sein wie das Doppelte des
# Gleichverteilungs-Niveaus (1/N) -- sonst ist es nicht besser als Raten.
DEFAULT_MIN_RATIO = 2.0
# Und es muss absolut relevant sein, damit uninformative Bilder leer bleiben.
DEFAULT_MIN_PROB = 0.04


@dataclass(frozen=True)
class ImageModel:
    """Ein Bildmodell samt der Stelle, an der sein Vektor im Index liegt."""

    key: str
    name: str          # open_clip-Architektur
    pretrained: str
    dim: int
    collection: str    # wo der Vektor liegt
    vector: str        # Name des Vektors dort
    #: Softmax-Temperatur ueber die Konzepte. CLIP: fest 100 wie seit jeher
    #: (entspricht seinem logit_scale). None: `logit_scale` des Modells.
    temperature: float | None
    top_k: int
    min_ratio: float
    min_prob: float


#: Die Schwellen von SigLIP 2 sind mit `tools.clip_vergleich --schwellen`
#: eingestellt (docs/performance.md, #66), die von CLIP sind die alten.
IMAGE_MODELS: dict[str, ImageModel] = {
    "clip": ImageModel("clip", "ViT-L-14", "openai", 768, "photos", "clip",
                       100.0, DEFAULT_TOP_K, DEFAULT_MIN_RATIO, DEFAULT_MIN_PROB),
    "siglip2": ImageModel("siglip2", "ViT-SO400M-14-SigLIP2", "webli", 1152,
                          "photos_siglip2", "siglip2",
                          None, DEFAULT_TOP_K, DEFAULT_MIN_RATIO, DEFAULT_MIN_PROB),
}

#: Vorgabe, wenn `PHOTOVAULT_IMAGE_MODEL` nicht gesetzt ist.
DEFAULT_IMAGE_MODEL = "clip"


def image_model(key: str | None = None) -> ImageModel:
    """Das gewaehlte Bildmodell: Argument > `PHOTOVAULT_IMAGE_MODEL` > Vorgabe.

    Ein unbekannter Name ist ein Fehler, kein stiller Rueckfall: sonst
    rechnete ein Tippfehler in der Umgebung wochenlang mit dem falschen
    Modell in den falschen Vektor.
    """
    k = (key or os.environ.get("PHOTOVAULT_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL).strip().lower()
    if k not in IMAGE_MODELS:
        raise ValueError(f"Unbekanntes Bildmodell {k!r}, bekannt: {', '.join(IMAGE_MODELS)}")
    return IMAGE_MODELS[k]


class SceneTagger:
    def __init__(
        self,
        model_dir: str = "/models",
        threshold: float | None = None,
        top_k: int | None = None,
        min_ratio: float | None = None,
        model: str | None = None,
    ):
        self.spec = image_model(model)
        self._model = None
        self._processor = None
        self._text_features = None
        self._labels: list[str] = []
        self._model_dir = model_dir
        self._min_prob = self.spec.min_prob if threshold is None else threshold
        self._top_k = self.spec.top_k if top_k is None else top_k
        self._min_ratio = self.spec.min_ratio if min_ratio is None else min_ratio
        self._temperature = self.spec.temperature

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import open_clip
        import torch

        logger.info("Loading %s (%s)...", self.spec.name, self.spec.pretrained)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        # fp16 auf der GPU: halber VRAM-Bedarf und schneller. CLIP wurde in
        # fp16 trainiert, der Qualitaetsunterschied ist vernachlaessigbar --
        # und auf dieser Maschine haelt Ollama dauerhaft 21 GB belegt.
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32
        self._model, _, self._processor = open_clip.create_model_and_transforms(
            self.spec.name,
            pretrained=self.spec.pretrained,
            cache_dir=self._model_dir,
            device=self._device,
            precision="fp16" if self._dtype is torch.float16 else "fp32",
        )
        self._model.eval()
        # SigLIP bringt einen eigenen Tokenizer mit (Gemma, ueber
        # `transformers`); fuer CLIP ist es derselbe wie open_clip.tokenize.
        tokenize = open_clip.get_tokenizer(self.spec.name, cache_dir=self._model_dir)
        if self._temperature is None:
            self._temperature = float(self._model.logit_scale.exp().item())

        # Pro Konzept alle Formulierungen einbetten und mitteln -- das ist
        # robuster als eine einzelne Formulierung (Prompt-Ensembling).
        self._labels = list(SCENE_CONCEPTS.keys())
        per_concept = []
        with torch.no_grad():
            for label in self._labels:
                tokens = tokenize(SCENE_CONCEPTS[label]).to(self._device)
                feats = self._model.encode_text(tokens).float()
                feats /= feats.norm(dim=-1, keepdim=True)
                mean = feats.mean(dim=0)
                mean /= mean.norm()
                per_concept.append(mean)
            self._text_features = torch.stack(per_concept)
        logger.info(
            "%s loaded on %s, %d concepts (%d prompts)",
            self.spec.key,
            self._device,
            len(self._labels),
            sum(len(v) for v in SCENE_CONCEPTS.values()),
        )

    def process(self, file_path: str, image=None) -> dict:
        """`image` ist ein bereits geladenes PIL-Image (RGB) -- spart das
        erneute Dekodieren derselben Datei."""
        result: dict = {"tags": [], "embedding": None}
        try:
            from PIL import Image

            self._ensure_loaded()
            if image is None:
                image = Image.open(file_path).convert("RGB")
            result.update(self.process_image(image))
        except Exception as e:
            logger.warning("Scene tagging failed for %s: %s", file_path, e)
        return result

    def process_image(self, image) -> dict:
        """Wie process(), aber auf einem bereits geladenen PIL-Image.

        Erspart der Pipeline ein zweites JPEG-Dekodieren pro Foto.
        """
        return self.process_images([image])[0]

    def preprocess(self, image):
        """Bild auf 224x224 bringen -- reine CPU-Arbeit.

        Gehoert in den Reader-Pool: am GPU-Thread haengend wuerde sie ihn
        ausbremsen, obwohl dort nur die Inferenz laufen soll.
        """
        self._ensure_loaded()
        return self._processor(image)

    def encode_tensors(self, tensors: list) -> list[dict]:
        """Fertig vorverarbeitete Tensoren als Stapel durch CLIP."""
        import torch

        self._ensure_loaded()
        if not tensors:
            return []
        batch = torch.stack(tensors).to(self._device, dtype=self._dtype)
        with torch.no_grad():
            feats = self._model.encode_image(batch).float()
            feats /= feats.norm(dim=-1, keepdim=True)
            probs = (self._temperature * feats @ self._text_features.T).softmax(dim=-1)
            k = min(self._top_k, len(self._labels))
            top = torch.topk(probs, k=k, dim=-1)
            floor = max(self._min_prob, self._min_ratio / len(self._labels))
            out = []
            for row in range(len(tensors)):
                tags = [
                    self._labels[i]
                    for i, p in zip(top.indices[row].tolist(), top.values[row].tolist())
                    if p >= floor
                ]
                out.append({"tags": tags, "embedding": feats[row].tolist()})
            return out

    def process_images(self, images: list) -> list[dict]:
        """Mehrere Bilder in einem GPU-Durchlauf.

        Ein einzelnes 224x224-Bild lastet eine 5090 nicht annaehernd aus --
        der Aufruf-Overhead dominiert. Als Stapel faellt er einmal statt
        `n` mal an.
        """
        import torch

        self._ensure_loaded()
        if not images:
            return []
        return self.encode_tensors([self._processor(im) for im in images])
