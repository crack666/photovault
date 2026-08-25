"""Der abgetrennte Caption-Lauf: Auswahl, Kontext, Schreiben, Fehlertoleranz."""
from __future__ import annotations

import pytest

from ingest import caption_pass
from ingest.caption_pass import Photo, build_filter, payload_context, select_photos


# --------------------------------------------------------------------------
# Testdoubles
# --------------------------------------------------------------------------

class _Point:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class FakeClient:
    """Nur was caption_pass braucht: scroll, set_payload, retrieve, update_vectors."""

    def __init__(self, points: list[_Point]):
        self.points = {p.id: p for p in points}
        self.set_payload_calls: list[dict] = []
        self.updated_vectors: list = []
        self.upsert_calls = 0

    def scroll(self, collection_name, scroll_filter=None, limit=256, offset=None,
               with_payload=True, with_vectors=False):
        items = list(self.points.values())
        start = offset or 0
        page = items[start:start + limit]
        nxt = start + limit if start + limit < len(items) else None
        return page, nxt

    def set_payload(self, collection_name, payload, points, wait=False):
        self.set_payload_calls.append({"payload": payload, "points": points, "wait": wait})
        for pid in points:
            self.points[pid].payload.update(payload)

    def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        return [self.points[i] for i in ids if i in self.points]

    def update_vectors(self, collection_name, points, wait=True):
        self.updated_vectors.extend(points)

    def upsert(self, *a, **kw):  # darf nie passieren -- wuerde Payload ersetzen
        self.upsert_calls += 1


def _payload(**over):
    base = {
        "file_path": "/mnt/photo/Fotos/Abi 2008/IMG_0001.JPG",
        "folder_name": "Abi 2008",
        "date": "2008-06-21",
        "date_source": "exif",
        "sequence_in_folder": 1,
        "face_count": 3,
        "person_names": ["Jonas Meyer"],
        "person_suggestions": ["p-fremd"],
        "folder_people": ["Marco"],
        "scene_tags": ["party"],
        "caption_de": None,
        "caption_locked": False,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Kontext
# --------------------------------------------------------------------------

def test_context_uses_confirmed_names_not_suggestions():
    """Ein Face-Match-Vorschlag darf nicht als Name in die Caption geraten."""
    ctx = payload_context(_payload())
    assert ctx["people_assigned"] == ["Jonas Meyer"]
    assert "p-fremd" not in str(ctx["people_assigned"])


def test_context_carries_the_grounding_fields():
    ctx = payload_context(_payload())
    assert ctx["folder_name"] == "Abi 2008"
    assert ctx["date"] == "2008-06-21"
    assert ctx["filename"] == "IMG_0001.JPG"
    assert ctx["sequence"] == 1
    assert ctx["face_count"] == 3
    assert ctx["clip_tags"] == ["party"]


def test_context_survives_a_sparse_payload():
    ctx = payload_context({"file_path": ""})
    assert ctx["filename"] is None
    assert ctx["people_assigned"] == []
    assert ctx["clip_tags"] == []


# --------------------------------------------------------------------------
# Auswahl
# --------------------------------------------------------------------------

def test_filter_always_excludes_locked_captions():
    """Von Hand geschriebene Captions sind tabu -- auch bei --all."""
    for missing_only in (True, False):
        flt = build_filter(missing_only=missing_only)
        keys = [c.key for c in flt.must_not]
        assert "caption_locked" in keys


def test_exif_only_keeps_locked_captions_in_the_file():
    """Der Satz von Hand gehört in die Datei, das Modell darf ihn nicht ersetzen."""
    flt = build_filter(missing_only=False, has_caption=True)
    keys = [getattr(c, "key", None) for c in (flt.must_not or [])]
    assert "caption_locked" not in keys


def test_missing_only_excludes_existing_llm_captions():
    keys = [c.key for c in build_filter(missing_only=True).must_not]
    assert "caption_source" in keys
    keys_all = [c.key for c in build_filter(missing_only=False).must_not]
    assert "caption_source" not in keys_all


def test_person_and_album_become_must_conditions():
    flt = build_filter(person="Annika Wolf", album="Abi")
    keys = [c.key for c in flt.must]
    assert keys == ["person_names", "folder_name"]


def test_select_returns_sorted_paths():
    pts = [_Point(i, _payload(file_path=f"/p/{c}.jpg")) for i, c in enumerate("dbca")]
    found = select_photos(FakeClient(pts))
    assert [p.file_path for p in found] == ["/p/a.jpg", "/p/b.jpg", "/p/c.jpg", "/p/d.jpg"]


def test_limit_cuts_in_scroll_order_and_is_stable():
    """Alphabetisch zu truncieren wuerde bei --limit immer dasselbe Album treffen.

    Die Auswahl folgt deshalb der Scroll-Reihenfolge; nur die Ausgabe ist
    sortiert. Zwei Laeufe muessen dieselbe Menge liefern.
    """
    pts = [_Point(i, _payload(file_path=f"/p/{c}.jpg")) for i, c in enumerate("dbca")]
    first = [p.file_path for p in select_photos(FakeClient(pts), limit=3)]
    second = [p.file_path for p in select_photos(FakeClient(pts), limit=3)]
    assert len(first) == 3
    assert first == sorted(first)
    assert first == second


def test_select_skips_points_without_a_file_path():
    client = FakeClient([_Point(1, _payload()), _Point(2, _payload(file_path=None))])
    assert len(select_photos(client)) == 1


# --------------------------------------------------------------------------
# Durchlauf
# --------------------------------------------------------------------------

@pytest.fixture
def wired(monkeypatch):
    """Bilder und Ollama ersetzen; der Rest laeuft echt."""
    monkeypatch.setattr(caption_pass, "jpeg_b64",
                        lambda path, image=None, max_side=1024: f"b64:{path}")

    class FakeCaptioner:
        def __init__(self, url=None, num_ctx=None):
            self.url = url
            self.num_ctx = num_ctx
            self.seen: list[tuple[str, dict, str]] = []

        def caption_structured(self, file_path, context=None, image_b64=None):
            self.seen.append((file_path, context, image_b64))
            return {"caption_de": f"Beschreibung zu {file_path}", "scene_tags": ["fest"]}

    class FakeEmbedder:
        def __init__(self, url=None):
            pass

        def embed_batch(self, docs):
            return [[0.1] * 4 for _ in docs]

    made: dict = {}

    def make_captioner(url=None, num_ctx=None):
        made["captioner"] = FakeCaptioner(url, num_ctx)
        return made["captioner"]

    monkeypatch.setattr(caption_pass, "Captioner", make_captioner)
    monkeypatch.setattr(caption_pass, "TextEmbedder", FakeEmbedder)
    monkeypatch.setattr(caption_pass, "_write_file_captions", lambda *a, **k: None)
    monkeypatch.setattr("ingest.reembed.PointVectors", None, raising=False)
    return made


def test_run_captions_writes_payload_and_reembeds(wired, monkeypatch):
    client = FakeClient([_Point(i, _payload(file_path=f"/p/{i}.jpg")) for i in range(3)])
    seen_reembed = {}

    def fake_reembed(cl, ids, collection="photos", embedder=None, **kw):
        seen_reembed["ids"] = list(ids)
        return {"updated": len(ids)}

    monkeypatch.setattr(caption_pass, "rebuild_text_vectors", fake_reembed)
    stats = caption_pass.run(client, workers=2, io_workers=2, track=False)

    assert stats["selected"] == 3
    assert stats["captioned"] == 3
    assert stats["reembedded"] == 3
    assert client.upsert_calls == 0, "upsert wuerde Personen und Vektoren mitloeschen"
    for call in client.set_payload_calls:
        assert call["payload"]["caption_source"] == "llm"
        assert call["payload"]["caption_de"].startswith("Beschreibung zu ")
        assert call["wait"] is True, "sonst liest das Re-Embedding die alte Caption"
    # Erst schreiben, dann einbetten.
    assert set(seen_reembed["ids"]) == {0, 1, 2}


def test_run_writes_exif_after_the_index(wired, monkeypatch):
    """Index zuerst — eine fehlschlagende Datei darf den Satz in Qdrant nicht kosten."""
    client = FakeClient([_Point(0, _payload(file_path="/p/0.jpg"))])
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda *a, **kw: {"updated": 1})
    seen = []

    def fake_exif(photos, stats):
        seen.append([p.caption_de for p in photos])
        stats["exif_written"] = stats.get("exif_written", 0) + len(photos)

    monkeypatch.setattr(caption_pass, "_write_file_captions", fake_exif)
    stats = caption_pass.run(client, workers=1, io_workers=1, track=False)
    assert seen and seen[0][0].startswith("Beschreibung zu ")
    assert stats["exif_written"] == 1
    assert client.set_payload_calls, "Index kommt vor der Datei"


def test_llm_tags_are_merged_not_replaced(wired, monkeypatch):
    client = FakeClient([_Point(0, _payload(scene_tags=["party", "nacht"]))])
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda *a, **kw: {"updated": 1})
    caption_pass.run(client, workers=1, io_workers=1, track=False)
    tags = client.set_payload_calls[0]["payload"]["scene_tags"]
    assert tags == ["party", "nacht", "fest"]


def test_unreadable_photo_is_counted_and_skipped(wired, monkeypatch):
    def flaky(path, image=None, max_side=1024):
        if path.endswith("1.jpg"):
            raise OSError("SMB weg")
        return f"b64:{path}"

    monkeypatch.setattr(caption_pass, "jpeg_b64", flaky)
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda cl, ids, **kw: {"updated": len(ids)})
    client = FakeClient([_Point(i, _payload(file_path=f"/p/{i}.jpg")) for i in range(3)])
    stats = caption_pass.run(client, workers=2, io_workers=2, track=False)

    assert stats["unreadable"] == 1
    assert stats["captioned"] == 2
    assert len(client.set_payload_calls) == 2


def test_a_failing_caption_costs_only_that_photo(wired, monkeypatch):
    class Grumpy:
        def __init__(self, url=None, num_ctx=None):
            pass

        def caption_structured(self, file_path, context=None, image_b64=None):
            if file_path.endswith("1.jpg"):
                raise RuntimeError("Ollama sagt nein")
            return {"caption_de": "ok", "scene_tags": []}

    monkeypatch.setattr(caption_pass, "Captioner", Grumpy)
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda cl, ids, **kw: {"updated": len(ids)})
    client = FakeClient([_Point(i, _payload(file_path=f"/p/{i}.jpg")) for i in range(4)])
    stats = caption_pass.run(client, workers=2, io_workers=2, track=False)

    assert stats["captioned"] == 3
    assert stats["failed"] == 1


def test_dry_run_touches_nothing(wired):
    client = FakeClient([_Point(0, _payload())])
    stats = caption_pass.run(client, dry_run=True, track=False)
    assert stats["selected"] == 1
    assert stats["captioned"] == 0
    assert client.set_payload_calls == []


def test_empty_selection_is_not_an_error(wired):
    stats = caption_pass.run(FakeClient([]), track=False)
    assert stats["selected"] == 0
    assert stats["captioned"] == 0
    assert stats["exif_written"] == 0


def test_caption_display_is_written_for_the_gallery(wired, monkeypatch):
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda *a, **kw: {"updated": 1})
    client = FakeClient([_Point(0, _payload())])
    caption_pass.run(client, workers=1, io_workers=1, track=False)
    display = client.set_payload_calls[0]["payload"]["caption_display"]
    assert display and "2008" in display


def test_job_tracking_failure_does_not_stop_the_run(wired, monkeypatch):
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda *a, **kw: {"updated": 1})

    def boom(*a, **kw):
        raise RuntimeError("Qdrant-Jobs kaputt")

    monkeypatch.setattr("ingest.jobs.JobTracker", boom)
    client = FakeClient([_Point(0, _payload())])
    stats = caption_pass.run(client, workers=1, io_workers=1, track=True)
    assert stats["captioned"] == 1


def test_pass_sends_no_num_ctx_by_default(wired, monkeypatch):
    """Der Sinn des abgetrennten Laufs: das geladene Modell in Ruhe lassen.

    Ein mitgeschicktes `num_ctx` wuerde Ollama zum Reload zwingen (11-112 s)
    und dabei andere Modelle aus dem VRAM werfen.
    """
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors", lambda *a, **kw: {"updated": 1})
    caption_pass.run(FakeClient([_Point(0, _payload())]), workers=1, io_workers=1, track=False)
    assert wired["captioner"].num_ctx == 0


def test_num_ctx_can_be_forced_when_needed(wired, monkeypatch):
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors", lambda *a, **kw: {"updated": 1})
    caption_pass.run(FakeClient([_Point(0, _payload())]),
                     workers=1, io_workers=1, track=False, num_ctx=8192)
    assert wired["captioner"].num_ctx == 8192


def test_caption_options_respect_the_caller():
    from ingest.captioner import caption_options

    assert "num_ctx" not in caption_options(0)
    assert caption_options(16384)["num_ctx"] == 16384
    assert "num_ctx" in caption_options()  # ohne Angabe gilt CAPTION_NUM_CTX


# --------------------------------------------------------------------------
# Tags und Namensregeln
# --------------------------------------------------------------------------

def test_merge_tags_folds_umlauts():
    """CLIP liefert `getraenke`, das LLM `getränke` — das ist ein Tag, nicht zwei."""
    from ingest.captioner import merge_tags

    assert merge_tags(["getraenke"], ["getränke"]) == ["getraenke"]
    assert merge_tags(["gruene-waende"], ["grüne wände"]) == ["gruene-waende"]


def test_merge_tags_keeps_genuinely_new_ones_in_order():
    from ingest.captioner import merge_tags

    assert merge_tags(["party"], ["bier", "party", "lächeln"]) == ["party", "bier", "lächeln"]


def test_merge_tags_drops_empties():
    from ingest.captioner import merge_tags

    assert merge_tags([], ["", "  ", "fest"]) == ["fest"]


def test_prompt_forbids_dodging_a_known_name():
    """Mit zwei Gesichtern und einem Namen wich das Modell auf „zwei Personen“ aus.

    Die Regel für den Teilfall hatte im Prompt gefehlt.
    """
    from ingest.captioner import build_caption_prompt

    prompt = build_caption_prompt({"face_count": 2, "people_assigned": ["Tobias Krueger"]})
    assert "MUSS mindestens einer davon in caption_de vorkommen" in prompt
    assert "mehr Gesichter erkannt als Namen bekannt" in prompt
    assert "Tobias Krueger" in prompt


def test_prompt_asks_for_searchable_objects_not_clip_buckets():
    """Freitext 'Bier' soll ein Bierglas treffen, nicht nur das CLIP-Tag getraenke."""
    from ingest.captioner import build_caption_prompt

    prompt = build_caption_prompt({}).lower()
    assert "bierglas" in prompt
    assert "konkrete" in prompt
    assert "getraenke" in prompt
    assert "bier/wein" in prompt or "bier/wein/cola" in prompt


def test_merge_tags_is_capped():
    """Ein wiederholbarer Lauf darf die Tag-Liste nicht unbegrenzt wachsen lassen."""
    from ingest.captioner import MAX_TAGS, merge_tags

    grown = merge_tags([], [f"tag{i}" for i in range(50)])
    assert len(grown) == MAX_TAGS


def test_repeated_merges_converge():
    from ingest.captioner import merge_tags

    tags = ["party", "getraenke"]
    for _ in range(10):
        tags = merge_tags(tags, ["getränke", "bier", "party"])
    assert tags == ["party", "getraenke", "bier"]


def test_a_blip_does_not_mark_photos_unreadable(wired, monkeypatch):
    """Ein SMB-Neustart mitten im Lauf darf keine Fotos verlieren.

    Ohne Wiederholung galten sie als "unlesbar" und bekamen keine Caption.
    """
    import errno

    state = {"n": 0}

    def flaky(path, image=None, max_side=1024):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError(errno.EHOSTDOWN, "Host is down")
        return f"b64:{path}"

    monkeypatch.setattr(caption_pass, "jpeg_b64", flaky)
    monkeypatch.setattr("ingest.netfs.time.sleep", lambda s: None)
    monkeypatch.setattr(caption_pass, "rebuild_text_vectors",
                        lambda cl, ids, **kw: {"updated": len(ids)})
    client = FakeClient([_Point(i, _payload(file_path=f"/p/{i}.jpg")) for i in range(3)])
    stats = caption_pass.run(client, workers=1, io_workers=1, track=False)

    assert stats["unreadable"] == 0
    assert stats["captioned"] == 3
