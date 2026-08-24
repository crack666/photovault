"""Face Detection + Embedding via insightface (BGR ndarray, all faces)."""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_CUDA_LIBS_LOADED = False
#: Reihenfolge zaehlt -- cublas braucht cublasLt, cudnn braucht das CUDA-Runtime.
_CUDA_LIB_ORDER = (
    "cuda_runtime",
    "nvjitlink",
    "cublas",
    "cufft",
    "curand",
    "cusparse",
    "cusolver",
    "cudnn",
)


def _preload_cuda_libs() -> None:
    """CUDA-Bibliotheken vorab in den Prozess laden.

    torch bringt sie als pip-Pakete unter ``nvidia/*/lib`` mit, aber
    onnxruntime sucht sie nur im System-Linkerpfad. Ohne diesen Schritt
    scheitert der CUDAExecutionProvider mit "libcublasLt.so.12 not found"
    und faellt still auf die CPU zurueck -- was die Gesichtserkennung von
    ~30 ms auf ~860 ms pro Foto bremst, ohne einen Fehler zu melden.
    """
    global _CUDA_LIBS_LOADED
    if _CUDA_LIBS_LOADED:
        return
    _CUDA_LIBS_LOADED = True
    import ctypes
    import glob
    import os

    try:
        import nvidia
    except ImportError:
        return
    base = os.path.dirname(nvidia.__file__)
    loaded = 0
    for package in _CUDA_LIB_ORDER:
        for so in sorted(glob.glob(os.path.join(base, package, "lib", "*.so*")), reverse=True):
            try:
                ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                loaded += 1
            except OSError:
                continue
    logger.debug("Preloaded %d CUDA libraries from %s", loaded, base)


class FaceEmbedder:
    def __init__(self, model_dir: str = "/models"):
        self._app = None
        self._model_dir = model_dir

    def _ensure_loaded(self):
        if self._app is None:
            _preload_cuda_libs()
            import onnxruntime as ort
            from insightface.app import FaceAnalysis

            available = ort.get_available_providers()
            if "CUDAExecutionProvider" not in available:
                logger.warning(
                    "onnxruntime has no CUDA provider (%s) -- face detection will run on "
                    "the CPU at roughly 860 ms/photo instead of ~30 ms. "
                    "Install onnxruntime-gpu matching the installed CUDA runtime.",
                    ", ".join(available),
                )
            logger.info("Loading insightface buffalo_l...")
            # buffalo_l bringt fuenf Modelle mit; gebraucht werden zwei:
            # detection (Boxen + Landmarks) und recognition (das 512d-Embedding).
            # landmark_3d_68, landmark_2d_106 und genderage laufen sonst bei
            # *jedem* Gesicht mit, ohne dass ihr Ergebnis irgendwo landet --
            # auf einem Gruppenfoto mit 20 Gesichtern sind das 60 Inferenzen
            # umsonst.
            self._app = FaceAnalysis(
                name="buffalo_l",
                root=self._model_dir,
                allowed_modules=["detection", "recognition"],
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            self._app.prepare(ctx_id=0, det_size=(640, 640))
            active = getattr(self._app.models.get("detection"), "session", None)
            if active is not None:
                logger.info("insightface loaded, providers: %s", active.get_providers())
            else:
                logger.info("insightface loaded")

    def process(self, file_path: str, image=None, bgr=None) -> dict:
        """`image` ist ein bereits geladenes PIL-Image (RGB), `bgr` ein fertig
        konvertiertes ndarray.

        Die BGR-Konvertierung kopiert bei einem 12-MP-Foto rund 36 MB. Im
        Fliessband erledigt das der Reader-Pool und reicht `bgr` durch --
        sonst haengt diese CPU-Arbeit am GPU-Thread und bremst ihn aus.
        """
        result = {"count": 0, "primary_embedding": None, "boxes": [], "faces": []}
        try:
            self._ensure_loaded()
            if bgr is None:
                bgr = _to_bgr(image) if image is not None else _load_bgr(file_path)
            img_bgr = bgr
            if img_bgr is None:
                return result
            faces = self._app.get(img_bgr)
            if not faces:
                return result
            parsed = []
            for face in faces:
                x1, y1, x2, y2 = face.bbox.astype(int)
                box = [int(x1), int(y1), int(x2), int(y2)]
                vec = getattr(face, "normed_embedding", None)
                if vec is None:
                    vec = face.embedding
                kps = getattr(face, "kps", None)
                landmarks = (
                    np.asarray(kps, dtype=np.float32).round(1).tolist()
                    if kps is not None
                    else None
                )
                parsed.append(
                    {
                        "embedding": np.asarray(vec, dtype=np.float32).tolist(),
                        "box": box,
                        "score": float(getattr(face, "det_score", 0.0)),
                        "area": max(0, box[2] - box[0]) * max(0, box[3] - box[1]),
                        "landmarks": landmarks,
                        "frontality": frontality(landmarks, box),
                    }
                )
            parsed.sort(key=lambda f: f["area"], reverse=True)
            result["count"] = len(parsed)
            result["faces"] = parsed
            result["boxes"] = [f["box"] for f in parsed]
            result["primary_embedding"] = parsed[0]["embedding"]
        except Exception as e:
            logger.warning("Face processing failed for %s: %s", file_path, e)
        return result


def frontality(landmarks, box) -> float | None:
    """Wie frontal ist das Gesicht? 1.0 = direkt in die Kamera, 0.0 = Profil.

    Der Detektor meldet auch Ohren und Hinterköpfe als Gesichter -- die liefern
    zwar ein Embedding, taugen aber nicht zur Wiedererkennung und verstopfen
    die Labeling-Queue. Die fünf Landmarks (zwei Augen, Nase, zwei Mundwinkel)
    verraten den Unterschied: Frontal stehen die Augen weit auseinander und die
    Nase mittig dazwischen; im Profil rücken die Augen zusammen und die Nase
    wandert an den Rand oder darüber hinaus.
    """
    if not landmarks or len(landmarks) < 5 or not box or len(box) != 4:
        return None
    try:
        pts = np.asarray(landmarks, dtype=np.float32)
        left_eye, right_eye, nose = pts[0], pts[1], pts[2]
        width = max(1.0, float(box[2] - box[0]))
        eye_dist = float(np.linalg.norm(right_eye - left_eye))
        if eye_dist < 1e-3:
            return 0.0
        # Frontal liegen die Augen bei etwa 40 % der Gesichtsbreite auseinander.
        spread = min(1.0, (eye_dist / width) / 0.40)
        # Und die Nase mittig zwischen ihnen; im Profil kippt das weg.
        eye_mid_x = (left_eye[0] + right_eye[0]) / 2.0
        offset = abs(float(nose[0]) - eye_mid_x) / eye_dist
        centered = max(0.0, 1.0 - offset / 0.75)
        return round(float(spread * centered), 3)
    except Exception:
        return None


def to_bgr(image) -> np.ndarray | None:
    """Oeffentlich, damit der Reader-Pool sie vorab erledigen kann."""
    return _to_bgr(image)


def _to_bgr(image) -> np.ndarray | None:
    """PIL-RGB -> BGR-ndarray, wie insightface es erwartet."""
    rgb = np.array(image if image.mode == "RGB" else image.convert("RGB"))
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        return None
    return rgb[:, :, ::-1].copy()


def _load_bgr(file_path: str) -> np.ndarray | None:
    from PIL import Image

    return _to_bgr(Image.open(file_path))
