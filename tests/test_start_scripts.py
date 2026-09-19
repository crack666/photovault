"""Die Startskripte, soweit sie Logik haben: die Override-Datei.

Alles andere in start.sh / start.ps1 ist Ablauf gegen Docker und den
Browser -- das prueft nur die Testmaschine. Aber welche Ordner *beschreibbar*
in den Container kommen, ist die Grenze, die der Kernel zieht, und die darf
nicht an einem Tippfehler im Skript haengen: nur aktive Quellen, nur
existierende, nur unterhalb der eingebundenen Orte, nie ein Ort selbst.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _bash() -> str | None:
    return shutil.which("bash")


def _powershell() -> str | None:
    # Unter WSL liegt Windows PowerShell per Interop im PATH; sonst nirgends.
    return shutil.which("powershell.exe") or shutil.which("powershell")


@pytest.mark.skipif(not _bash(), reason="bash fehlt")
class TestStartSh:
    def _emit(self, sources: Path, gpu: str, roots: list[Path]) -> str:
        out = subprocess.run(
            [_bash(), str(ROOT / "start.sh"), "--emit-override", str(sources), gpu, *map(str, roots)],
            capture_output=True, text=True, check=True, cwd=str(ROOT),
        )
        return out.stdout

    def test_nur_aktive_existierende_quellen_unter_den_orten_werden_beschreibbar(self, tmp_path):
        home = tmp_path / "home"
        media = tmp_path / "media"
        (home / "x" / "Bilder").mkdir(parents=True)
        (media / "usb").mkdir(parents=True)
        src = tmp_path / "sources.txt"
        src.write_text("\n".join([
            "# Kopf",
            f"/host{home}/x/Bilder      # 12 Bilder",
            f"-/host{home}/x/Bilder/Screens",   # Ausschluss: kein Schreibrecht noetig
            f"#/host{media}/usb",               # stillgelegt
            f"/host{home}/x/gibtsnicht",        # existiert nicht
            "/host/nirgends/x",                 # ausserhalb der Orte
            f"/host{home}",                     # ein Ort selbst: nie ganz beschreibbar
            "/mnt/photo/alt",                   # alter Stil ohne /host
        ]) + "\n", encoding="utf-8")
        yaml = self._emit(src, "0", [home, media])
        assert yaml.count("read_only: true") == 2
        assert f'source: "{home}"' in yaml and f"target: /host{home}\n" in yaml
        rw = [l.strip() for l in yaml.splitlines() if l.strip().startswith("source:")]
        assert rw == [f'source: "{home}"', f'source: "{media}"', f'source: "{home}/x/Bilder"']
        assert "nvidia" not in yaml

    def test_gpu_reservierung_nur_auf_wunsch(self, tmp_path):
        src = tmp_path / "sources.txt"
        src.write_text("", encoding="utf-8")
        assert "driver: nvidia" in self._emit(src, "1", [tmp_path])
        assert "driver: nvidia" not in self._emit(src, "0", [tmp_path])


@pytest.mark.skipif(not _powershell(), reason="Windows PowerShell fehlt")
class TestStartPs1:
    def _emit(self, sources: Path, out: Path, drives: str, gpu: bool = False) -> str:
        # WSL reicht Umgebungsvariablen nur an Windows-Programme weiter, die in
        # WSLENV stehen -- sonst kaeme der Schalter nie an.
        env = {**os.environ, "PHOTOVAULT_TEST_GPU": "1" if gpu else "",
               "WSLENV": (os.environ.get("WSLENV", "") + ":PHOTOVAULT_TEST_GPU").strip(":")}
        win = lambda p: _to_windows(p)
        subprocess.run(
            [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win(ROOT / "start.ps1"),
             "-EmitOverride", win(sources), "-OutFile", win(out), "-Drives", drives],
            capture_output=True, text=True, check=True, env=env,
        )
        return out.read_text(encoding="utf-8")

    def test_laufwerke_lesend_gewaehlte_ordner_beschreibbar(self, tmp_path):
        src = tmp_path / "sources.txt"
        src.write_text("\n".join([
            "/host/d/Fotos/Alben   # 4812 Bilder",
            "-/host/d/Fotos/Alben/Screenshots",
            "#/host/c/still",
            "/host/c/Users/x/Bilder M\u00fcller",
            "/host/e",              # ganzes Laufwerk: nein
            "/mnt/photo/alt",
        ]) + "\n", encoding="utf-8")
        out = tmp_path / "override.yml"
        yaml = self._emit(src, out, "C,D")
        assert out.read_bytes()[:3] != b"\xef\xbb\xbf"   # kein BOM
        assert yaml.count("read_only: true") == 2
        assert 'source: "C:/"' in yaml and "target: /host/c\n" in yaml
        assert 'source: "D:/Fotos/Alben"' in yaml and "target: /host/d/Fotos/Alben" in yaml
        assert 'source: "C:/Users/x/Bilder M\u00fcller"' in yaml
        assert "Screenshots" not in yaml and "still" not in yaml and "E:/" not in yaml
        assert "nvidia" not in yaml

    def test_gpu_reservierung_nur_mit_gemessener_karte(self, tmp_path):
        src = tmp_path / "sources.txt"
        src.write_text("", encoding="utf-8")
        assert "driver: nvidia" in self._emit(src, tmp_path / "a.yml", "C", gpu=True)
        assert "driver: nvidia" not in self._emit(src, tmp_path / "b.yml", "C", gpu=False)


def _to_windows(p: Path) -> str:
    """Unter WSL braucht powershell.exe Windows-Pfade; sonst ist der Pfad schon einer."""
    s = str(p)
    if s.startswith("/mnt/") and len(s) > 6 and s[5].isalpha() and s[6] == "/":
        return f"{s[5].upper()}:{s[6:]}"
    if s.startswith("/") and shutil.which("wslpath"):
        try:
            return subprocess.run(["wslpath", "-w", s], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return s
    return s
