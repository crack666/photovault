"""Die Albumliste und das Umbenennen -- `api/routes/albums.py`.

Beides ist Oberflächenvertrag: die Liste füllt den Ablage-Reiter, und der
Trockenlauf ist das, was der Nutzer liest, bevor er auf „Umbenennen" klickt.
Zwei Zusicherungen tragen dabei mehr als der Rest.

1. **Die Liste schneidet ab, die Zahl daneben nicht.** Bei 500 Alben liefert
   die Route 400 Einträge. Stünde daneben `total: 400`, wäre der Rest nicht
   nur unsichtbar, sondern auch unauffällig -- niemand sucht 100 Alben, von
   denen die Kopfzeile nichts weiß. Genau das passiert in der alten Suche:
   `api/routes/search.py:363` und `:374` melden `total=len(results)`, also die
   Seitenlänge. Hier ist es richtig herum, und dieser Test hält es fest.

2. **Der echte Lauf kann scheitern, nachdem der Trockenlauf grün war.**
   Zwischen beiden liegt ein Nutzerklick. In der Zeit kann der Ordner weg
   sein, das Ziel entstanden oder der Index nicht erreichbar. Jeder dieser
   Fälle hat einen eigenen Status, und der Teilfehlschlag -- Ordner umbenannt,
   Index nicht nachgezogen -- ist kein Fehler, sondern ein Ergebnis mit
   `ok: false`. Ein 500er würde ihn verschlucken.

Kein Test spricht mit Qdrant und keiner fasst eine echte Datei an: der Index
ist ein Fake, die Ordner liegen unter `tmp_path`.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.routes import albums
from ingest.identity import photo_id_for, point_id_for


class _Point:
    def __init__(self, pid, payload=None):
        self.id = pid
        self.payload = dict(payload or {})
        self.vector = {"clip": [0.1]}


def _trifft(point, scroll_filter) -> bool:
    """Nur die zwei Bedingungen, die dieser Code wirklich stellt.

    `visible()` hängt eine IsEmptyCondition auf `trashed_at` an,
    `_photos_under` filtert auf `folder_name`. Mehr kann der Fake nicht, und
    mehr braucht er auch nicht.
    """
    if scroll_filter is None:
        return True
    payload = point.payload or {}
    for cond in getattr(scroll_filter, "must", None) or []:
        leer = getattr(cond, "is_empty", None)
        if leer is not None:
            if payload.get(leer.key) is not None:
                return False
            continue
        treffer = getattr(cond, "match", None)
        if treffer is not None and payload.get(cond.key) != treffer.value:
            return False
    return True


class _Q:
    """Minimal-Qdrant: blättert in Häppchen aus und merkt sich jeden Schreibzugriff."""

    def __init__(self, points=(), upsert_error=None):
        self.points = list(points)
        self.upsert_error = upsert_error
        self.calls = []
        self.writes = []

    def scroll(self, collection_name, scroll_filter=None, limit=256, offset=None, **kw):
        treffer = [p for p in self.points if _trifft(p, scroll_filter)]
        start = offset or 0
        end = min(start + limit, len(treffer))
        self.calls.append({
            "collection": collection_name,
            "limit": limit,
            "offset": start,
            "filter": scroll_filter,
            "vectors": kw.get("with_vectors"),
            "payload": kw.get("with_payload"),
        })
        return treffer[start:end], (end if end < len(treffer) else None)

    def retrieve(self, collection_name, ids, **kw):
        nach_id = {p.id: p for p in self.points}
        return [nach_id[i] for i in ids if i in nach_id]

    def upsert(self, collection_name, points, wait=True):
        self.writes.append(("upsert", [p.id for p in points]))
        if self.upsert_error is not None:
            raise self.upsert_error

    def set_payload(self, collection_name, payload, points, wait=True):
        self.writes.append(("set_payload", list(points)))

    def delete(self, collection_name, points_selector, wait=True):
        self.writes.append(("delete", list(points_selector)))


def _foto(pid, path, folder=None, event=None, trashed=None):
    """Ein Indexpunkt, wie der Ingest ihn hinterlässt."""
    payload = {
        "file_path": path,
        "folder_name": folder if folder is not None else Path(path).parent.name,
    }
    if event is not None:
        payload["event_name"] = event
    if trashed is not None:
        payload["trashed_at"] = trashed
    return _Point(pid, payload)


def _liste(monkeypatch, points, **kw):
    q = _Q(points)
    monkeypatch.setattr(albums, "client", lambda: q)
    # Ausdrücklich, nicht aus sources.txt geraten: die Attrappen-Pfade liegen
    # unter /mnt/photo, und list_albums prüft die Albumwurzel dagegen.
    monkeypatch.setenv("PHOTOVAULT_PHOTO_ROOT", "/mnt/photo")
    return albums.list_albums(**kw), q


def _nach_name(antwort):
    return {a["folder_name"]: a for a in antwort["albums"]}


def _wirft(fehler):
    def boom(*a, **kw):
        raise fehler

    return boom


# --- Auflisten ------------------------------------------------------------

class TestAuflisten:
    def test_fotos_desselben_ordners_werden_ein_eintrag(self, monkeypatch):
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/GC 07/1.jpg"),
            _foto("b", "/mnt/photo/Fotos/GC 07/2.jpg"),
            _foto("c", "/mnt/photo/Fotos/Abi 08/1.jpg"),
        ])
        assert out["total"] == 2
        assert _nach_name(out)["GC 07"]["photo_count"] == 2
        assert _nach_name(out)["Abi 08"]["photo_count"] == 1

    def test_kameraordner_zaehlt_zum_album_darueber(self, monkeypatch):
        # „GC 07/100MSDCF/x.jpg" ist ein Foto aus dem Album GC 07, nicht aus
        # einem eigenen Album namens 100MSDCF.
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/GC 07/100MSDCF/1.jpg", folder="GC 07"),
            _foto("b", "/mnt/photo/Fotos/GC 07/2.jpg", folder="GC 07"),
        ])
        assert out["total"] == 1
        assert out["albums"][0]["photo_count"] == 2
        assert out["albums"][0]["path"].endswith("GC 07")

    def test_punkt_ohne_pfad_wird_uebersprungen(self, monkeypatch):
        # Ohne Pfad gibt es kein Album -- ein Eintrag mit leerem Namen wäre
        # eine Zeile, die nichts benennt und nicht umbenannt werden kann.
        out, _ = _liste(monkeypatch, [
            _Point("kaputt", {"folder_name": "GC 07"}),
            _foto("a", "/mnt/photo/Fotos/GC 07/1.jpg"),
        ])
        assert out["total"] == 1
        assert out["albums"][0]["photo_count"] == 1

    def test_papierkorb_zaehlt_nicht_mit(self, monkeypatch):
        out, q = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/GC 07/1.jpg"),
            _foto("b", "/mnt/photo/Fotos/GC 07/2.jpg", trashed="2026-08-26T10:00:00Z"),
        ])
        assert out["albums"][0]["photo_count"] == 1
        assert q.calls[0]["filter"] is not None

    def test_album_nur_aus_papierkorbfotos_verschwindet_ganz(self, monkeypatch):
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/Weg/1.jpg", trashed="2026-08-26T10:00:00Z"),
        ])
        assert out == {"total": 0, "albums": []}

    def test_cover_ist_der_erste_punkt_des_albums(self, monkeypatch):
        out, _ = _liste(monkeypatch, [
            _foto(42, "/mnt/photo/Fotos/GC 07/1.jpg"),
            _foto(43, "/mnt/photo/Fotos/GC 07/2.jpg"),
        ])
        assert out["albums"][0]["cover"] == "42"

    def test_seriennamen_werden_gezaehlt_und_entdoppelt(self, monkeypatch):
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/GC 07/1.jpg", event="Games Convention"),
            _foto("b", "/mnt/photo/Fotos/GC 07/2.jpg", event="Games Convention"),
            _foto("c", "/mnt/photo/Fotos/GC 07/3.jpg", event="Abreise"),
            _foto("d", "/mnt/photo/Fotos/GC 07/4.jpg"),
        ])
        eintrag = out["albums"][0]
        assert eintrag["photo_count"] == 4
        assert eintrag["named_count"] == 3
        assert eintrag["event_names"] == ["Games Convention", "Abreise"]

    def test_blaettert_bis_zum_ende(self, monkeypatch):
        # 300 Fotos passen nicht in ein Häppchen von 256. Bräche die Schleife
        # nach dem ersten ab, fehlten 44 Fotos in der Zählung.
        punkte = [_foto(f"p{i}", f"/mnt/photo/Fotos/GC 07/{i}.jpg") for i in range(300)]
        out, q = _liste(monkeypatch, punkte)
        assert out["albums"][0]["photo_count"] == 300
        assert [c["offset"] for c in q.calls] == [0, 256]

    def test_holt_keine_vektoren_und_nur_die_drei_noetigen_felder(self, monkeypatch):
        # 14.593 Fotos mal CLIP-Vektor wären der ganze Index über die Leitung.
        _, q = _liste(monkeypatch, [_foto("a", "/mnt/photo/Fotos/GC 07/1.jpg")])
        assert q.calls[0]["vectors"] is False
        assert q.calls[0]["payload"] == ["file_path", "folder_name", "event_name"]
        assert q.calls[0]["limit"] == 256

    def test_leerer_index(self, monkeypatch):
        out, _ = _liste(monkeypatch, [])
        assert out == {"total": 0, "albums": []}

    def test_sortierung_generische_zuerst_dann_kurz_dann_alphabetisch(self, monkeypatch):
        # Zuerst die Ordner, die nichts benennen -- dort ist am meisten zu tun.
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Handys/Zoo 12/1.jpg"),
            _foto("b", "/mnt/photo/Handys/Sent/1.jpg"),
            _foto("c", "/mnt/photo/Handys/Abi 08/1.jpg"),
            _foto("d", "/mnt/photo/Handys/Download/1.jpg"),
            _foto("e", "/mnt/photo/Handys/GC 07/1.jpg"),
        ])
        assert [a["folder_name"] for a in out["albums"]] == [
            "Sent", "Download", "GC 07", "Abi 08", "Zoo 12",
        ]
        assert [a["generic"] for a in out["albums"]] == [True, True, False, False, False]


class TestGanzenOrdnerUmbenennenErlaubt:
    """`rename_whole` sagt der Oberfläche, ob der Ordnername die Serie sein darf."""

    def test_benannter_ordner_darf_immer(self, monkeypatch):
        out, _ = _liste(monkeypatch, [_foto("a", "/mnt/photo/Fotos/GC 07/1.jpg")])
        assert out["albums"][0]["rename_whole"] is True

    def test_dump_mit_einer_einzigen_serie_darf(self, monkeypatch):
        punkte = [
            _foto(f"p{i}", f"/mnt/photo/Handys/Download/{i}.jpg", event="Klagenfurter Hütte")
            for i in range(10)
        ]
        out, _ = _liste(monkeypatch, punkte)
        assert out["albums"][0]["rename_whole"] is True

    def test_dump_mit_halb_benannten_fotos_darf_nicht(self, monkeypatch):
        punkte = [
            _foto(f"p{i}", f"/mnt/photo/Handys/Download/{i}.jpg",
                  event="Klagenfurter Hütte" if i < 5 else None)
            for i in range(10)
        ]
        out, _ = _liste(monkeypatch, punkte)
        assert out["albums"][0]["named_count"] == 5
        assert out["albums"][0]["rename_whole"] is False

    def test_dump_mit_zwei_serien_darf_nicht(self, monkeypatch):
        punkte = [
            _foto(f"p{i}", f"/mnt/photo/Handys/Download/{i}.jpg",
                  event="Hütte" if i else "Judo")
            for i in range(10)
        ]
        out, _ = _liste(monkeypatch, punkte)
        assert out["albums"][0]["rename_whole"] is False


# --- Grenze und Gesamtzahl ------------------------------------------------

class TestGrenzeUndGesamtzahl:
    """Die Liste hört bei `limit` auf. `total` darf das nicht mitmachen."""

    def _fuenfhundert(self, monkeypatch, **kw):
        punkte = [
            _foto(f"p{i}", f"/mnt/photo/Fotos/Album {i:03d}/1.jpg")
            for i in range(500)
        ]
        return _liste(monkeypatch, punkte, **kw)

    def test_gesamtzahl_ist_die_volle_zahl_nicht_die_seitenlaenge(self, monkeypatch):
        out, _ = self._fuenfhundert(monkeypatch)
        assert len(out["albums"]) == 400          # so viel steht auf der Seite ...
        assert out["total"] == 500                # ... so viel gibt es.

    def test_die_seite_ist_der_anfang_der_sortierung(self, monkeypatch):
        # Nicht irgendwelche 400, sondern die ersten 400 nach der Reihenfolge,
        # die die Route selbst gewählt hat. Sonst ist „mehr anzeigen" beliebig.
        out, _ = self._fuenfhundert(monkeypatch)
        assert out["albums"][0]["folder_name"] == "Album 000"
        assert out["albums"][-1]["folder_name"] == "Album 399"

    def test_kleineres_limit_aendert_die_gesamtzahl_nicht(self, monkeypatch):
        out, _ = self._fuenfhundert(monkeypatch, limit=10)
        assert len(out["albums"]) == 10
        assert out["total"] == 500

    def test_limit_groesser_als_der_bestand_schneidet_nichts_ab(self, monkeypatch):
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/GC 07/1.jpg"),
            _foto("b", "/mnt/photo/Fotos/Abi 08/1.jpg"),
        ], limit=400)
        assert out["total"] == len(out["albums"]) == 2

    def test_auflisten_schreibt_nichts_in_den_index(self, monkeypatch):
        _, q = self._fuenfhundert(monkeypatch)
        assert q.writes == []


# --- Trockenlauf ----------------------------------------------------------

def _album(tmp_path, name="GC 07", dateien=("DSCF0001.JPG",)):
    ordner = tmp_path / name
    ordner.mkdir()
    for datei in dateien:
        (ordner / datei).write_bytes(b"x")
    return ordner


class TestTrockenlauf:
    def test_ist_die_voreinstellung(self):
        # Wer `dry_run` vergisst, verschiebt nichts.
        assert albums.RenameRequest(path="/p/GC 07", new_name="X").dry_run is True

    def test_zaehlt_die_fotos_und_bewegt_nichts(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        anderes = _album(tmp_path, "Abi 08", ["a.jpg"])
        q = _Q([
            _foto("a", str(src / "DSCF0001.JPG"), folder="GC 07"),
            _foto("b", str(anderes / "a.jpg"), folder="Abi 08"),
        ])
        monkeypatch.setattr(albums, "client", lambda: q)
        out = albums.rename(albums.RenameRequest(
            path=str(src), new_name="Games Convention 2007"))
        assert out["dry_run"] is True and out["ok"] is True
        assert out["photos"] == 1               # nur die aus diesem Ordner
        assert out["from_name"] == "GC 07"
        assert out["to_name"] == "Games Convention 2007"
        assert Path(out["to"]) == tmp_path / "Games Convention 2007"
        assert src.is_dir() and not Path(out["to"]).exists()
        assert q.writes == []                   # eine Probe schreibt nirgends hin

    def test_name_wird_vor_der_probe_getrimmt(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        out = albums.rename(albums.RenameRequest(
            path=str(src), new_name="  Games Convention 2007 "))
        assert out["to_name"] == "Games Convention 2007"
        assert Path(out["to"]).name == "Games Convention 2007"

    def test_zaehlt_auch_die_fotos_im_papierkorb(self, tmp_path, monkeypatch):
        # Der Papierkorb ist weich: die Datei liegt noch im Ordner und zieht
        # beim Umbenennen mit. Würde sie hier fehlen, behielte ihr Indexpunkt
        # den alten Pfad -- und wäre nach dem Retten unauffindbar.
        src = _album(tmp_path, dateien=["a.jpg", "b.jpg"])
        q = _Q([
            _foto("a", str(src / "a.jpg"), folder="GC 07"),
            _foto("b", str(src / "b.jpg"), folder="GC 07", trashed="2026-08-26T10:00:00Z"),
        ])
        monkeypatch.setattr(albums, "client", lambda: q)
        out = albums.rename(albums.RenameRequest(path=str(src), new_name="GC 2007"))
        assert out["photos"] == 2

    def test_findet_die_fotos_auch_bei_veraltetem_ordnernamen(self, tmp_path, monkeypatch):
        # Der schnelle Weg filtert auf `folder_name`. Steht dort noch der Name
        # von vor einem abgebrochenen Lauf, muss der Pfad übernehmen.
        src = _album(tmp_path)
        q = _Q([_foto("a", str(src / "DSCF0001.JPG"), folder="Kamera-Dump")])
        monkeypatch.setattr(albums, "client", lambda: q)
        out = albums.rename(albums.RenameRequest(path=str(src), new_name="GC 2007"))
        assert out["photos"] == 1

    def test_ungueltiger_name_ist_400(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(path=str(src), new_name="a/b"))
        assert fehler.value.status_code == 400

    def test_leerer_name_ist_400(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(path=str(src), new_name="   "))
        assert fehler.value.status_code == 400

    def test_unveraenderter_name_ist_400(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(path=str(src), new_name="GC 07"))
        assert fehler.value.status_code == 400

    def test_fehlender_ordner_ist_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(tmp_path / "gibtsnicht"), new_name="X"))
        assert fehler.value.status_code == 404

    def test_vorhandenes_ziel_ist_409(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        (tmp_path / "Games Convention 2007").mkdir()
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(src), new_name="Games Convention 2007"))
        assert fehler.value.status_code == 409
        assert "Ziel existiert schon" in fehler.value.detail


# --- Und dann scheitert der echte Lauf ------------------------------------

class TestDerEchteLaufScheitertNachGruenerProbe:
    def test_ordner_verschwand_zwischen_probe_und_lauf(self, tmp_path, monkeypatch):
        import shutil

        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        probe = albums.RenameRequest(path=str(src), new_name="Games Convention 2007")
        assert albums.rename(probe)["ok"] is True

        shutil.rmtree(src)                      # jemand räumt dazwischen auf
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(src), new_name="Games Convention 2007", dry_run=False))
        assert fehler.value.status_code == 404

    def test_ziel_entstand_zwischen_probe_und_lauf(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        assert albums.rename(albums.RenameRequest(
            path=str(src), new_name="Games Convention 2007"))["ok"] is True

        (tmp_path / "Games Convention 2007").mkdir()
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(src), new_name="Games Convention 2007", dry_run=False))
        assert fehler.value.status_code == 409
        assert src.is_dir()                     # nichts halb verschoben

    def test_index_nachzug_scheitert_ordner_ist_trotzdem_umbenannt(self, tmp_path, monkeypatch):
        """Der unangenehme Fall: die Datei liegt neu, der Punkt liegt alt.

        Das ist kein Serverfehler, sondern ein Ergebnis -- und es muss als
        solches herauskommen, sonst weiß die Oberfläche nicht, dass genau
        dieses Album jetzt ein Nachziehen braucht.
        """
        src = _album(tmp_path)
        foto = src / "DSCF0001.JPG"
        pid = point_id_for(photo_id_for(str(foto)))
        q = _Q(
            [_Point(pid, {"file_path": str(foto), "photo_id": photo_id_for(str(foto)),
                          "folder_name": "GC 07"})],
            upsert_error=RuntimeError("Qdrant nicht erreichbar"),
        )
        monkeypatch.setattr(albums, "client", lambda: q)
        monkeypatch.setattr("ingest.reembed.rebuild_text_vectors", lambda *a, **k: {"updated": 0})

        out = albums.rename(albums.RenameRequest(
            path=str(src), new_name="Games Convention 2007", dry_run=False))
        ziel = tmp_path / "Games Convention 2007"
        assert ziel.is_dir() and not src.exists()
        assert out["ok"] is False
        assert out["migrated"] == 0
        assert out["photos"] == 1
        assert [f["path"] for f in out["failed"]] == [str(foto)]

    def test_volumewechsel_ist_409(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        monkeypatch.setattr(albums, "rename_album", _wirft(OSError("nicht dasselbe Volume")))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(src), new_name="Games Convention 2007", dry_run=False))
        assert fehler.value.status_code == 409
        assert "Volume" in fehler.value.detail

    def test_unerwarteter_fehler_wird_500_und_nicht_verschluckt(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        monkeypatch.setattr(albums, "rename_album", _wirft(RuntimeError("kaputt")))
        with pytest.raises(HTTPException) as fehler:
            albums.rename(albums.RenameRequest(
                path=str(src), new_name="Games Convention 2007", dry_run=False))
        assert fehler.value.status_code == 500
        assert "kaputt" in fehler.value.detail

    def test_der_trockenlauf_ruft_den_echten_lauf_nicht(self, tmp_path, monkeypatch):
        src = _album(tmp_path)
        monkeypatch.setattr(albums, "client", lambda: _Q([]))
        monkeypatch.setattr(albums, "rename_album", _wirft(
            AssertionError("Trockenlauf darf nicht umbenennen")))
        assert albums.rename(albums.RenameRequest(
            path=str(src), new_name="Games Convention 2007"))["dry_run"] is True


# --- Anzeigename und Pfad muessen dasselbe Album meinen --------------------

class TestAlbumpfadUndAnzeigename:
    """Der Name in der Zeile und der Pfad, den „Umbenennen" schickt.

    Die Oberfläche zeigt `folder_name` und schickt beim Umbenennen `path`.
    Gehen die auseinander, benennt der Klick etwas anderes um als das, was
    dasteht -- und `path` ist das, was zählt.
    """

    def test_album_unter_der_wurzel_zeigt_seinen_eigenen_pfad(self, monkeypatch):
        out, _ = _liste(monkeypatch, [_foto("a", "/mnt/photo/Fotos/GC 07/1.jpg")])
        eintrag = out["albums"][0]
        assert eintrag["path"] == "/mnt/photo/Fotos/GC 07"
        assert Path(eintrag["path"]).name == eintrag["folder_name"]

    def test_lose_datei_bleibt_unter_ihrer_scanwurzel(self, monkeypatch):
        # „Fotos" gilt als Sammelordner (folder_parser.RE_CAMERA_DIR), also
        # stieg album_dir ohne Wurzel eine Ebene zu hoch: das „Album" war
        # /mnt/photo, die Freigabe selbst, und ein Umbenennen hätte sie
        # verschoben. Seit list_albums die Wurzel durchreicht, hält es.
        out, _ = _liste(monkeypatch, [
            _foto("a", "/mnt/photo/Fotos/lose.jpg", folder="Fotos"),
        ])
        eintrag = out["albums"][0]
        assert eintrag["folder_name"] == "Fotos"
        assert eintrag["path"] == "/mnt/photo/Fotos"
        assert Path(eintrag["path"]).name == eintrag["folder_name"]
