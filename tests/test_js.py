"""Die JavaScript-Tests des Atlas ueber pytest mitlaufen lassen.

Ein Einstieg, nicht zwei: wer `pytest` sagt, soll auch die Karte geprueft
bekommen. Die JS-Tests brauchen nur Node (eingebauter Testlaeufer, keine
Abhaengigkeit). Unter WSL gibt es hier kein Node, aber das Windows-Node ist
ueber die Interop erreichbar -- deshalb beide Wege. Fehlt beides, wird
sichtbar uebersprungen, nicht still bestanden.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
JS_TESTS = sorted((REPO / "tests" / "js").glob("*.test.mjs"))

#: Wo Node liegen kann, wenn `node` nicht im PATH ist: das Windows-Node,
#: aus WSL ueber die Interop aufrufbar.
KANDIDATEN = (
    "/mnt/c/Program Files/nodejs/node.exe",
    "C:/Program Files/nodejs/node.exe",
)


def node_binary() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    for k in KANDIDATEN:
        if Path(k).is_file():
            return k
    return None


@pytest.mark.skipif(not JS_TESTS, reason="keine JS-Tests unter tests/js")
def test_atlas_model_js():
    node = node_binary()
    if node is None:
        pytest.skip("kein Node erreichbar -- `node --test tests/js/*.test.mjs` von Hand")
    # Windows-Node aus WSL: Pfade als Windows-Pfade uebergeben, sonst sieht
    # es /mnt/d/... nicht.
    dateien = [str(p) for p in JS_TESTS]
    if node.endswith(".exe") and dateien[0].startswith("/mnt/"):
        dateien = [d.replace("/mnt/d/", "D:/", 1).replace("/mnt/c/", "C:/", 1) for d in dateien]
    r = subprocess.run([node, "--test", *dateien], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "\n".join(
        l for l in (r.stdout + r.stderr).splitlines() if "✖" in l or "fail" in l.lower()
    )[:4000]
