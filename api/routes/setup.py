"""Der Setup-Wizard, soweit er Server ist: Zustand, Sprachmodell, Fertig.

Die Ordnerwahl selbst hat schon eine Schnittstelle (`/api/sources`: Baum,
Haken, Trockenlauf) und das Einlesen auch (`/api/jobs/run`). Was fehlte,
war der Teil, den ein Fremder ohne diese Maschine nicht hinbekommt: welches
Sprachmodell, wo, wie gross -- und die Antwort darauf, ob es auf *seinem*
Rechner ueberhaupt in vertretbarer Zeit laeuft.

Drei Dinge sind hier absichtlich so und nicht anders:

*Der Browser spricht nicht selbst mit Ollama.* Alles geht ueber diese API,
auch das Ziehen eines Modells. Sonst ginge der Wizard nur auf dem Rechner,
auf dem Ollama laeuft -- und PhotoVault wird auch vom Handy aus bedient.

*Die Empfehlung kommt aus gemessenem Speicher, nicht aus dem Kartennamen.*
`start.bat` schreibt `GPU_VRAM_MB` aus `nvidia-smi`; eine 5060 Ti gibt es
mit 8 und mit 16 GB, und nur die Zahl unterscheidet die beiden.

*Eine Test-Caption mit Stoppuhr, bevor jemand einen Lauf ueber Tage
startet.* Auf einer CPU dauert ein Bild eine halbe Minute oder mehr; der
Satz "4 800 Fotos, etwa 2 Tage" gehoert vor den Startknopf, nicht ins
Protokoll danach.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import capabilities as cap
from api.qdrant_util import PHOTOS, client
from ingest import settings

logger = logging.getLogger(__name__)
router = APIRouter()

#: Was zu welchem Speicher passt. Gemessen am 19.09.2026 gegen die
#: Ollama-Bibliothek: `qwen3.8` gibt es nur als 27B (18 GB) -- fuer 16 GB
#: muss eine andere Familie her, und `gemma4` hat die Leiter mit Bild-
#: Eingang: 12b (7,6 GB), e4b-it-qat (6,1 GB), e2b-it-qat (4,3 GB). Der
#: Embedder wechselt unter 12 GB auf 0.6b (639 MB), damit beide Modelle
#: nebeneinander in die Karte passen; die Dimension misst der Wizard.
#: Ollama-Tags wandern -- die Zahlen hier sind ein Stand, kein Gesetz.
RECOMMENDATIONS: tuple[dict[str, Any], ...] = (
    {"min_mb": 24_000, "caption_model": "qwen3.8:27b", "caption_gb": 18.0,
     "embed_model": "qwen3-embedding:4b", "embed_gb": 2.5,
     "why": "Ab 24 GB passt das 27B-Modell, mit dem PhotoVault entwickelt wurde."},
    {"min_mb": 12_000, "caption_model": "gemma4:12b", "caption_gb": 7.6,
     "embed_model": "qwen3-embedding:4b", "embed_gb": 2.5,
     "why": "12 bis 23 GB: das 27B-Modell (18 GB) passt nicht mehr, gemma4:12b "
            "beschreibt Bilder und laesst Platz fuer den Embedder."},
    {"min_mb": 8_000, "caption_model": "gemma4:e4b-it-qat", "caption_gb": 6.1,
     "embed_model": "qwen3-embedding:0.6b", "embed_gb": 0.64,
     "why": "8 bis 11 GB: ein 4B-Modell mit Bild-Eingang und der kleine Embedder."},
    {"min_mb": 0, "caption_model": "gemma4:e2b-it-qat", "caption_gb": 4.3,
     "embed_model": "qwen3-embedding:0.6b", "embed_gb": 0.64,
     "why": "Ohne (oder mit kleiner) Grafikkarte rechnet der Prozessor: das "
            "kleinste Modell mit Bild-Eingang -- und vorher eine Test-Caption "
            "mit Stoppuhr, ob das hier tragbar ist."},
)


def recommend(vram_mb: int) -> dict[str, Any]:
    """Die Empfehlung zum gemessenen Speicher; 0 heisst: keine NVIDIA-Karte."""
    vram_mb = max(0, int(vram_mb or 0))
    for row in RECOMMENDATIONS:
        if vram_mb >= row["min_mb"]:
            return {**row, "vram_mb": vram_mb, "cpu": vram_mb < 6_000}
    return {**RECOMMENDATIONS[-1], "vram_mb": vram_mb, "cpu": True}


def vram_mb() -> int:
    """Aus der Umgebung -- `start.bat` misst mit nvidia-smi auf dem Host."""
    raw = (os.environ.get("GPU_VRAM_MB") or "").strip()
    return int(raw) if raw.isdigit() else 0


def needs_setup() -> bool:
    """Erstlauf: nicht abgeschlossen *und* keine aktive Quelle.

    Eine Installation mit Quellen ist eingerichtet, ob der Wizard je lief
    oder nicht -- sonst landete jede bestehende Installation nach dem
    Update auf der Einrichtungsseite.
    """
    if settings.load()["setup"].get("done"):
        return False
    from api.routes.jobs import sources_ready

    return bool(sources_ready())


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------

@router.get("/state")
def state() -> dict:
    from api.routes.jobs import sources_ready
    from ingest.spaces import browse_root, photo_root

    conf = settings.redacted()
    vram = vram_mb()
    reachable, models = _reachable_models()
    llm = {
        **conf["llm"],
        "effective": {
            "mode": settings.llm_mode(),
            "caption_model": cap.caption_model(),
            "embed_model": cap.embed_model(),
            "from_env": _from_env(),
        },
        "reachable": reachable,
        "models": models,
        "url_in_use": cap.litellm_url() or cap.ollama_url(),
    }
    llm["caption_ready"] = bool(cap.caption_model()) and cap.caption_model() in models
    llm["embed_ready"] = bool(cap.embed_model()) and cap.embed_model() in models
    return {
        "setup": conf["setup"],
        "needs_setup": needs_setup(),
        "sources": {"blocked": sources_ready(), "browse_root": browse_root() or None,
                    "photo_root": photo_root() or None},
        "gpu": {"vram_mb": vram, "recommendation": recommend(vram)},
        "llm": llm,
    }


def _from_env() -> dict[str, bool]:
    """Welche Werte die Umgebung festnagelt -- die kann der Wizard nicht aendern."""
    return {
        "pool": bool(os.environ.get("LITELLM_URL") or os.environ.get("PHOTOVAULT_EMBED_URL")),
        "caption_model": bool(os.environ.get("PHOTOVAULT_CAPTION_MODEL")),
        "embed_model": bool(os.environ.get("PHOTOVAULT_EMBED_MODEL")),
        "ollama_url": bool(os.environ.get("OLLAMA_URL")),
    }


def _reachable_models() -> tuple[bool, list[str]]:
    models = cap.llm_models()
    return models is not None, sorted(models or [])


# --------------------------------------------------------------------------
# Sprachmodell: waehlen, auflisten, ziehen, pruefen
# --------------------------------------------------------------------------

class LlmRequest(BaseModel):
    mode: str
    url: str = ""
    key: Optional[str] = None      # None: Schluessel unveraendert lassen
    caption_model: str = ""
    embed_model: str = ""


@router.post("/llm")
def save_llm(req: LlmRequest) -> dict:
    """Modus und Modelle festhalten. Der Schluessel wird nie zurueckgegeben."""
    mode = req.mode.strip().lower()
    if mode not in settings.MODES:
        raise HTTPException(400, f"Unbekannter Modus: {req.mode}")
    url = req.url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Adresse muss mit http:// oder https:// beginnen")
    if mode == "openai" and not url:
        raise HTTPException(400, "Fuer einen OpenAI-kompatiblen Anbieter fehlt die Adresse")
    patch: dict[str, Any] = {"llm": {
        "mode": mode, "url": url,
        "caption_model": req.caption_model.strip(),
        "embed_model": req.embed_model.strip() or settings.DEFAULTS["llm"]["embed_model"],
    }}
    if req.key is not None:
        patch["llm"]["key"] = req.key.strip()
    saved = settings.save(patch)
    cap.forget()
    return {"ok": True, "llm": settings.redacted(saved)["llm"], "from_env": _from_env()}


@router.get("/llm/models")
def llm_models() -> dict:
    """Was beim Anbieter liegt -- mit Groesse, wo er sie nennt (Ollama)."""
    mode = settings.llm_mode()
    pool = cap.litellm_url()
    if pool:
        req = urllib.request.Request(f"{pool}/v1/models", headers=cap.litellm_headers())
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            return {"reachable": False, "mode": mode, "url": pool, "models": [],
                    "error": _plain(e)}
        models = [{"name": str(m.get("id") or ""), "size_gb": None}
                  for m in data.get("data", []) if m.get("id")]
        return {"reachable": True, "mode": mode, "url": pool,
                "models": sorted(models, key=lambda m: m["name"])}
    url = cap.ollama_url()
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"reachable": False, "mode": mode, "url": url, "models": [],
                "error": _plain(e)}
    models = [{"name": str(m.get("name") or ""),
               "size_gb": round(int(m.get("size") or 0) / 1e9, 1)}
              for m in data.get("models", []) if m.get("name")]
    return {"reachable": True, "mode": mode, "url": url,
            "models": sorted(models, key=lambda m: m["name"])}


class PullRequest(BaseModel):
    model: str


@router.post("/llm/pull")
def pull_model(req: PullRequest) -> StreamingResponse:
    """Ein Modell ziehen, Fortschritt als NDJSON durchreichen.

    Ollama meldet je Schicht `total` und `completed`; der Browser zeichnet
    daraus den Balken. Nicht gepuffert: bei 7 GB will man sehen, dass sich
    etwas bewegt.
    """
    name = req.model.strip()
    if not name or any(c in name for c in " \t\n\"'"):
        raise HTTPException(400, "Kein gueltiger Modellname")
    if settings.llm_mode() != "ollama" or cap.litellm_url():
        raise HTTPException(409, "Ziehen geht nur mit Ollama; ein Anbieter hat seine Modelle schon.")
    url = cap.ollama_url()
    body = json.dumps({"name": name, "stream": True}).encode("utf-8")
    request = urllib.request.Request(f"{url}/api/pull", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"Ollama antwortet {e.code}: {e.read()[:200]!r}") from e
    except Exception as e:
        raise HTTPException(502, f"Ollama nicht erreichbar ({url}): {_plain(e)}") from e

    def lines() -> Iterator[bytes]:
        try:
            for line in resp:
                if line.strip():
                    yield line if line.endswith(b"\n") else line + b"\n"
        except Exception as e:
            yield (json.dumps({"error": _plain(e)}) + "\n").encode("utf-8")
        finally:
            resp.close()
            cap.forget()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


class TestRequest(BaseModel):
    path: str = ""          #: leer: das erste Foto im Index
    photos: int = 0         #: fuer die Hochrechnung; 0: Zahl aus dem Index


@router.post("/llm/test")
def test_caption(req: TestRequest) -> dict:
    """Eine Beschreibung, mit Stoppuhr -- und die Hochrechnung dazu.

    Das ist der Satz, der auf einer Maschine ohne Grafikkarte ehrlich sein
    muss: "38 s je Bild, 4 800 Fotos, etwa 2 Tage". Erst danach entscheidet
    jemand, ob er das nachts laufen laesst, ohne Beschreibungen weitermacht
    oder einen Anbieter nimmt.
    """
    from ingest.captioner import Captioner

    model = cap.caption_model()
    if not model:
        raise HTTPException(409, "Kein Caption-Modell gewaehlt.")
    path = _test_photo(req.path)
    started = time.monotonic()
    result = Captioner(model=model).caption_structured(path, {"filename": Path(path).name})
    seconds = time.monotonic() - started
    if not result:
        raise HTTPException(502, f"Keine Beschreibung von {model} nach {seconds:.0f} s -- "
                                 f"laeuft das Modell, und kann es Bilder? Protokoll pruefen.")
    total = req.photos if req.photos > 0 else _index_count()
    return {
        "model": model,
        "path": path,
        "seconds": round(seconds, 1),
        "caption_de": result.get("caption_de"),
        "scene_tags": result.get("scene_tags") or [],
        "estimate": {"photos": total, "hours": round(total * seconds / 3600, 1)},
    }


class EmbedProbeRequest(BaseModel):
    text: str = "Ein Foto aus dem Familienarchiv, Sommer im Garten."


@router.post("/llm/embed")
def probe_embedding(req: EmbedProbeRequest) -> dict:
    """Welche Dimension liefert der Embedder? Messen, dann merken.

    Die Collection wird mit dieser Groesse angelegt. Gibt es sie schon mit
    einer anderen, wird nichts ueberschrieben -- dann sagt die Antwort, dass
    ein anderes Modell die Vektoren neu rechnen muesste (`reembed` nach
    dem Neuanlegen), statt still einen Lauf zu starten, der bei jedem Foto
    scheitert.
    """
    from ingest.text_embedder import TextEmbedder

    model = cap.embed_model()
    if not model:
        raise HTTPException(409, "Kein Embedding-Modell gewaehlt.")
    started = time.monotonic()
    try:
        vec = TextEmbedder(model=model).raw_batch([req.text])[0]
    except Exception as e:
        raise HTTPException(502, f"Embedding mit {model} fehlgeschlagen: {_plain(e)}") from e
    seconds = time.monotonic() - started
    if not vec:
        raise HTTPException(502, f"{model} lieferte keinen Vektor.")
    dim = len(vec)
    existing = _collection_text_dim()
    stored = False
    if existing is None or existing == dim:
        settings.save({"llm": {"embed_dim": dim}})
        stored = True
    return {
        "model": model, "dim": dim, "seconds": round(seconds, 2),
        "collection_dim": existing, "stored": stored,
        "note": "" if stored else (
            f"Die Collection hat {existing} Dimensionen, {model} liefert {dim}. "
            "Entweder das bisherige Modell behalten oder die Collection neu anlegen "
            "und die Text-Vektoren neu rechnen."),
    }


@router.post("/done")
def finish() -> dict:
    saved = settings.save({"setup": {"done": True, "step": "done"}})
    return {"ok": True, "setup": saved["setup"]}


class StepRequest(BaseModel):
    step: str


@router.post("/step")
def set_step(req: StepRequest) -> dict:
    """Wo der Wizard steht -- damit `start.bat` weiss, wann die Ordner gewaehlt sind."""
    step = req.step.strip()
    if not step or len(step) > 40:
        raise HTTPException(400, "Kein gueltiger Schritt")
    saved = settings.save({"setup": {"step": step}})
    return {"ok": True, "setup": saved["setup"]}


# --------------------------------------------------------------------------
# Helfer
# --------------------------------------------------------------------------

def _plain(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def _test_photo(path: str) -> str:
    """Der Pfad fuer die Test-Caption: angegeben und innerhalb der Grenze,
    sonst das erste Foto im Index."""
    if path:
        from api.routes.sources import _in_der_bibliothek

        p = _in_der_bibliothek(path)
        if not Path(p).is_file():
            raise HTTPException(400, f"Keine Datei: {p}")
        return p
    try:
        points, _ = client().scroll(collection_name=PHOTOS, limit=1,
                                    with_payload=["file_path"], with_vectors=False)
    except Exception as e:
        raise HTTPException(502, f"Index nicht erreichbar: {_plain(e)}") from e
    for pt in points:
        fp = (pt.payload or {}).get("file_path")
        if fp and Path(fp).is_file():
            return str(fp)
    raise HTTPException(400, "Noch kein Foto im Index -- `path` angeben.")


def _index_count() -> int:
    try:
        return int(client().count(collection_name=PHOTOS, exact=True).count)
    except Exception:
        return 0


def _collection_text_dim() -> Optional[int]:
    """Die Textvektor-Groesse der bestehenden Collection, oder None ohne Collection."""
    try:
        info = client().get_collection(PHOTOS)
    except Exception:
        return None
    vectors = getattr(info.config.params, "vectors", None) or {}
    text_cfg = vectors.get("text") if isinstance(vectors, dict) else None
    size = getattr(text_cfg, "size", None)
    return int(size) if size else None
