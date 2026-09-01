"""Der einzige Pfad im Projekt, auf dem Daten unwiederbringlich verschwinden.

`empty_trash` ruft `Path.unlink()` und `q.delete()`. Danach ist die Datei weg,
der Punkt weg, die Gesichter weg -- es gibt keinen zweiten Versuch. Geprüft
wird hier deshalb vor allem, wann *nichts* passieren darf: ohne `confirm`, bei
leerem Papierkorb, bei unbekannter Kennung. Und wenn doch gelöscht wird, ob
hinterher nachvollziehbar ist, was fehlt -- das Protokoll unter `logs/` ist die
einzige Spur, die bleibt.

Zwei Dinge dürfen in diesen Tests nie vorkommen: ein echter Qdrant (auf 6333
liegt der produktive Index) und ein echter Pfad. Der Client ist ein Fake, das
Protokoll und alle Dateien liegen unter `tmp_path`.
"""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from qdrant_client.models import FilterSelector

from api.routes import trash
from api.routes.trash import (
    EmptyTrashRequest,
    TrashRequest,
    empty_trash,
    list_trash,
    set_trash,
)

STAMP = "2026-08-30T12:00:00+00:00"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class _P:
    def __init__(self, pid, payload=None):
        self.id = pid
        self.payload = payload or {}


def _passt(payload, scroll_filter):
    """Nur `must_not=[IsEmpty(...)]` -- mehr baut `list_trash` nicht.

    Qdrant zählt fehlend, `null` und leer alle als „leer"; deshalb hier die
    Wahrheitsprüfung statt eines `in`.
    """
    if scroll_filter is None:
        return True
    for cond in (scroll_filter.must_not or []):
        if not payload.get(cond.is_empty.key):
            return False
    return True


class _Q:
    """Minimal-Qdrant im Speicher: Punkt-ID -> Payload.

    Der Blätter-Zeiger ist hier ein Index statt einer Punkt-ID -- `list_trash`
    reicht ihn nur durch, für die Schleife macht das keinen Unterschied.
    """

    page = 512

    def __init__(self, photos=None):
        self.photos = dict(photos or {})
        self.retrieved: list[list] = []
        self.scrolls: list[dict] = []
        self.deleted: list[list] = []
        self.face_deletes: list = []
        self.payload_calls: list[dict] = []
        self.cleared: list[dict] = []

    def retrieve(self, collection_name, ids, **kw):
        self.retrieved.append(list(ids))
        return [_P(i, dict(self.photos[i])) for i in ids if i in self.photos]

    def scroll(self, collection_name, limit=512, offset=None, scroll_filter=None, **kw):
        self.scrolls.append({"limit": limit, "offset": offset,
                             "vectors": kw.get("with_vectors"),
                             "payload": kw.get("with_payload")})
        items = [_P(i, p) for i, p in self.photos.items() if _passt(p, scroll_filter)]
        start = int(offset or 0)
        end = min(start + min(self.page, limit), len(items))
        return items[start:end], (end if end < len(items) else None)

    def delete(self, collection_name, points_selector, wait=False):
        if collection_name == "photos":
            self.deleted.append(list(points_selector))
            for i in points_selector:
                self.photos.pop(i, None)
        else:
            self.face_deletes.append(points_selector)

    def set_payload(self, collection_name, payload, points, wait=False):
        self.payload_calls.append({"collection": collection_name, "payload": dict(payload),
                                   "points": list(points), "wait": wait})
        for i in points:
            self.photos.setdefault(i, {}).update(payload)

    def clear_payload_keys(self, collection_name, keys, points, wait=False):
        self.cleared.append({"collection": collection_name, "keys": list(keys),
                             "points": list(points), "wait": wait})
        for i in points:
            for k in keys:
                self.photos.get(i, {}).pop(k, None)


class _QAlt(_Q):
    """Ein älterer Client -- `clear_payload_keys` gibt es dort nicht."""

    @property
    def clear_payload_keys(self):
        raise AttributeError("clear_payload_keys")


class _QLose(_Q):
    """Ein Index, der `trashed_at: None` trotzdem zurückgibt.

    Ob ein ausdrückliches `null` als „leer" gilt, entscheidet Qdrant, nicht
    diese Route. Der pessimistische Fall darf sie nicht sprengen.
    """

    def scroll(self, collection_name, limit=512, offset=None, scroll_filter=None, **kw):
        return super().scroll(collection_name, limit, offset, None, **kw)


class _Thumbs:
    """Ersatz für `drop_cached` -- der echte Cache liegt im Home des Nutzers."""

    def __init__(self, je_datei=0):
        self.je_datei = je_datei
        self.calls: list[str] = []

    def __call__(self, file_path):
        self.calls.append(file_path)
        return self.je_datei if file_path else 0


# --------------------------------------------------------------------------
# Verdrahtung
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def kein_echter_qdrant(monkeypatch, tmp_path):
    """Sicherung: `client()` ungepatcht baut eine Verbindung nach 6333."""
    def verboten(*a, **kw):
        raise AssertionError("Test wollte an den echten Index oder den echten Cache")

    monkeypatch.setattr(trash, "client", verboten)
    monkeypatch.setattr(trash, "drop_cached", verboten)
    # Die Bibliothekswurzel fuer diesen Test ist tmp_path -- dort liegen die
    # Attrappen. Ohne das griffe die Schranke in `empty_trash` gegen die
    # echte Wurzel aus sources.txt und liesse nichts durch.
    monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", str(tmp_path))
    # Ungepatcht zeigt TRASH_LOG auf `logs/` im Repo. None knallt sofort und
    # sichtbar, statt still dorthin zu schreiben.
    monkeypatch.setattr(trash, "TRASH_LOG", None)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Fake-Client einhängen, Protokoll nach tmp_path umlenken."""
    def _wire(q, thumbs_je_datei=0):
        logs = tmp_path / "logs"
        thumbs = _Thumbs(thumbs_je_datei)
        monkeypatch.setattr(trash, "client", lambda: q)
        monkeypatch.setattr(trash, "TRASH_LOG", logs)
        monkeypatch.setattr(trash, "drop_cached", thumbs)
        return SimpleNamespace(q=q, logs=logs, thumbs=thumbs)

    return _wire


def _foto(tmp_path, name, stamp=STAMP, **extra):
    """Eine echte Datei unter tmp_path plus das Payload dazu."""
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"kein echtes JPEG")
    payload = {"file_path": str(f), "photo_id": f"hash-{f.stem}"}
    if stamp is not None:
        payload["trashed_at"] = stamp
    payload.update(extra)
    return f, payload


def _protokoll(logs):
    dateien = sorted(logs.glob("deleted-*.log"))
    assert len(dateien) == 1, f"genau ein Protokoll erwartet, gefunden: {dateien}"
    return dateien[0]


# --------------------------------------------------------------------------
# Vormerken und zurückholen
# --------------------------------------------------------------------------

class TestVormerken:
    def test_leere_liste_ist_ein_fehler(self):
        # Und zwar bevor überhaupt ein Client gebaut wird -- sonst hätte die
        # Sicherung oben angeschlagen.
        with pytest.raises(HTTPException) as e:
            set_trash(TrashRequest(photo_ids=[]))
        assert e.value.status_code == 400

    def test_stempel_wird_gesetzt(self, wire):
        w = wire(_Q({"a": {}}))
        assert set_trash(TrashRequest(photo_ids=["a"])) == {"trashed": 1}
        call = w.q.payload_calls[0]
        assert call["collection"] == "photos"
        assert call["points"] == ["a"]
        assert call["wait"] is True
        # Nur der Stempel -- alles andere am Foto bleibt unangetastet.
        assert set(call["payload"]) == {"trashed_at"}

    def test_stempel_ist_utc_auf_sekunden(self, wire):
        w = wire(_Q({"a": {}}))
        set_trash(TrashRequest(photo_ids=["a"]))
        stamp = w.q.payload_calls[0]["payload"]["trashed_at"]
        gesetzt = datetime.fromisoformat(stamp)
        assert gesetzt.tzinfo is not None
        assert gesetzt.utcoffset().total_seconds() == 0
        assert gesetzt.microsecond == 0
        assert abs((datetime.now(timezone.utc) - gesetzt).total_seconds()) < 60

    def test_stempel_hat_feste_breite(self, wire):
        # `list_trash` sortiert die Stempel als Text. Eine wackelnde Länge
        # (mal mit, mal ohne Bruchteile) sortierte falsch.
        w = wire(_Q({"a": {}}))
        set_trash(TrashRequest(photo_ids=["a"]))
        stamp = w.q.payload_calls[0]["payload"]["trashed_at"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", stamp)

    def test_vormerken_ist_die_voreinstellung(self):
        assert TrashRequest(photo_ids=["a"]).trashed is True

    def test_mehrere_in_einem_aufruf(self, wire):
        w = wire(_Q({"a": {}, "b": {}, "c": {}}))
        assert set_trash(TrashRequest(photo_ids=["a", "b", "c"])) == {"trashed": 3}
        assert len(w.q.payload_calls) == 1

    def test_keine_datei_wird_angefasst(self, wire, tmp_path):
        # Die erste Stufe ist folgenlos -- das ist ihr ganzer Sinn.
        f, payload = _foto(tmp_path, "a.jpg", stamp=None)
        wire(_Q({"a": payload}))
        set_trash(TrashRequest(photo_ids=["a"]))
        assert f.is_file()

    def test_fehler_wird_nicht_geschluckt(self, wire):
        class _Kaputt(_Q):
            def set_payload(self, **kw):
                raise RuntimeError("Index weg")

        wire(_Kaputt({"a": {}}))
        with pytest.raises(HTTPException) as e:
            set_trash(TrashRequest(photo_ids=["a"]))
        assert e.value.status_code == 500
        assert "Index weg" in e.value.detail


class TestZurueckholen:
    def test_stempel_wird_entfernt(self, wire):
        w = wire(_Q({"a": {"trashed_at": STAMP}}))
        assert set_trash(TrashRequest(photo_ids=["a"], trashed=False)) == {"restored": 1}
        assert w.q.cleared[0]["keys"] == ["trashed_at"]
        assert w.q.cleared[0]["points"] == ["a"]
        assert "trashed_at" not in w.q.photos["a"]

    def test_gerettetes_liegt_nicht_mehr_im_papierkorb(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP}, "b": {"trashed_at": STAMP}}))
        set_trash(TrashRequest(photo_ids=["a"], trashed=False))
        liste = list_trash()
        assert liste["total"] == 1
        assert [p["id"] for p in liste["photos"]] == ["b"]

    def test_alter_client_setzt_den_stempel_auf_null(self, wire):
        # Kein `clear_payload_keys`: dann muss wenigstens der Wert weg sein.
        w = wire(_QAlt({"a": {"trashed_at": STAMP}}))
        assert set_trash(TrashRequest(photo_ids=["a"], trashed=False)) == {"restored": 1}
        assert w.q.payload_calls[0]["payload"] == {"trashed_at": None}
        assert w.q.photos["a"]["trashed_at"] is None

    def test_auch_der_null_stempel_zaehlt_nicht_als_papierkorb(self, wire):
        wire(_QAlt({"a": {"trashed_at": STAMP}}))
        set_trash(TrashRequest(photo_ids=["a"], trashed=False))
        assert list_trash()["total"] == 0


# --------------------------------------------------------------------------
# Die Liste
# --------------------------------------------------------------------------

class TestListe:
    def test_nur_vorgemerkte(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP}, "b": {}}))
        liste = list_trash()
        assert liste["total"] == 1
        assert [p["id"] for p in liste["photos"]] == ["a"]

    def test_neueste_markierung_zuerst(self, wire):
        wire(_Q({
            "alt": {"trashed_at": "2026-08-01T09:00:00+00:00"},
            "neu": {"trashed_at": "2026-08-30T09:00:00+00:00"},
            "mittel": {"trashed_at": "2026-08-15T09:00:00+00:00"},
        }))
        assert [p["id"] for p in list_trash()["photos"]] == ["neu", "mittel", "alt"]

    def test_null_stempel_sprengt_die_sortierung_nicht(self, wire):
        # Nach einem Rettungsversuch auf altem Client steht `null` im Payload.
        # Gibt der Index den Punkt trotzdem zurück, darf `sort` nicht an
        # None gegen str scheitern.
        wire(_QLose({"a": {"trashed_at": STAMP}, "b": {"trashed_at": None}}))
        assert [p["id"] for p in list_trash()["photos"]] == ["a", "b"]

    def test_die_karte_bekommt_alle_felder(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP, "file_path": "/x/a.jpg", "caption_de": "Ein Hund",
                       "caption_display": "Hund", "date": "2013-05-01", "file_size": 42,
                       "scene_tags": ["hund"], "person_names": ["Zelda"]}}))
        eintrag = list_trash()["photos"][0]
        assert set(eintrag) == {"id", "file_path", "trashed_at", "caption_display",
                                "caption_de", "date", "scene_tags", "person_names",
                                "file_size"}
        assert eintrag["file_path"] == "/x/a.jpg"

    def test_fehlende_felder_werden_zu_none_und_leer(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP}}))
        eintrag = list_trash()["photos"][0]
        assert eintrag["file_path"] is None
        assert eintrag["scene_tags"] == []
        assert eintrag["person_names"] == []

    def test_szenen_tags_werden_gekuerzt(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP, "scene_tags": [f"t{i}" for i in range(12)]}}))
        assert len(list_trash()["photos"][0]["scene_tags"]) == 6

    def test_summe_der_bytes_vertraegt_luecken(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP, "file_size": 100},
                 "b": {"trashed_at": STAMP, "file_size": None},
                 "c": {"trashed_at": STAMP}}))
        assert list_trash()["bytes"] == 100

    def test_was_verloren_geht_wird_gezaehlt(self, wire):
        # Die Zahlen stehen im Bestätigungsdialog. Zu niedrig gezählt heißt:
        # jemand löscht Beschreibungen, von denen er nichts wusste.
        wire(_Q({"a": {"trashed_at": STAMP, "caption_de": "Ein Hund", "person_names": ["Zelda"]},
                 "b": {"trashed_at": STAMP, "caption_de": "", "person_names": []},
                 "c": {"trashed_at": STAMP, "person_names": ["Bodo"]}}))
        liste = list_trash()
        assert liste["with_caption"] == 1
        assert liste["with_person"] == 2

    def test_blaettert_bis_zum_ende(self, wire):
        # 512er-Häppchen: bei einem Papierkorb über der Häppchengröße wäre
        # eine einzelne Abfrage schlicht eine falsche Gesamtzahl.
        q = _Q({f"p{i}": {"trashed_at": STAMP} for i in range(1200)})
        q.page = 500
        wire(q)
        assert list_trash(limit=10)["total"] == 1200
        assert len(q.scrolls) == 3

    def test_ohne_vektoren(self, wire):
        # 14.000 Punkte mal 512 Floats, nur um Pfade aufzuzählen.
        w = wire(_Q({"a": {"trashed_at": STAMP}}))
        list_trash()
        assert w.q.scrolls[0]["vectors"] is False
        assert w.q.scrolls[0]["payload"] is True

    def test_seitenweise(self, wire):
        wire(_Q({f"p{i}": {"trashed_at": f"2026-08-{i + 1:02d}T09:00:00+00:00"}
                 for i in range(5)}))
        seite = list_trash(limit=2, offset=2)
        assert seite["total"] == 5
        assert seite["returned"] == 2
        assert seite["offset"] == 2
        assert [p["id"] for p in seite["photos"]] == ["p2", "p1"]

    def test_offset_hinter_dem_ende(self, wire):
        wire(_Q({"a": {"trashed_at": STAMP}}))
        seite = list_trash(limit=10, offset=50)
        assert seite["photos"] == []
        assert seite["returned"] == 0
        assert seite["total"] == 1

    def test_leerer_papierkorb(self, wire):
        wire(_Q({"a": {}}))
        assert list_trash() == {"total": 0, "bytes": 0, "photos": [], "offset": 0,
                                "returned": 0, "with_caption": 0, "with_person": 0}


# --------------------------------------------------------------------------
# Leeren -- ohne Bestätigung
# --------------------------------------------------------------------------

class TestOhneConfirm:
    def test_confirm_ist_aus_bis_jemand_es_setzt(self):
        req = EmptyTrashRequest()
        assert req.confirm is False
        assert req.photo_ids == []

    def test_ohne_confirm_bleibt_alles_liegen(self, wire, tmp_path):
        f, payload = _foto(tmp_path, "a.jpg")
        w = wire(_Q({"a": payload}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"]))
        assert out["would_delete"] == 1
        assert out["note"] == "Ohne confirm wird nichts gelöscht."
        # Gleiche Schluessel wie beim echten Lauf -- der Aufrufer soll nicht
        # auf die Gestalt der Antwort verzweigen muessen.
        assert out["deleted"] == 0 and out["files"] == 0 and out["failed"] == []
        assert f.is_file()
        assert w.q.deleted == []
        assert w.q.face_deletes == []
        assert w.q.photos["a"] == payload
        assert w.thumbs.calls == []

    def test_ohne_confirm_kein_protokoll(self, wire, tmp_path):
        # Das Protokoll ist die Ankündigung des Löschens. Ein Eintrag ohne
        # Löschung liest sich hinterher wie ein Verlust, der keiner war.
        _f, payload = _foto(tmp_path, "a.jpg")
        w = wire(_Q({"a": payload}))
        empty_trash(EmptyTrashRequest(photo_ids=["a"]))
        assert not w.logs.exists()

    def test_ohne_ids_zaehlt_den_ganzen_papierkorb(self, wire, tmp_path):
        f1, p1 = _foto(tmp_path, "a.jpg")
        f2, p2 = _foto(tmp_path, "b.jpg")
        _f3, p3 = _foto(tmp_path, "c.jpg", stamp=None)
        w = wire(_Q({"a": p1, "b": p2, "c": p3}))
        out = empty_trash(EmptyTrashRequest())
        assert out["would_delete"] == 2
        assert f1.is_file() and f2.is_file()
        assert w.q.deleted == []

    def test_leerer_papierkorb_auch_mit_confirm(self, wire):
        # Die leere Liste kommt vor der Bestätigungsfrage -- „nichts da" ist
        # keine gefährliche Antwort.
        w = wire(_Q({"a": {}}))
        assert empty_trash(EmptyTrashRequest(confirm=True)) == {
            "deleted": 0, "note": "Der Papierkorb ist leer."}
        assert w.q.retrieved == []
        assert w.q.deleted == []
        assert not w.logs.exists()


# --------------------------------------------------------------------------
# Leeren -- mit Bestätigung
# --------------------------------------------------------------------------

class TestMitConfirm:
    def test_datei_punkt_gesichter_vorschau(self, wire, tmp_path):
        f, payload = _foto(tmp_path, "a.jpg")
        w = wire(_Q({"a": payload}), thumbs_je_datei=2)
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))

        assert not f.exists()
        assert w.q.deleted == [["a"]]
        assert w.thumbs.calls == [str(f)]
        assert out["deleted"] == 1
        assert out["files"] == 1
        assert out["thumbs"] == 2
        assert out["failed"] == []

        sel = w.q.face_deletes[0]
        assert isinstance(sel, FilterSelector)
        bedingung = sel.filter.must[0]
        assert bedingung.key == "photo_id"
        assert bedingung.match.value == "hash-a"

    def test_ohne_ids_nur_der_papierkorb(self, wire, tmp_path):
        # Der wichtigste Fall: „alles leeren" darf nicht heißen „alles".
        vorgemerkt, p1 = _foto(tmp_path, "weg.jpg")
        behalten, p2 = _foto(tmp_path, "bleibt.jpg", stamp=None)
        w = wire(_Q({"a": p1, "b": p2}))
        out = empty_trash(EmptyTrashRequest(confirm=True))
        assert out["deleted"] == 1
        assert not vorgemerkt.exists()
        assert behalten.is_file()
        assert "b" in w.q.photos

    def test_unbekannte_kennung_loescht_nichts(self, wire, tmp_path):
        f, payload = _foto(tmp_path, "a.jpg")
        w = wire(_Q({"a": payload}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["gibtesnicht"], confirm=True))
        assert out["note"] == "Keine dieser Kennungen steht im Index."
        assert out["deleted"] == 0
        assert out["files"] == 0
        assert out["failed"] == []
        assert f.is_file()
        assert "a" in w.q.photos
        assert w.q.face_deletes == []
        # Kein Protokoll: es gibt nichts zu protokollieren. Vorher entstand
        # hier eine leere Datei unter logs/ fuer jeden Fehlgriff.
        assert not w.logs.exists() or list(w.logs.glob("deleted-*.log")) == []

    def test_datei_schon_weg_zaehlt_als_erledigt(self, wire, tmp_path):
        f, payload = _foto(tmp_path, "a.jpg")
        f.unlink()
        w = wire(_Q({"a": payload}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert out["files"] == 1
        assert out["failed"] == []
        # Der Punkt muss trotzdem verschwinden, sonst bleibt eine Karteileiche.
        assert w.q.deleted == [["a"]]

    def test_punkt_ohne_pfad_fasst_keine_datei_an(self, wire):
        w = wire(_Q({"a": {"trashed_at": STAMP, "photo_id": "hash-a"}}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert out["files"] == 0
        assert out["failed"] == []
        assert out["thumbs"] == 0
        assert w.q.deleted == [["a"]]

    def test_nicht_loeschbares_wird_gemeldet_nicht_verschwiegen(self, wire, tmp_path):
        # Ein Verzeichnis statt einer Datei: `unlink` scheitert. Die Antwort
        # muss den Pfad nennen, sonst hält der Nutzer das Foto für gelöscht.
        ordner = tmp_path / "kein-foto"
        ordner.mkdir()
        wire(_Q({"a": {"trashed_at": STAMP, "file_path": str(ordner)}}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert out["files"] == 0
        assert [x["path"] for x in out["failed"]] == [str(ordner)]
        assert out["failed"][0]["error"]
        assert ordner.is_dir()

    def test_ohne_photo_id_keine_gesichter_loeschung(self, wire, tmp_path):
        # Ein `MatchValue(None)` träfe sonst jedes Gesicht ohne `photo_id`.
        _f, payload = _foto(tmp_path, "a.jpg")
        payload.pop("photo_id")
        w = wire(_Q({"a": payload}))
        empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert w.q.face_deletes == []

    def test_in_haeppchen_nachgeschlagen(self, wire):
        # 300 Kennungen in einer Abfrage wären eine Wand aus JSON.
        ids = [f"p{i}" for i in range(300)]
        w = wire(_Q({i: {"trashed_at": STAMP, "photo_id": f"h{i}"} for i in ids}))
        empty_trash(EmptyTrashRequest(photo_ids=ids, confirm=True))
        assert [len(b) for b in w.q.retrieved] == [128, 128, 44]

    def test_vorschau_je_datei_zusammengezaehlt(self, wire, tmp_path):
        f1, p1 = _foto(tmp_path, "a.jpg")
        f2, p2 = _foto(tmp_path, "b.jpg")
        w = wire(_Q({"a": p1, "b": p2}), thumbs_je_datei=3)
        out = empty_trash(EmptyTrashRequest(photo_ids=["a", "b"], confirm=True))
        assert out["thumbs"] == 6
        assert w.thumbs.calls == [str(f1), str(f2)]


# --------------------------------------------------------------------------
# Das Protokoll
# --------------------------------------------------------------------------

class TestProtokoll:
    def test_eine_zeile_je_foto_mit_kennung_pfad_beschreibung(self, wire, tmp_path):
        f1, p1 = _foto(tmp_path, "a.jpg", caption_de="Ein Hund im Schnee")
        f2, p2 = _foto(tmp_path, "b.jpg")
        w = wire(_Q({"a": p1, "b": p2}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a", "b"], confirm=True))

        zeilen = _protokoll(w.logs).read_text(encoding="utf-8").splitlines()
        assert len(zeilen) == 2
        assert zeilen[0].split("\t") == ["a", str(f1), "Ein Hund im Schnee"]
        # Ohne Beschreibung bleibt das Feld leer, aber es bleibt ein Feld.
        assert zeilen[1].split("\t") == ["b", str(f2), ""]
        assert out["log"] == str(_protokoll(w.logs))

    def test_verzeichnis_wird_angelegt(self, wire, tmp_path):
        _f, payload = _foto(tmp_path, "a.jpg")
        w = wire(_Q({"a": payload}))
        assert not w.logs.exists()
        empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert w.logs.is_dir()
        assert re.fullmatch(r"deleted-\d+\.log", _protokoll(w.logs).name)

    def test_steht_schon_da_wenn_das_aufraeumen_scheitert(self, wire, tmp_path):
        # Genau dafür ist es da: die Datei ist an dieser Stelle bereits weg,
        # der Aufrufer sieht nur einen 500. Ohne Protokoll wüsste niemand,
        # welche Fotos das betraf.
        class _KaputtesDelete(_Q):
            def delete(self, collection_name, points_selector, wait=False):
                raise RuntimeError("Collection weg")

        f, payload = _foto(tmp_path, "a.jpg", caption_de="Ein Hund")
        w = wire(_KaputtesDelete({"a": payload}))
        with pytest.raises(HTTPException) as e:
            empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert e.value.status_code == 500
        assert "Collection weg" in e.value.detail
        assert str(f) in _protokoll(w.logs).read_text(encoding="utf-8")

    def test_auch_das_nicht_loeschbare_steht_drin(self, wire, tmp_path):
        ordner = tmp_path / "kein-foto"
        ordner.mkdir()
        w = wire(_Q({"a": {"trashed_at": STAMP, "file_path": str(ordner)}}))
        empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert str(ordner) in _protokoll(w.logs).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Was die Route *nicht* prüft
# --------------------------------------------------------------------------

class TestWurzelschranke:
    """Der Pfad kommt aus dem Payload -- also wird geprüft, wohin er zeigt.

    Ohne diese Schranke löscht ein Punkt mit beliebigem `file_path` eine
    beliebige Datei auf der Maschine. Gefunden beim Schreiben dieser Tests.
    """

    def test_pfad_ausserhalb_der_bibliothek_wird_nicht_geloescht(self, wire, tmp_path, monkeypatch):
        bib = tmp_path / "bibliothek"
        bib.mkdir()
        monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", str(bib))
        fremd = tmp_path / "anderswo" / "wichtig.txt"
        fremd.parent.mkdir()
        fremd.write_text("nicht aus der Bibliothek", encoding="utf-8")

        wire(_Q({"a": {"trashed_at": STAMP, "file_path": str(fremd)}}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert fremd.exists(), "Datei ausserhalb der Bibliothek wurde geloescht"
        assert out["deleted"] == 0
        assert out["outside"] == 1

    def test_praefix_allein_genuegt_nicht(self, wire, tmp_path, monkeypatch):
        # `/…/photo-alt` faengt mit `/…/photo` an und gehoert trotzdem nicht dazu.
        bib = tmp_path / "photo"
        bib.mkdir()
        monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", str(bib))
        nachbar = tmp_path / "photo-alt"
        nachbar.mkdir()
        fremd = nachbar / "wichtig.txt"
        fremd.write_text("Nachbarverzeichnis", encoding="utf-8")

        wire(_Q({"a": {"trashed_at": STAMP, "file_path": str(fremd)}}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert fremd.exists()
        assert out["outside"] == 1

    def test_ohne_wurzel_wird_gar_nichts_geloescht(self, wire, tmp_path, monkeypatch):
        # Lieber laut verweigern als im Zweifel loeschen.
        monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", "")
        monkeypatch.setattr(trash, "SOURCES_FILE", tmp_path / "gibtsnicht.txt")
        f, payload = _foto(tmp_path, "a.jpg")
        wire(_Q({"a": payload}))
        with pytest.raises(HTTPException) as exc:
            empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert exc.value.status_code == 500
        assert f.exists()


class TestNurWasImPapierkorbLiegt:
    """`photo_ids` waehlt aus dem Papierkorb aus -- es umgeht ihn nicht.

    Vorher galt die Papierkorb-Bedingung nur im Zweig ohne Kennungen. Ein
    Aufruf mit beliebigen Kennungen und `confirm` loeschte damit Fotos, die
    nie vorgemerkt waren: die erste, bewusst umkehrbare Stufe liess sich
    vollstaendig ueberspringen.
    """

    def test_nicht_vorgemerktes_bleibt_liegen(self, wire, tmp_path):
        f, payload = _foto(tmp_path, "a.jpg", stamp=None)
        wire(_Q({"a": payload}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a"], confirm=True))
        assert f.exists(), "Foto ohne Papierkorb-Stempel wurde geloescht"
        assert out["deleted"] == 0
        assert out["skipped"] == 1

    def test_vorgemerktes_daneben_wird_geloescht(self, wire, tmp_path):
        offen, p_offen = _foto(tmp_path, "offen.jpg", stamp=None)
        weg, p_weg = _foto(tmp_path, "weg.jpg")
        wire(_Q({"a": p_offen, "b": p_weg}))
        out = empty_trash(EmptyTrashRequest(photo_ids=["a", "b"], confirm=True))
        assert offen.exists()
        assert not weg.exists()
        assert out["deleted"] == 1 and out["skipped"] == 1

    def test_die_vorschau_zaehlt_dasselbe_wie_der_lauf(self, wire, tmp_path):
        # Sonst verspricht der Bestaetigungsdialog eine andere Zahl, als
        # danach geschieht.
        _, p_offen = _foto(tmp_path, "offen.jpg", stamp=None)
        _, p_weg = _foto(tmp_path, "weg.jpg")
        wire(_Q({"a": p_offen, "b": p_weg}))
        vorschau = empty_trash(EmptyTrashRequest(photo_ids=["a", "b"], confirm=False))
        assert vorschau["would_delete"] == 1
        assert vorschau["skipped"] == 1
