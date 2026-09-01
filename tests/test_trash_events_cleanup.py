"""Was das endgueltige Loeschen aufraeumt -- und was es stehen laesst.

`empty_trash` loescht Datei, Foto-Punkt, Gesichter und Vorschaubild
(api/routes/trash.py:130-195). Die Ereignis-Ablage (api/events_store.py,
Collection `event_meta`) kommt darin nicht vor -- weder als Import noch als
Aufruf. Diese Tests halten den heutigen Stand fest, sie fordern nichts ein.

Der urspruengliche Verdacht war "ein geloeschtes Foto bleibt Mitglied einer
Serie". Das stimmt so nicht: Mitgliedschaft wird bei jedem Aufruf aus den
Foto-Punkten abgeleitet (api/routes/events.py:170-200), und der Punkt ist weg.
Stehen bleibt der *Name* samt seiner Fotozahl -- eine Serie ohne Fotos.

Kein Netz, keine echten Dateien: Fake-Client, tmp_path.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from api import events_store
from api.qdrant_util import FACES, PHOTOS
from api.routes import events as events_route
from api.routes import trash as trash_route
from api.routes.trash import EmptyTrashRequest, empty_trash, list_trash

SERIE = "Nasen OP"
START = "2013-06-12T19:06:00"
END = "2013-06-12T19:12:00"
IM_PAPIERKORB = "2026-09-01T08:00:00"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _Point:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = dict(payload)


class FakeQdrant:
    """So viel Qdrant, wie Papierkorb und Ereignis-Ablage anfassen.

    `touched` merkt sich jede beruehrte Collection der Reihe nach -- daran
    laesst sich zeigen, dass `event_meta` beim Loeschen nie vorkommt.
    """

    def __init__(self, photos=None, faces=None, event_meta=None):
        self.stores = {
            PHOTOS: dict(photos or {}),
            FACES: dict(faces or {}),
            events_store.COLLECTION: dict(event_meta or {}),
        }
        self.touched: list[str] = []

    def _store(self, name):
        self.touched.append(name)
        return self.stores.setdefault(name, {})

    # -- Collections -------------------------------------------------------
    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self.stores]
        )

    def create_collection(self, collection_name, vectors_config=None, **kw):
        self.stores.setdefault(collection_name, {})

    # -- Punkte ------------------------------------------------------------
    def retrieve(self, collection_name, ids, **kw):
        store = self._store(collection_name)
        return [store[i] for i in ids if i in store]

    def upsert(self, collection_name, points, wait=True):
        store = self._store(collection_name)
        for p in points:
            store[p.id] = _Point(p.id, p.payload)

    def set_payload(self, collection_name, payload, points, wait=True):
        store = self._store(collection_name)
        for i in points:
            store[i].payload.update(payload)

    def delete(self, collection_name, points_selector, wait=True):
        store = self._store(collection_name)
        if isinstance(points_selector, list):
            for i in points_selector:
                store.pop(i, None)
            return
        # FilterSelector -- der eine Fall, den trash.py braucht: photo_id.
        for cond in points_selector.filter.must:
            value = cond.match.value
            for pid in [p for p, pt in store.items()
                        if (pt.payload or {}).get(cond.key) == value]:
                store.pop(pid, None)

    def scroll(self, collection_name, limit=256, offset=None, scroll_filter=None, **kw):
        store = self._store(collection_name)
        items = list(store.values())
        for cond in getattr(scroll_filter, "must_not", None) or []:
            key = cond.is_empty.key
            items = [p for p in items if (p.payload or {}).get(key) is not None]
        return items, None


@pytest.fixture(autouse=True)
def no_real_qdrant(monkeypatch, tmp_path):
    """Sicherheitsnetz: auf localhost:6333 liegt der produktive Index."""

    def boom(*a, **kw):
        raise AssertionError("Test wollte einen echten Qdrant-Client oeffnen")

    monkeypatch.setattr("api.qdrant_util.QdrantClient", boom)
    # Bibliothekswurzel fuer diesen Test: die Attrappen liegen unter tmp_path,
    # und `empty_trash` loescht nur innerhalb der Wurzel.
    monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", str(tmp_path))


@pytest.fixture
def welt(tmp_path, monkeypatch):
    """Drei Fotos einer benannten Serie, zwei davon im Papierkorb.

    Dateien liegen unter tmp_path, das Loeschprotokoll ebenfalls; der
    Thumbnail-Cache wird gar nicht erst angefasst.
    """
    ordner = tmp_path / SERIE
    ordner.mkdir()
    dateien = {}
    photos = {}
    for pid, hash_, minute, trashed in (
        ("p1", "h1", "06", True),
        ("p2", "h2", "09", True),
        ("p3", "h3", "11", False),
    ):
        datei = ordner / f"{pid}.jpg"
        datei.write_bytes(b"jpeg")
        dateien[pid] = datei
        payload = {
            "file_path": str(datei),
            "photo_id": hash_,
            "folder_name": SERIE,
            "channel": "camera",
            "taken_at": f"2013-06-12T19:{minute}:00",
            "event_name": SERIE,
            "person_names": [],
            "file_size": 4,
        }
        if trashed:
            payload["trashed_at"] = IM_PAPIERKORB
        photos[pid] = _Point(pid, payload)

    faces = {
        "f1a": _Point("f1a", {"photo_id": "h1"}),
        "f1b": _Point("f1b", {"photo_id": "h1"}),
        "f3": _Point("f3", {"photo_id": "h3"}),
    }
    meta_id = events_store._point_id(f"camera|{START}|{END}")
    meta = {
        meta_id: _Point(meta_id, {
            "name": SERIE, "channel": "camera", "start": START, "end": END,
            "photo_count": 3, "updated_at": 0.0,
        })
    }
    fake = FakeQdrant(photos=photos, faces=faces, event_meta=meta)

    monkeypatch.setattr(trash_route, "client", lambda: fake)
    monkeypatch.setattr(events_route, "client", lambda: fake)
    monkeypatch.setattr(trash_route, "drop_cached", lambda path: 0)
    monkeypatch.setattr(trash_route, "TRASH_LOG", tmp_path / "logs")
    return SimpleNamespace(q=fake, files=dateien, tmp=tmp_path)


def _alles_in_den_papierkorb(welt):
    for point in welt.q.stores[PHOTOS].values():
        point.payload["trashed_at"] = IM_PAPIERKORB


def _leeren():
    return empty_trash(EmptyTrashRequest(confirm=True))


def _rows(fake):
    """Was `events._load` aus den verbliebenen Punkten machen wuerde."""
    return {pid: dict(p.payload) for pid, p in fake.stores[PHOTOS].items()}


# --------------------------------------------------------------------------
# Was aufgeraeumt wird
# --------------------------------------------------------------------------


class TestWasAufgeraeumtWird:
    def test_datei_und_punkt_sind_weg(self, welt):
        out = _leeren()
        assert out["deleted"] == 2
        assert out["files"] == 2
        assert set(welt.q.stores[PHOTOS]) == {"p3"}
        assert not welt.files["p1"].exists()
        assert not welt.files["p2"].exists()
        assert welt.files["p3"].exists()

    def test_gesichter_des_fotos_sind_weg(self, welt):
        _leeren()
        assert set(welt.q.stores[FACES]) == {"f3"}

    def test_protokoll_nennt_jeden_geloeschten_pfad(self, welt):
        out = _leeren()
        zeilen = Path(out["log"]).read_text(encoding="utf-8").splitlines()
        assert len(zeilen) == 2
        assert str(welt.files["p1"]) in zeilen[0] or str(welt.files["p1"]) in zeilen[1]


# --------------------------------------------------------------------------
# Die Luecke: die Ereignis-Ablage wird nicht angefasst
# --------------------------------------------------------------------------


class TestEreignisAblageBleibtUnberuehrt:
    def test_empty_trash_fasst_event_meta_nie_an(self, welt):
        _leeren()
        assert events_store.COLLECTION not in welt.q.touched
        assert set(welt.q.touched) == {PHOTOS, FACES}

    def test_der_name_ueberlebt_seine_fotos(self, welt):
        """Auch wenn die ganze Serie im Papierkorb landet."""
        _alles_in_den_papierkorb(welt)
        _leeren()
        assert welt.q.stores[PHOTOS] == {}
        assert [n["name"] for n in events_store.all_names(welt.q)] == [SERIE]

    def test_die_fotozahl_bleibt_stehen(self, welt):
        """`photo_count` sagt 3, im Index liegt danach genau eines."""
        _leeren()
        (eintrag,) = events_store.all_names(welt.q)
        assert eintrag["photo_count"] == 3
        assert len(welt.q.stores[PHOTOS]) == 1

    def test_abgelehnte_zusammenlegung_ueberlebt_ebenfalls(self, welt):
        """Auch die Merkposten fuer abgelehnte Vorschlaege zeigen ins Leere."""
        events_store.reject_merge(
            welt.q,
            a=("camera", START, END),
            b=("camera", "2013-06-13T10:00:00", "2013-06-13T11:00:00"),
        )
        _alles_in_den_papierkorb(welt)
        _leeren()
        assert len(events_store.all_rejects(welt.q)) == 1

    def test_named_ohne_detail_liefert_die_leere_serie_weiter(self, welt):
        """`GET /api/events/named` liest die Ablage roh -- die Serie bleibt sichtbar."""
        _alles_in_den_papierkorb(welt)
        _leeren()
        antwort = events_route.named(detail=False)
        assert antwort["total"] == 1
        assert antwort["events"][0]["name"] == SERIE
        assert antwort["events"][0]["photo_count"] == 3

    def test_die_vorschau_warnt_nicht_vor_serien(self, welt):
        """list_trash zaehlt Bildtext und Personen -- Ereignisse nicht."""
        vorschau = list_trash()
        assert vorschau["total"] == 2
        assert "with_caption" in vorschau and "with_person" in vorschau
        assert [k for k in vorschau if "event" in k] == []


# --------------------------------------------------------------------------
# Gegenprobe: kein Geisterfoto in der Serie
# --------------------------------------------------------------------------


class TestKeinGeisterMitglied:
    def test_geloeschtes_foto_ist_kein_mitglied_mehr(self, welt):
        """Mitgliedschaft wird abgeleitet, nicht gespeichert."""
        _leeren()
        gruppen, offen = events_route._named_groups(
            _rows(welt.q), events_store.all_names(welt.q)
        )
        assert gruppen == {("camera", SERIE): ["p3"]}
        assert offen == {}

    def test_die_leere_serie_hat_in_der_detailansicht_keine_karte(self, welt):
        """`named(detail=True)` gruppiert ueber die Punkte -- ohne Fotos nichts."""
        _alles_in_den_papierkorb(welt)
        _leeren()
        gruppen, offen = events_route._named_groups(
            _rows(welt.q), events_store.all_names(welt.q)
        )
        assert gruppen == {}
        assert offen == {}
