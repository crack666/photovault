"""FastAPI Application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import events, faces, ingest, jobs, persons, photos, search

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PhotoVault API starting...")
    yield
    logger.info("PhotoVault API shutting down...")


app = FastAPI(title="PhotoVault API", version="0.1.0", description="Lokale Foto-Suche", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(persons.router, prefix="/api/persons", tags=["persons"])
app.include_router(faces.router, prefix="/api/faces", tags=["faces"])
app.include_router(photos.router, prefix="/api/photos", tags=["photos"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["ingest"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "photovault"}


@app.get("/")
def ui_index():
    index = WEB_DIR / "index.html"
    if not index.is_file():
        return {"status": "ok", "ui": False}
    return FileResponse(index)


@app.get("/jobs.html")
def ui_jobs():
    page = WEB_DIR / "jobs.html"
    if not page.is_file():
        return {"status": "ok", "ui": False}
    return FileResponse(page)


if (WEB_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

