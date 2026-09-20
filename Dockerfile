# PhotoVault -- das Image fuer den Verbund (Qdrant + API + Oberflaeche).
#
# Zwei Stufen, aus zwei gemessenen Gruenden (19.09.2026):
#
# 1. insightface kommt von PyPI als Quellpaket und kompiliert eine
#    Cython-Erweiterung -- auf python:3.11-slim ohne g++ brach der Bau ab
#    ("No such file or directory: 'g++'"). Der Compiler gehoert in die
#    Baustufe, nicht ins Image, das jeder laedt.
# 2. `pip install torch` zieht unter Linux die CUDA-Fassung samt
#    NVIDIA-Bibliotheken, rund 3 GB -- fuer die meisten Rechner Ballast.
#    Deshalb zwei Varianten (unten): `cpu` als Vorgabe, `cuda` fuer Rechner
#    mit NVIDIA-Karte, die start.bat waehlt, wenn es eine misst.
#
# Dazu, was der Code zur Laufzeit braucht und pyproject nicht nennt:
# onnxruntime (insightface laedt es selbst, erklaert es aber nicht als
# Abhaengigkeit), ffmpeg/ffprobe fuer Videos, libgl/libglib fuer OpenCV,
# und das Extra [atlas], damit die Jobs-Seite die Karte rechnen kann.

FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential g++ \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./

# Zwei Varianten desselben Images, gewaehlt ueber Build-Argumente:
#
#   cpu  (Vorgabe, Tag `latest`)  torch aus dem CPU-Index, onnxruntime.
#        Fuer Rechner ohne NVIDIA-Karte -- und ein Zehntel der Groesse.
#   cuda (Tag `cuda`)             torch aus dem cu130-Index, onnxruntime-gpu.
#        CUDA 13 fuer beide: onnxruntime-gpu von PyPI ist gegen CUDA 13
#        gebaut ("Require cuDNN 9.* and CUDA 13.*" mit den 12.8er-Bibliotheken
#        von torch cu128 -- gemessen), und Blackwell (RTX 50xx, sm_120)
#        kennen beide. Die nvidia-*-Pakete kommen mit torch,
#        ingest/face_embedder.py laedt sie fuer onnxruntime vor. Braucht auf
#        dem Host einen Treiber ab 580; start.bat prueft das und nimmt sonst
#        die CPU-Variante.
#
# torch *und* torchvision zusammen aus demselben Index -- getrennt passten
# sie nicht zueinander (open-clip zieht torchvision nach; eines von PyPI
# gegen das CPU-torch ergab "operator torchvision::nms does not exist").
# Dann der Rest gegen die Liste aus pyproject; die Schicht bleibt im Cache,
# solange sich pyproject nicht aendert.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ARG ORT=onnxruntime
ARG ORT_INDEX=https://pypi.org/simple
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch torchvision --index-url "$TORCH_INDEX" \
    && python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); \
        print('\n'.join(d['project']['dependencies'] + d['project']['optional-dependencies']['atlas']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip uninstall -y onnxruntime onnxruntime-gpu \
    && pip install --no-cache-dir "$ORT" --index-url "$ORT_INDEX" --extra-index-url https://pypi.org/simple
# onnxruntime kommt zuletzt und allein: eine Abhaengigkeit zieht die
# CPU-Fassung mit, und beide Pakete teilen sich ein Verzeichnis -- gemessen
# hatte das cuda-Image danach keinen CUDAExecutionProvider. ORT_INDEX bleibt
# als Schalter fuer eine andere CUDA-Generation (ORT fuehrt dafuer eigene
# Indizes), die Vorgabe ist PyPI.

# Das Projekt selbst wird nicht als Paket installiert: es laeuft aus /app
# (`uvicorn api.main:app`, Jobs als `python -m ingest...` mit /app als
# Arbeitsverzeichnis). setuptools verweigert die Flat-Layout-Erkennung bei
# mehreren Top-Level-Paketen (api, ingest, web) -- gemessen beim ersten Bau.


FROM python:3.11-slim

# libGL und libglib brauchen OpenCV/insightface; ohne sie scheitert der Import
# erst zur Laufzeit, nicht beim Bauen. ffmpeg fuer Poster und Frames aus Videos.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Modellgewichte landen hier; als Volume gemountet ueberleben sie einen Neubau.
ENV MODEL_DIR=/models
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=20 \
    CMD curl -sf http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
