"""Was diese Installation kann — an einer Stelle, nicht pro Funktion neu.

Ein `pip install` beantwortet die Frage nicht, und zwar aus drei Gruenden,
die alle in diesem Projekt zutreffen:

1. **Ollama ist kein Python-Paket.** Es ist ein eigener Dienst auf dem Host,
   der laufen muss und dessen Modelle gezogen sein muessen. Kein Paketmanager
   kann das zusagen -- und `docker compose` auch nicht, denn Ollama laeuft
   absichtlich ausserhalb des Verbunds (GPU-Durchreichung, zweistellige
   Gigabyte).
2. **Manches ist absichtlich optional.** `umap-learn` zieht numba und llvmlite
   fuer einen einzigen Befehl. Verpflichtend gemacht, zahlt das jeder mit, der
   die Karte nie rechnet.
3. **Hardware laesst sich nicht deklarieren.** `onnxruntime` und
   `onnxruntime-gpu` sind zwei Pakete fuer denselben Import, und ob die
   CUDA-Version passt, entscheidet die Maschine.

Deshalb beides: deklarieren, was deklarierbar ist (`[atlas]`-Extra), und zur
Laufzeit pruefen, was nicht. Die Oberflaeche fragt einmal und bietet dann
nichts an, was hier nicht steht.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ingest.ollama_client import CAPTION_MODEL, EMBED_MODEL, ollama_url

logger = logging.getLogger(__name__)

ATLAS_FILE = Path(__file__).resolve().parent.parent / "web" / "static" / "atlas" / "atlas.json"

#: Kurz gecacht: Ollama kann jederzeit starten, ein Paket jederzeit
#: dazukommen. Laenger waere gelogen, kuerzer kostet bei jedem Seitenaufruf
#: einen HTTP-Rundlauf.
TTL_SECONDS = 15.0

_cache: tuple[float, dict] = (0.0, {})

#: „noch nicht nachgesehen" -- unterscheidbar von `None`, das „Ollama
#: antwortet nicht" bedeutet.
UNCHECKED: Any = object()


def ollama_models() -> Optional[set[str]]:
    """Welche Modelle liegen bereit? `None` heisst: Ollama antwortet nicht."""
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    return {str(m.get("name") or "") for m in data.get("models", [])}


def missing(
    modules: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
    hint: str = "",
    have_models: Any = UNCHECKED,
) -> str:
    """Was fehlt? Leerer Text heisst: nichts.

    Pakete zuerst, denn das ist ohne Netz feststellbar -- sonst wartet die
    Antwort auf einen Zeitablauf, obwohl sie schon feststeht.
    """
    gone = [m for m in modules if importlib.util.find_spec(m) is None]
    if gone:
        return f"{', '.join(gone)} nicht installiert. {hint}".strip()
    if models:
        pool = ollama_models() if have_models is UNCHECKED else have_models
        if pool is None:
            return f"Ollama nicht erreichbar ({ollama_url()}). {hint}".strip()
        absent = [m for m in models if m not in pool]
        if absent:
            return f"Modell fehlt: {', '.join(absent)}. {hint}".strip()
    return ""


#: Jede Zeile ist etwas, das die Oberflaeche anbietet oder eben nicht.
#: `lost` sagt, was ohne sie fehlt -- das ist die Auskunft, die zaehlt.
FEATURES: dict[str, dict] = {
    "freetext": {
        "label": "Freitextsuche",
        "models": (EMBED_MODEL,),
        "hint": f"Ollama starten und `ollama pull {EMBED_MODEL}`.",
        "lost": "Suche nach Personen, Jahr, Ort, Album und Tags funktioniert weiter — "
                "nur das Sortieren nach einem getippten Satz nicht.",
    },
    "captions": {
        "label": "Bildbeschreibungen",
        "models": (CAPTION_MODEL,),
        "hint": f"Ollama starten und `ollama pull {CAPTION_MODEL}`.",
        "lost": "Die Kontinente der Karte tragen dann ihre Szenen-Tags als Namen "
                "statt der Beschreibungen.",
    },
    "reembed": {
        "label": "Text-Vektoren neu rechnen",
        "models": (EMBED_MODEL,),
        "hint": f"Ollama starten und `ollama pull {EMBED_MODEL}`.",
        "lost": "Notizen und Beschreibungen greifen trotzdem als Filter — nur in der "
                "Rangfolge der Freitextsuche nicht.",
    },
    "atlas_build": {
        "label": "Karte rechnen",
        "modules": ("umap", "sklearn"),
        "hint": "pip install 'photovault[atlas]'",
        "lost": "Eine bereits gerechnete Karte bleibt benutzbar; sie veraltet nur.",
    },
}


def snapshot() -> dict:
    """Der ganze Zustand, kurz gecacht."""
    now = time.time()
    stamp, cached = _cache
    if cached and now - stamp < TTL_SECONDS:
        return cached

    pool = ollama_models()
    features = {}
    for key, spec in FEATURES.items():
        why = missing(
            modules=tuple(spec.get("modules", ())),
            models=tuple(spec.get("models", ())),
            hint=spec.get("hint", ""),
            have_models=pool,
        )
        features[key] = {
            "label": spec["label"],
            "ok": not why,
            "why": why,
            "lost": spec["lost"] if why else "",
        }

    # Die Karte ist eine Datei, nicht ein Paket -- gerechnet oder nicht.
    features["atlas_map"] = {
        "label": "Karte vorhanden",
        "ok": ATLAS_FILE.is_file(),
        "why": "" if ATLAS_FILE.is_file() else "Noch nicht gerechnet.",
        "lost": "" if ATLAS_FILE.is_file() else "Der Tab Atlas bleibt leer.",
    }

    state = {
        "ollama": {"url": ollama_url(), "reachable": pool is not None,
                   "models": sorted(pool) if pool else []},
        "accelerator": _accelerator(),
        "features": features,
    }
    _cache_set(now, state)
    return state


def _cache_set(stamp: float, state: dict) -> None:
    global _cache
    _cache = (stamp, state)


_accel: Optional[dict] = None


def _accelerator() -> dict:
    """Nur zur Auskunft: nichts haengt davon ab, ob eine Grafikkarte da ist.

    `torch` wird bewusst nicht importiert -- das kostet Sekunden und Speicher
    in einem Webprozess, der es sonst nie braucht. `onnxruntime` bringt
    insightface ohnehin mit.
    """
    global _accel
    if _accel is not None:
        return _accel
    providers: list[str] = []
    try:
        import onnxruntime as ort

        providers = list(ort.get_available_providers())
    except Exception:
        pass
    _accel = {
        "onnxruntime_providers": providers,
        "cuda": any("CUDA" in p or "Tensorrt" in p for p in providers),
    }
    return _accel
