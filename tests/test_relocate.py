"""Album-Rename ist ein Move, und der Index zieht die Pfad-IDs mit."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ingest.identity import photo_id_for, point_id_for
from ingest.relocate import (
    album_name_ok,
    dest_for_series,
    library_root_for,
    migrate_photo,
    move_photos,
    needs_shelve,
    plan_album_rename,
    replace_prefix,
    rename_album,
    unique_dest,
)


class TestPrefix:
    def test_file_under_the_album(self):
        assert replace_prefix(
            "/mnt/photo/Fotos/GC 07/DSCF0001.JPG",
            "/mnt/photo/Fotos/GC 07",
            "/mnt/photo/Fotos/Games Convention 2007",
        ) == "/mnt/photo/Fotos/Games Convention 2007/DSCF0001.JPG"

    def test_unrelated_path_stays(self):
        assert replace_prefix("/mnt/photo/Fotos/Abi 08/a.jpg",
                              "/mnt/photo/Fotos/GC 07",
                              "/mnt/photo/Fotos/X") == "/mnt/photo/Fotos/Abi 08/a.jpg"


class TestAlbumName:
    def test_rejects_slashes(self):
        with pytest.raises(ValueError):
            album_name_ok("a/b")

    def test_trims(self):
        assert album_name_ok("  Games Convention 2007 ") == "Games Convention 2007"


class TestPlan:
    def test_dry_checks(self, tmp_path):
        src = tmp_path / "GC 07"
        src.mkdir()
        plan = plan_album_rename(src, "Games Convention 2007")
        assert plan["to_name"] == "Games Convention 2007"
        assert not Path(plan["to"]).exists()

    def test_refuses_existing_target(self, tmp_path):
        src = tmp_path / "GC 07"
        src.mkdir()
        (tmp_path / "Games Convention 2007").mkdir()
        with pytest.raises(FileExistsError):
            plan_album_rename(src, "Games Convention 2007")


class _Point:
    def __init__(self, pid, payload, vector=None):
        self.id = pid
        self.payload = payload
        self.vector = vector or {"clip": [0.1]}


class FakeClient:
    def __init__(self, photos=None, faces=None):
        self.photos = dict(photos or {})
        self.faces = dict(faces or {})
        self.deleted = []

    def retrieve(self, collection_name, ids, **kw):
        store = self.photos if collection_name == "photos" else self.faces
        return [store[i] for i in ids if i in store]

    def upsert(self, collection_name, points, wait=True):
        store = self.photos if collection_name == "photos" else self.faces
        for p in points:
            store[p.id] = _Point(p.id, p.payload, p.vector)

    def delete(self, collection_name, points_selector, wait=True):
        store = self.photos if collection_name == "photos" else self.faces
        for i in points_selector:
            store.pop(i, None)
            self.deleted.append(i)

    def set_payload(self, collection_name, payload, points, wait=True):
        store = self.faces if collection_name == "faces" else self.photos
        for i in points:
            store[i].payload.update(payload)

    def scroll(self, collection_name, scroll_filter=None, limit=256, offset=None, **kw):
        store = self.photos if collection_name == "photos" else self.faces
        items = list(store.values())
        if scroll_filter is not None:
            # Tests setzen den Filter; wir liefern alles und lassen den Aufrufer filtern,
            # außer photo_id-Match, den migrate_photo braucht.
            must = getattr(scroll_filter, "must", None) or []
            for cond in must:
                key = getattr(getattr(cond, "key", None), "key", None) or getattr(cond, "key", None)
                match = getattr(cond, "match", None)
                value = getattr(match, "value", None) if match else None
                if key and value is not None:
                    items = [p for p in items if (p.payload or {}).get(key) == value]
        return items, None


def _verschoben(old, new, **kw):
    """Ein Foto mit einem Gesicht, an den neuen Ort gezogen."""
    uid = photo_id_for(old)
    pid = point_id_for(uid)
    client = FakeClient(
        photos={pid: _Point(pid, {"file_path": old, "photo_id": uid, "photo_uid": uid,
                                  "folder_name": "GC 07"}, {"clip": [1.0]})},
        faces={"face-1": _Point("face-1", {"photo_id": uid, "file_path": old})},
    )
    out = migrate_photo(client, old_path=old, new_path=new, **kw)
    return client, out, pid, uid


def test_migrate_photo_laesst_den_punkt_wo_er_ist():
    """Seit die Kennung eingefroren ist, wandert der Punkt nicht mehr.

    Vorher war ein Verschieben eine Operation an vier Stellen: Punkt mit
    allen drei Vektoren holen, unter neuer ID schreiben, Gesichter umhaengen,
    alten loeschen -- nur weil die Kennung `sha256(Pfad)` war. Jetzt aendert
    sich der Pfad und sonst nichts.
    """
    old, new = "/p/GC 07/a.jpg", "/p/Games Convention 2007/a.jpg"
    client, out, pid, uid = _verschoben(old, new, folder_name="Games Convention 2007")

    assert out["old_id"] == out["new_id"] == pid
    assert out["moved_point"] is False
    assert client.deleted == []                      # nichts geloescht
    assert list(client.photos) == [pid]              # kein zweiter Punkt
    assert client.photos[pid].payload["file_path"] == new
    assert client.photos[pid].payload["folder_name"] == "Games Convention 2007"
    # Die Kennung bleibt -- daran haengen Gesichter, Cache und Labels.
    assert client.photos[pid].payload["photo_uid"] == uid


def test_migrate_photo_laesst_die_gesichtskennung_stehen():
    """Der Fremdschluessel bewegt sich nicht -- nur der Pfad zieht mit.

    Die Zuschnitte kommen aus der Datei, deshalb braucht das Gesicht den
    neuen Pfad. Seine Kennung zu aendern waere dagegen genau das, was die
    Umstellung abschafft.
    """
    old, new = "/p/GC 07/a.jpg", "/p/Neu/a.jpg"
    client, out, _pid, uid = _verschoben(old, new, folder_name="Neu")

    assert out["faces"] == 1
    assert client.faces["face-1"].payload["photo_id"] == uid
    assert client.faces["face-1"].payload["file_path"] == new


def test_migrate_photo_findet_ein_schon_einmal_verschobenes_foto():
    """Nach dem ersten Verschieben passt die Kennung nicht mehr zum Pfad.

    Genau deshalb wird der Punkt gesucht statt ausgerechnet -- sonst waere
    das zweite Verschieben desselben Fotos ein "nicht im Index".
    """
    erst, dann, drittens = "/p/A/a.jpg", "/p/B/a.jpg", "/p/C/a.jpg"
    uid = photo_id_for(erst)
    pid = point_id_for(uid)
    client = FakeClient(
        photos={pid: _Point(pid, {"file_path": dann, "photo_id": uid, "photo_uid": uid},
                            {"clip": [1.0]})},
        faces={},
    )
    out = migrate_photo(client, old_path=dann, new_path=drittens, folder_name="C")
    assert out["new_id"] == pid
    assert client.photos[pid].payload["file_path"] == drittens


def test_migrate_photo_meldet_ein_unbekanntes_foto():
    client = FakeClient(photos={}, faces={})
    with pytest.raises(KeyError, match="nicht im Index"):
        migrate_photo(client, old_path="/p/weg.jpg", new_path="/p/neu.jpg",
                      folder_name=None)


def test_rename_album_is_a_directory_rename(tmp_path, monkeypatch):
    src = tmp_path / "GC 07"
    src.mkdir()
    photo = src / "DSCF0001.JPG"
    photo.write_bytes(b"x")
    old = str(photo)
    old_id = point_id_for(photo_id_for(old))
    client = FakeClient(
        photos={old_id: _Point(old_id, {"file_path": old, "photo_id": photo_id_for(old),
                                        "folder_name": "GC 07"}, {"clip": [0.2]})}
    )

    def fake_rebuild(*a, **k):
        return {"updated": 1}

    monkeypatch.setattr("ingest.reembed.rebuild_text_vectors", fake_rebuild)
    dry = rename_album(client, src, "Games Convention 2007", dry_run=True)
    assert dry["dry_run"] is True
    assert src.is_dir()
    out = rename_album(client, src, "Games Convention 2007", dry_run=False)
    dest = tmp_path / "Games Convention 2007" / "DSCF0001.JPG"
    assert dest.is_file()
    assert not src.exists()
    assert out["migrated"] == 1
    assert out["ok"] is True


def test_unique_dest_avoids_overwrite(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"1")
    assert unique_dest(tmp_path, "b.jpg").name == "b.jpg"
    assert unique_dest(tmp_path, "a.jpg").name == "a-2.jpg"


def test_library_root_prefers_sibling_fotos(tmp_path):
    fotos = tmp_path / "Fotos"
    fotos.mkdir()
    dump = tmp_path / "Handys" / "WhatsApp Images"
    dump.mkdir(parents=True)
    src = dump / "IMG-WA0001.jpg"
    src.write_bytes(b"x")
    assert library_root_for([str(src)]) == fotos


def test_dest_for_series_joins_fotos_parent_and_name():
    out = dest_for_series(
        ["/mnt/photo/Fotos/Abistreich 2008", "/mnt/photo/Handys/WhatsApp Images"],
        "Abistreich 2008",
    )
    parent = out["dest_parent"].replace("\\", "/")
    dest = out["dest"].replace("\\", "/")
    assert parent.endswith("/Fotos")
    assert dest.endswith("/Fotos/Abistreich 2008")


def test_dest_for_series_without_name_is_parent_only():
    out = dest_for_series(["/mnt/photo/Fotos/GC 07"], "")
    assert out["dest"] is None
    assert out["dest_parent"].replace("\\", "/").endswith("/Fotos")


def test_dest_for_series_empty_paths():
    assert dest_for_series([], "X") == {"dest_parent": None, "dest": None}


def test_whatsapp_series_needs_shelve_not_folder_rename():
    assert needs_shelve(["WhatsApp Images"], "Klagenfurter Hütte 2024") is True
    assert needs_shelve(["Klagenfurter Hütte 2024"], "Klagenfurter Hütte 2024") is False


def test_move_photos_takes_only_the_series(tmp_path, monkeypatch):
    dump = tmp_path / "WhatsApp Images"
    dump.mkdir()
    keep = dump / "other.jpg"
    keep.write_bytes(b"keep")
    move = dump / "hutte.jpg"
    move.write_bytes(b"go")
    dest = tmp_path / "Fotos" / "Klagenfurter Hütte 2024"
    old = str(move)
    old_id = point_id_for(photo_id_for(old))
    keep_id = point_id_for(photo_id_for(str(keep)))
    client = FakeClient(photos={
        old_id: _Point(old_id, {"file_path": old, "photo_id": photo_id_for(old),
                                "folder_name": "WhatsApp Images"}, {"clip": [0.2]}),
        keep_id: _Point(keep_id, {"file_path": str(keep), "photo_id": photo_id_for(str(keep)),
                                  "folder_name": "WhatsApp Images"}, {"clip": [0.3]}),
    })
    monkeypatch.setattr("ingest.reembed.rebuild_text_vectors", lambda *a, **k: {"updated": 1})
    out = move_photos(client, [old_id], dest, folder_name="Klagenfurter Hütte 2024", dry_run=False)
    assert out["migrated"] == 1
    assert (dest / "hutte.jpg").read_bytes() == b"go"
    assert keep.is_file()
    assert not move.exists()
