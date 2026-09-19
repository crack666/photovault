# PhotoVault -- das Image fuer den Verbund (Qdrant + API + Oberflaeche).
#
# Zwei Stufen, aus zwei gemessenen Gruenden (19.09.2026):
#
# 1. insightface kommt von PyPI als Quellpaket und kompiliert eine
#    Cython-Erweiterung -- auf python:3.11-slim ohne g++ brach der Bau ab
#    ("No such file or directory: 'g++'"). Der Compiler gehoert in die
#    Baustufe, nicht ins Image, das jeder laedt.
# 2. `pip install torch` zieht unter Linux die CUDA-Fassung samt
#    NVIDIA-Bibliotheken, rund 3 GB -- fuer einen Container, der in v1
#    keine GPU bekommt. Die CPU-Fassung ist ein Zehntel davon; Gesichter
#    und CLIP rechnen im Container ohnehin auf dem Prozessor (README:
#    1,6-2,6 Fotos/s). Die GPU-Durchreichung (v2) bekommt ein eigenes Image.
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

# Erst torch *und* torchvision aus dem CPU-Index -- zusammen, damit die
# Versionen zueinander passen: open-clip zieht torchvision nach, und eines
# von PyPI gegen das CPU-torch ergab beim ersten Rauchtest "operator
# torchvision::nms does not exist". Dann der Rest gegen die Liste aus
# pyproject; die Schicht bleibt im Cache, solange sich pyproject nicht
# aendert, und ein spaeteres `pip install` zieht torch nicht als CUDA nach.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); \
        print('\n'.join(d['project']['dependencies'] + d['project']['optional-dependencies']['atlas']))" \
        > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt onnxruntime

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
