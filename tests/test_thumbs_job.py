"""`tools.thumbs --warm` meldet sich auf der Jobs-Seite (photovault#31).

Vorher legte `thumbs` als einziger startbarer Lauf keinen `JobTracker` an:
kein Fortschritt, `busy` nie wahr -- der Knopf blieb aktiv und ein Doppelklick
startete zwei Laeufe --, und hinterher stand nichts in der Historie.

Ohne Netzlaufwerk und ohne Qdrant: die Originale sind kleine JPEGs in
`tmp_path`, Qdrant ist ein Woerterbuch, das nur kann, was Tracker, Bericht und
Jobs-Route brauchen.
"""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException
from PIL import Image

import api.thumbs
import tools.thumbs as thumbs
from api.qdrant_util import FACES, PHOTOS
from ingest import jobs as jobs_mod


class _Punkt:
    def __init__(self, payload):
        self.payload = payload


class FakeQdrant:
    """Fotos, Gesichter und die Job-Collection in einem."""

    def __init__(self, fotos, gesichter=()):
        self.fotos = list(fotos)
        self.gesichter = list(gesichter)
        self.jobs: dict[str, dict] = {}
        self.writes: list[dict] = []

    # -- was der Tracker braucht ------------------------------------------
    def get_collection(self, name):
        vec = types.SimpleNamespace(size=jobs_mod.DEFAULT_VECTOR_SIZE)
        return types.SimpleNamespace(
            config=types.SimpleNamespace(params=types.SimpleNamespace(vectors=vec)))

    def upsert(self, collection_name, points, wait=True):
        assert collection_name == jobs_mod.COLLECTION
        for p in points:
            self.jobs[p.id] = dict(p.payload)
            self.writes.append(dict(p.payload))

    def set_payload(self, collection_name, payload, points, wait=True):
        for pid in points:
            self.jobs.setdefault(pid, {}).update(payload)

    # -- was Bericht, Lauf und `list_jobs` lesen ----------------------------
    def scroll(self, collection_name, limit=None, offset=None, with_payload=None,
               with_vectors=None, **kw):
        if collection_name == PHOTOS:
            daten = self.fotos
        elif collection_name == FACES:
            daten = self.gesichter
        elif collection_name == jobs_mod.COLLECTION:
            daten = list(self.jobs.values())
        else:
            raise AssertionError(collection_name)
        return [_Punkt(dict(d)) for d in daten], None

    def eintraege(self, kind="thumbs"):
        return [j for j in jobs_mod.list_jobs(self) if j.get("kind") == kind]


def _jpeg(pfad, farbe=(20, 80, 140)):
    Image.new("RGB", (64, 48), farbe).save(pfad, format="JPEG")
    return str(pfad)


@pytest.fixture
def archiv(tmp_path, monkeypatch):
    """Zwei Fotos (eines mit Gesicht), leerer Cache, Qdrant als Attrappe."""
    cache = tmp_path / "cache"
    for mod in (api.thumbs, thumbs):
        monkeypatch.setattr(mod, "CACHE_DIR", cache)
        monkeypatch.setattr(mod, "LEGACY_CACHE", cache)
    a = _jpeg(tmp_path / "a.jpg")
    b = _jpeg(tmp_path / "b.jpg", (200, 30, 30))
    q = FakeQdrant(
        fotos=[{"file_path": a, "content_sha256": "ha"},
               {"file_path": b, "content_sha256": "hb"}],
        gesichter=[{"file_path": a, "box": [4, 4, 40, 40], "content_sha256": "ha"}],
    )
    monkeypatch.setattr(thumbs, "client", lambda: q)
    # Jedes Update schreiben, nicht nur alle zwei Sekunden -- sonst sieht der
    # Test bei einem schnellen Lauf nur Anfang und Ende.
    monkeypatch.setattr(jobs_mod, "FLUSH_SECONDS", 0.0)
    monkeypatch.delenv("PHOTOVAULT_JOB_CHILD", raising=False)
    return q


def test_warm_steht_danach_in_der_historie(archiv):
    assert thumbs.main(["--warm", "--sizes", "160"]) == 0

    eintraege = archiv.eintraege()
    assert len(eintraege) == 1, "genau ein Lauf, genau ein Eintrag"
    job = eintraege[0]
    assert job["status"] == "done"
    assert job["finished_at"]
    assert "2 Kacheln" in job["phase"] and "1 Gesichtsausschnitte" in job["phase"]
    assert job["errors"] == 0


def test_warm_meldet_fortschritt_je_phase(archiv):
    thumbs.main(["--warm", "--sizes", "160"])

    laufend = [w for w in archiv.writes if w["status"] == "running"]
    # `starting` schreibt der Tracker selbst, sobald es ihn gibt.
    phasen = [w["phase"] for w in laufend if w["phase"] != "starting"]
    # Angemeldet, bevor gezaehlt wird -- dann Kacheln, dann Gesichter.
    assert phasen[0] == "Bestandsaufnahme"
    assert "Kacheln" in phasen and "Gesichter" in phasen
    assert phasen.index("Kacheln") < phasen.index("Gesichter")

    kacheln = [w for w in laufend if w["phase"] == "Kacheln"]
    assert kacheln[0]["total"] == 2
    assert [w["processed"] for w in kacheln] == sorted(w["processed"] for w in kacheln)
    assert kacheln[-1]["processed"] == 2
    assert kacheln[-1]["percent"] == 100.0


def test_knopf_zeigt_laeuft_und_startet_kein_zweites_mal(archiv, monkeypatch):
    """Waehrend des Laufs: `busy` wahr, `POST /run` lehnt mit 409 ab."""
    from api.routes import jobs as route

    monkeypatch.setattr(route, "client", lambda: archiv)
    monkeypatch.setattr(route, "llm_models", lambda: [])
    monkeypatch.setattr(route.subprocess, "Popen",
                        lambda *a, **kw: pytest.fail("zweiter Lauf gestartet"))

    gesehen = []
    echt = thumbs.get_thumb

    def mitten_im_lauf(*a, **kw):
        if not gesehen:
            stand = route.all_jobs()
            knopf = next(r for r in stand["runnable"] if r["id"] == "thumbs")
            with pytest.raises(HTTPException) as e:
                route.run_job(route.RunRequest(job="thumbs"))
            gesehen.append((knopf["busy"], e.value.status_code, stand["running"]))
        return echt(*a, **kw)

    monkeypatch.setattr(thumbs, "get_thumb", mitten_im_lauf)
    thumbs.main(["--warm", "--sizes", "160", "--no-faces"])

    assert gesehen == [(True, 409, 1)]
    # Und danach ist der Knopf wieder frei.
    knopf = next(r for r in route.all_jobs()["runnable"] if r["id"] == "thumbs")
    assert knopf["busy"] is False


def test_fehler_werden_ueber_beide_phasen_gezaehlt(archiv, monkeypatch):
    def kaputt(pfad, *a, **kw):
        raise OSError("nicht lesbar")

    monkeypatch.setattr(thumbs, "get_thumb", kaputt)
    thumbs.main(["--warm", "--sizes", "160"])

    job = archiv.eintraege()[0]
    assert job["status"] == "done-with-errors"
    assert job["errors"] == 3          # zwei Kacheln + ein Gesicht
    assert {f["reason"] for f in job["failures"]} == {"nicht lesbar"}


def test_abbruch_steht_nicht_als_laufend_da(archiv, monkeypatch):
    def strg_c(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(thumbs, "get_thumb", strg_c)
    with pytest.raises(KeyboardInterrupt):
        thumbs.main(["--warm", "--sizes", "160"])
    assert archiv.eintraege()[0]["status"] == "aborted"


def test_ein_absturz_steht_als_fehler_da(archiv, monkeypatch):
    def kaputt(*a, **kw):
        raise RuntimeError("Qdrant weg\nmit Stacktrace")

    monkeypatch.setattr(thumbs, "report", kaputt)
    with pytest.raises(RuntimeError):
        thumbs.main(["--warm"])
    job = archiv.eintraege()[0]
    assert job["status"] == "error"
    assert job["phase"] == "Qdrant weg"


def test_nachsehen_ohne_warm_legt_keinen_eintrag_an(archiv):
    """Der Bericht dauert Sekunden und ist kein Lauf hinter dem Knopf."""
    thumbs.main(["--sizes", "160"])
    assert archiv.eintraege() == []


def test_ohne_qdrant_laeuft_warm_trotzdem(archiv, monkeypatch):
    """Tracking darf den Lauf nie stoppen."""
    def kein_tracker(*a, **kw):
        raise RuntimeError("keine Collection")

    monkeypatch.setattr(jobs_mod, "JobTracker", kein_tracker)
    assert thumbs.main(["--warm", "--sizes", "160"]) == 0
    assert len(list(thumbs.CACHE_DIR.rglob("*_160.jpg"))) == 3
