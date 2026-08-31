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
"""
from __future__ import annotations

import logging

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


class SceneTagger:
    def __init__(
        self,
        model_dir: str = "/models",
        threshold: float = DEFAULT_MIN_PROB,
        top_k: int = DEFAULT_TOP_K,
        min_ratio: float = DEFAULT_MIN_RATIO,
    ):
        self._model = None
        self._processor = None
        self._text_features = None
        self._labels: list[str] = []
        self._model_dir = model_dir
        self._min_prob = threshold
        self._top_k = top_k
        self._min_ratio = min_ratio

    def _ensure_loaded(self):
        if self._model is not None:
            return
        import open_clip
        import torch

        logger.info("Loading CLIP ViT-L/14...")
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        # fp16 auf der GPU: halber VRAM-Bedarf und schneller. CLIP wurde in
        # fp16 trainiert, der Qualitaetsunterschied ist vernachlaessigbar --
        # und auf dieser Maschine haelt Ollama dauerhaft 21 GB belegt.
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32
        self._model, _, self._processor = open_clip.create_model_and_transforms(
            "ViT-L-14",
            pretrained="openai",
            cache_dir=self._model_dir,
            device=self._device,
            precision="fp16" if self._dtype is torch.float16 else "fp32",
        )
        self._model.eval()

        # Pro Konzept alle Formulierungen einbetten und mitteln -- das ist
        # robuster als eine einzelne Formulierung (Prompt-Ensembling).
        self._labels = list(SCENE_CONCEPTS.keys())
        per_concept = []
        with torch.no_grad():
            for label in self._labels:
                tokens = open_clip.tokenize(SCENE_CONCEPTS[label]).to(self._device)
                feats = self._model.encode_text(tokens).float()
                feats /= feats.norm(dim=-1, keepdim=True)
                mean = feats.mean(dim=0)
                mean /= mean.norm()
                per_concept.append(mean)
            self._text_features = torch.stack(per_concept)
        logger.info(
            "CLIP loaded on %s, %d concepts (%d prompts)",
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
            probs = (100.0 * feats @ self._text_features.T).softmax(dim=-1)
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
