FROM python:3.11-slim

# libGL und libglib brauchen OpenCV/insightface; ohne sie scheitert der Import
# erst zur Laufzeit, nicht beim Bauen.
RUN apt-get update && apt-get install -y --no-install-recommends         libgl1-mesa-glx libglib2.0-0     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Erst die Abhaengigkeiten: sie aendern sich selten, der Code staendig --
# so bleibt die teure Installationsschicht im Cache.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e . || pip install --no-cache-dir         "insightface>=0.7.3" "open-clip-torch>=2.24" "Pillow>=10.0"         "qdrant-client>=1.9" "fastapi>=0.111" "uvicorn>=0.30" "pydantic>=2.0"         python-multipart aiofiles "numpy>=1.26" "torch>=2.2" "piexif>=1.1.3"

COPY . .

# Modellgewichte landen hier; als Volume gemountet ueberleben sie einen Neubau.
ENV MODEL_DIR=/models
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
