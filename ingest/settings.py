"""Einstellungen zur Laufzeit -- fuer Installationen, die keine `.env` haben.

Auf der Maschine, auf der PhotoVault entstand, kommt alles aus der Umgebung:
`LITELLM_URL`, `PHOTOVAULT_CAPTION_MODEL=local`, ein Schluessel aus dem
ai-stack. Ein Fremder hat davon nichts, und er soll es auch nicht tippen.
Der Setup-Wizard schreibt stattdessen `data/settings.json`, und die Stellen,
die ein Modell oder eine Adresse brauchen, fragen zur Laufzeit hier nach --
nicht beim Import, denn dann waere jede Aenderung ein Neustart.

Vorrang, und zwar genau so: **Umgebung > Datei > Vorgabe.** Eine gesetzte
Umgebungsvariable gewinnt immer. Damit aendert sich fuer die Installation
mit `.env` nichts, auch wenn irgendwann eine Datei daneben liegt.

Der Schluessel fuer einen Cloud-Anbieter steht in dieser Datei und sonst
nirgends: nicht im Repo (`data/` ist ignoriert), nicht im Protokoll
(`redacted()` fuer alles, was nach aussen geht).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

#: Vorgaben fuer eine frische Installation ohne Pool: Ollama auf dem Host,
#: kein Caption-Modell gewaehlt (das tut der Wizard, nach gemessenem VRAM),
#: der Embedder fest -- die Textvektor-Groesse der Collection haengt daran.
DEFAULTS: dict[str, Any] = {
    "setup": {"done": False, "step": "sources"},
    "llm": {
        "mode": "ollama",          # ollama | openai | off
        "url": "",                 # leer: Vorgabe des Modus
        "key": "",                 # nur fuer openai
        "caption_model": "",
        "embed_model": "qwen3-embedding:4b",
        "embed_dim": 2560,
    },
}

MODES = ("ollama", "openai", "off")

_lock = threading.Lock()
_cache: tuple[str, float, dict] = ("", -1.0, {})


def settings_path() -> Path:
    raw = os.environ.get("PHOTOVAULT_SETTINGS")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    return ROOT / "data" / "settings.json"


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load() -> dict:
    """Vorgaben, ueberlagert von der Datei -- oder nur die Vorgaben.

    Gecacht nach Pfad und Aenderungszeit: die Datei aendert sich, wenn der
    Wizard schreibt oder ein anderer Prozess (der Caption-Lauf ist ein
    eigener). Ein kaputtes JSON zaehlt wie keine Datei, mit Warnung -- eine
    Installation soll an einer Einstellungsdatei nicht sterben.
    """
    global _cache
    path = settings_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = -1.0
    with _lock:
        cpath, cstamp, cval = _cache
        if cval and cpath == str(path) and cstamp == stamp:
            return copy.deepcopy(cval)
        data: dict = {}
        if stamp >= 0:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
                else:
                    logger.warning("%s: kein Objekt, wird ignoriert", path)
            except (OSError, ValueError) as e:
                logger.warning("%s nicht lesbar (%s), Vorgaben gelten", path, e)
        merged = _merge(DEFAULTS, data)
        _cache = (str(path), stamp, merged)
        return copy.deepcopy(merged)


def save(patch: dict) -> dict:
    """Einen Ausschnitt einmischen und die ganze Datei neu schreiben.

    Atomar (Tmp-Datei, dann `os.replace`), damit ein Absturz mittendrin
    nicht die halbe Datei hinterlaesst. Auf POSIX nur fuer den Besitzer
    lesbar -- ein Cloud-Schluessel kann drinstehen.
    """
    path = settings_path()
    with _lock:
        current = _cache[2] if _cache[0] == str(path) and _cache[2] else None
    base = current or load()
    merged = _merge(base, patch)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    reset_cache()
    return copy.deepcopy(merged)


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = ("", -1.0, {})


def redacted(data: dict | None = None) -> dict:
    """Dieselben Werte, nur der Schluessel maskiert -- fuer jede Antwort nach aussen."""
    out = copy.deepcopy(data if data is not None else load())
    llm = out.get("llm") or {}
    key = llm.get("key") or ""
    llm["key"] = ("•" * 8 + key[-4:]) if len(key) > 4 else ("•" * len(key))
    llm["has_key"] = bool(key)
    out["llm"] = llm
    return out


# --------------------------------------------------------------------------
# Was die Stellen brauchen, die Modelle ansprechen
# --------------------------------------------------------------------------

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def llm_mode() -> str:
    """`ollama`, `openai` oder `off`. Ein gesetzter Pool in der Umgebung ist
    der alte Weg und zaehlt als `openai` -- LiteLLM spricht dieses Format."""
    if _env("LITELLM_URL") or _env("PHOTOVAULT_EMBED_URL"):
        return "openai"
    mode = str((load().get("llm") or {}).get("mode") or "ollama")
    return mode if mode in MODES else "ollama"


def pool_url() -> str:
    """OpenAI-kompatible Basis-URL (LiteLLM oder Cloud), sonst leer."""
    env = _env("LITELLM_URL") or _env("PHOTOVAULT_EMBED_URL")
    if env:
        return env.rstrip("/")
    llm = load().get("llm") or {}
    if llm.get("mode") == "openai":
        return str(llm.get("url") or "").rstrip("/")
    return ""


def pool_key() -> str:
    env = _env("LITELLM_MASTER_KEY")
    if env:
        return env
    llm = load().get("llm") or {}
    if llm.get("mode") == "openai":
        return str(llm.get("key") or "")
    return ""


def ollama_base() -> str:
    """Adresse von Ollama: Umgebung, sonst die im Modus `ollama` eingetragene."""
    env = _env("OLLAMA_URL")
    if env:
        return env.rstrip("/")
    llm = load().get("llm") or {}
    if llm.get("mode") == "ollama" and llm.get("url"):
        return str(llm["url"]).rstrip("/")
    return "http://127.0.0.1:11434"


def caption_model() -> str:
    """Leer heisst: kein Modell gewaehlt -- Beschreibungen sind aus.

    Der Pool spricht Aliasse (`local`), Ollama spricht Tags. Deshalb faellt
    die Vorgabe nur dort auf `local` zurueck, wo ein Pool konfiguriert ist;
    ohne Pool ist "kein Modell" die ehrliche Antwort, bis der Wizard eines
    eintraegt.
    """
    env = _env("PHOTOVAULT_CAPTION_MODEL")
    if env:
        return env
    if _env("LITELLM_URL"):
        return "local"
    llm = load().get("llm") or {}
    if llm.get("mode") == "off":
        return ""
    return str(llm.get("caption_model") or "")


def embed_model() -> str:
    env = _env("PHOTOVAULT_EMBED_MODEL")
    if env:
        return env
    if _env("LITELLM_URL"):
        return "embedder"
    llm = load().get("llm") or {}
    if llm.get("mode") == "off":
        return ""
    return str(llm.get("embed_model") or DEFAULTS["llm"]["embed_model"])


def text_vector_size() -> int:
    """Dimension des Textvektors, wie die Collection sie kennt.

    `PHOTOVAULT_TEXT_DIM` fuer die Umgebung, sonst die vom Wizard per
    Probe-Aufruf gemessene und gespeicherte -- ein Cloud-Embedder liefert
    andere Groessen als `qwen3-embedding:4b`.
    """
    env = _env("PHOTOVAULT_TEXT_DIM")
    if env.isdigit() and int(env) > 0:
        return int(env)
    llm = load().get("llm") or {}
    try:
        dim = int(llm.get("embed_dim") or 0)
    except (TypeError, ValueError):
        dim = 0
    return dim if dim > 0 else int(DEFAULTS["llm"]["embed_dim"])
