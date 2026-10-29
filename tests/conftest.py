"""Was jeder Test bekommt, ohne es zu bestellen.

`data/settings.json` ist Zustand dieser Installation, nicht der Tests: sobald
der Setup-Wizard einmal lief, stuende dort ein Modus und ein Modell -- und
jeder Test, der "kein Modell gewaehlt" oder "Ollama direkt" beschreibt,
laese ploetzlich etwas anderes. Deshalb zeigt `PHOTOVAULT_SETTINGS` in jedem
Test auf eine Datei, die es nicht gibt. Wer die Datei braucht, schreibt sie
selbst dorthin.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _eigene_einstellungen(monkeypatch, tmp_path):
    from ingest import settings

    monkeypatch.setenv("PHOTOVAULT_SETTINGS", str(tmp_path / "settings.json"))
    settings.reset_cache()
    yield
    settings.reset_cache()
