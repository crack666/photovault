"""Die CUDA-Bibliotheken fuer onnxruntime finden -- auch im CUDA-13-Layout.

Gemessen im cuda-Image: `nvidia` ist dort ein Namensraum ohne __init__.py,
`nvidia.__file__` ist None, und `os.path.dirname(None)` warf einen
TypeError -- die Gesichtserkennung waere gar nicht erst gestartet. Und die
Laufzeitbibliotheken liegen nicht mehr je Paket, sondern zusammen unter
`nvidia/cu13/lib`.
"""
from __future__ import annotations

import sys
import types

from ingest import face_embedder as fe


def _fake_nvidia(monkeypatch, tmp_path, layout: dict[str, list[str]]):
    root = tmp_path / "nvidia"
    for package, libs in layout.items():
        d = root / package / "lib"
        d.mkdir(parents=True)
        for name in libs:
            (d / name).write_bytes(b"kein echtes so")
    ns = types.ModuleType("nvidia")
    ns.__path__ = [str(root)]          # Namensraum: __path__, kein __file__
    ns.__file__ = None
    monkeypatch.setitem(sys.modules, "nvidia", ns)
    return root


def test_cuda13_layout_wird_gefunden_und_cublaslt_vor_cublas(monkeypatch, tmp_path):
    root = _fake_nvidia(monkeypatch, tmp_path, {
        "cu13": ["libcublas.so.13", "libcublasLt.so.13", "libcudart.so.13"],
        "cudnn": ["libcudnn.so.9"],
    })
    found = [p.replace(str(root), "") for p in fe._cuda_lib_candidates()]
    assert found.index("/cu13/lib/libcublasLt.so.13") < found.index("/cu13/lib/libcublas.so.13")
    assert found[-1] == "/cudnn/lib/libcudnn.so.9"          # cudnn zuletzt: braucht das Runtime


def test_cuda12_layout_bleibt(monkeypatch, tmp_path):
    root = _fake_nvidia(monkeypatch, tmp_path, {
        "cublas": ["libcublas.so.12"], "cuda_runtime": ["libcudart.so.12"],
    })
    found = [p.replace(str(root), "") for p in fe._cuda_lib_candidates()]
    assert found == ["/cuda_runtime/lib/libcudart.so.12", "/cublas/lib/libcublas.so.12"]


def test_unladbare_bibliotheken_kosten_nicht_den_start(monkeypatch, tmp_path):
    """Attrappen sind keine echten .so -- CDLL scheitert, der Aufruf nicht."""
    _fake_nvidia(monkeypatch, tmp_path, {"cu13": ["libcudart.so.13"]})
    monkeypatch.setattr(fe, "_CUDA_LIBS_LOADED", False)
    fe._preload_cuda_libs()


def test_ohne_nvidia_pakete_passiert_nichts(monkeypatch):
    monkeypatch.setitem(sys.modules, "nvidia", None)   # import nvidia -> ImportError
    assert fe._cuda_lib_candidates() == []
