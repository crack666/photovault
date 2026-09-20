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
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ingest.ollama_client import (
    caption_model,
    embed_model,
    litellm_headers,
    litellm_url,
    ollama_url,
)
from ingest.settings import llm_mode

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


def llm_models() -> Optional[set[str]]:
    """Pool-Aliase bei LiteLLM, sonst Ollama-Tags.

    PhotoVault kennt `local` und `embedder`. Ob dahinter ctx8k oder ein
    anderes Gewicht haengt, sieht nur der Proxy.
    """
    pool = litellm_url()
    if not pool:
        return ollama_models()
    try:
        req = urllib.request.Request(f"{pool}/v1/models", headers=litellm_headers())
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    return {str(m.get("id") or "") for m in data.get("data", [])}


#: Die zwei Rollen, die ein Sprachmodell hier hat. Welcher Name dahinter
#: steht, entscheidet die Umgebung oder der Setup-Wizard -- zur Laufzeit,
#: nicht beim Import (`ingest.settings`).
KINDS = ("caption", "embed")
KIND_LABEL = {"caption": "Bildbeschreibungen", "embed": "Text-Embeddings"}


def model_for(kind: str) -> str:
    """Der Modellname, wie er jetzt gilt; leer = nichts gewaehlt."""
    return caption_model() if kind == "caption" else embed_model()


def hint_for(kind: str, model: str = "") -> str:
    """Die Abhilfe haengt am Modus, nicht am Merkmal.

    Mit Pool ist es die LiteLLM-Config, mit Ollama ein `pull`, in der Cloud
    die Zugangsdaten -- und ohne gewaehltes Modell der Wizard. Ein fester
    Satz "LiteLLM starten" war fuer den Fremden mit Ollama schlicht falsch.
    """
    mode = llm_mode()
    if mode == "off":
        return "Im Setup ist das Sprachmodell ausgeschaltet."
    if not model:
        return "Im Setup ein Modell waehlen."
    if os.environ.get("LITELLM_URL"):
        return f"LiteLLM starten; Pool `{model}` muss in der Config stehen."
    if mode == "openai":
        return "Cloud-Zugang im Setup pruefen: Adresse, Schluessel, Modellname."
    return f"Ollama starten und `ollama pull {model}` -- oder im Setup ziehen."


def missing(
    modules: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
    hint: str = "",
    have_models: Any = UNCHECKED,
    kinds: tuple[str, ...] = (),
) -> str:
    """Was fehlt? Leerer Text heisst: nichts.

    Pakete zuerst, denn das ist ohne Netz feststellbar -- sonst wartet die
    Antwort auf einen Zeitablauf, obwohl sie schon feststeht. `kinds` sind
    Modellrollen, die erst hier zu Namen werden; `models` sind fertige Namen
    (Aufrufer, die es genau wissen).
    """
    gone = [m for m in modules if importlib.util.find_spec(m) is None]
    if gone:
        return f"{', '.join(gone)} nicht installiert. {hint}".strip()
    wanted: list[tuple[str, str]] = [(m, hint) for m in models]
    for kind in kinds:
        name = model_for(kind)
        if not name:
            return f"Kein Modell fuer {KIND_LABEL.get(kind, kind)} gewaehlt. {hint_for(kind)}".strip()
        wanted.append((name, hint or hint_for(kind, name)))
    if wanted:
        pool = llm_models() if have_models is UNCHECKED else have_models
        if pool is None:
            target = litellm_url() or ollama_url()
            return f"LLM-Pool nicht erreichbar ({target}). {wanted[0][1]}".strip()
        absent = [(m, h) for m, h in wanted if m not in pool]
        if absent:
            return f"Modell fehlt: {', '.join(m for m, _ in absent)}. {absent[0][1]}".strip()
    return ""


#: Jede Zeile ist etwas, das die Oberflaeche anbietet oder eben nicht.
#: `lost` sagt, was ohne sie fehlt -- das ist die Auskunft, die zaehlt.
FEATURES: dict[str, dict] = {
    "freetext": {
        "label": "Freitextsuche",
        "kinds": ("embed",),
        "lost": "Suche nach Personen, Jahr, Ort, Album und Tags funktioniert weiter — "
                "nur das Sortieren nach einem getippten Satz nicht.",
    },
    "captions": {
        "label": "Bildbeschreibungen",
        "kinds": ("caption",),
        "lost": "Die Kontinente der Karte tragen dann ihre Szenen-Tags als Namen "
                "statt der Beschreibungen.",
    },
    "reembed": {
        "label": "Text-Vektoren neu rechnen",
        "kinds": ("embed",),
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

    pool = llm_models() if any(spec.get("kinds") for spec in FEATURES.values()) else None
    features = {}
    for key, spec in FEATURES.items():
        why = missing(
            modules=tuple(spec.get("modules", ())),
            kinds=tuple(spec.get("kinds", ())),
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

    from api.archive import why_unavailable

    archiv = why_unavailable()
    features["archive"] = {
        "label": "Fotoarchiv",
        "ok": not archiv,
        "why": archiv or "",
        "lost": "" if not archiv else (
            "Vorschaubilder und Originale fehlen — der Index bleibt lesbar."
        ),
    }

    state = {
        "ollama": {"url": litellm_url() or ollama_url(), "reachable": pool is not None,
                   "models": sorted(pool) if pool else []},
        "llm": {"mode": llm_mode(), "caption_model": caption_model(),
                "embed_model": embed_model()},
        "accelerator": _accelerator(),
        "features": features,
    }
    _cache_set(now, state)
    return state


def _cache_set(stamp: float, state: dict) -> None:
    global _cache
    _cache = (stamp, state)


def forget() -> None:
    """Nach einer Aenderung im Setup: beim naechsten Blick neu nachsehen."""
    _cache_set(0.0, {})


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
