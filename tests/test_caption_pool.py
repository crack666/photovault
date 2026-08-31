"""Caption-Worker-Pool: Parallelitaet, Fehlertoleranz, Bildwiederverwendung."""
import base64
import io
import threading

import pytest

from ingest.captioner import Captioner, jpeg_b64, run_captions


class _Rec:
    def __init__(self, path):
        self.file_path = path
        self.caption_de = None


def test_pool_captions_every_record():
    records = [_Rec(f"/p/{i}.jpg") for i in range(10)]
    run_captions(records, lambda r: setattr(r, "caption_de", r.file_path), workers=4)
    assert [r.caption_de for r in records] == [r.file_path for r in records]


def test_one_failure_does_not_kill_the_batch():
    """Dieselbe Lehre wie beim CLIP-Batch: ein defektes Foto kostet nur sich selbst."""
    records = [_Rec(f"/p/{i}.jpg") for i in range(6)]

    def flaky(record):
        if record.file_path == "/p/3.jpg":
            raise RuntimeError("kaputt")
        record.caption_de = "ok"

    run_captions(records, flaky, workers=4)
    done = [r.caption_de for r in records]
    assert done.count("ok") == 5
    assert records[3].caption_de is None


def test_single_worker_takes_the_serial_path():
    records = [_Rec(f"/p/{i}.jpg") for i in range(4)]
    threads = set()
    run_captions(records, lambda r: threads.add(threading.get_ident()), workers=1)
    assert len(threads) == 1


def test_pool_really_overlaps():
    """Vier Anfragen a 50 ms duerfen nicht 200 ms brauchen."""
    import time

    records = [_Rec(f"/p/{i}.jpg") for i in range(4)]
    t0 = time.time()
    run_captions(records, lambda r: time.sleep(0.05), workers=4)
    assert time.time() - t0 < 0.15


def _tiny_jpeg_path(tmp_path):
    from PIL import Image

    p = tmp_path / "x.jpg"
    Image.new("RGB", (2000, 1500), (10, 20, 30)).save(p, format="JPEG")
    return p


def test_jpeg_b64_shrinks_and_accepts_a_loaded_image(tmp_path):
    from PIL import Image

    path = _tiny_jpeg_path(tmp_path)
    from_disk = jpeg_b64(str(path))
    img = Image.open(path)
    from_memory = jpeg_b64(str(path), image=img)

    for encoded in (from_disk, from_memory):
        out = Image.open(io.BytesIO(base64.b64decode(encoded)))
        assert max(out.size) == 1024

    # thumbnail() arbeitet in-place -- das uebergebene Bild darf nicht schrumpfen,
    # es wird danach noch fuer BGR und CLIP gebraucht.
    assert img.size == (2000, 1500)


def test_captioner_uses_the_given_image_and_reads_no_file(monkeypatch):
    sent = {}

    def fake_post(url, payload, timeout=180):
        sent["images"] = payload["messages"][0]["content"], payload["messages"][0]["images"]
        return {"message": {"content": '{"caption_de": "hallo", "scene_tags": []}'}}

    monkeypatch.setattr("ingest.captioner.post_json", fake_post)
    # Der Pfad existiert nicht -- ein Lesezugriff wuerde also auffliegen.
    result = Captioner().caption_structured("/gibt/es/nicht.jpg", {}, image_b64="AAAA")
    assert result["caption_de"] == "hallo"
    assert sent["images"][1] == ["AAAA"]


def test_captioner_falls_back_to_reading_the_file(tmp_path, monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout=180):
        captured["b64"] = payload["messages"][0]["images"][0]
        return {"message": {"content": '{"caption_de": "x", "scene_tags": []}'}}

    monkeypatch.setattr("ingest.captioner.post_json", fake_post)
    path = _tiny_jpeg_path(tmp_path)
    assert Captioner().caption_structured(str(path), {}) is not None
    assert len(captured["b64"]) > 100


class _Cfg:
    ollama_url = None
    skip_caption = False


def test_warmup_loads_caption_model_then_repins_embedder(monkeypatch):
    """Reihenfolge zaehlt: das grosse Modell zuerst, dann den Embedder zurueck.

    Ollama wirft beim Reload andere Modelle aus dem VRAM; der Embedder muss
    danach wieder angeheftet werden, sonst laedt ihn der erste Textvektor neu.
    """
    from ingest.ollama_client import CAPTION_MODEL, CAPTION_NUM_CTX, EMBED_MODEL
    from ingest.pipeline import IngestConfig, IngestPipeline

    calls = []

    def fake_post(url, payload, timeout=180):
        calls.append((url.rsplit("/", 1)[-1], payload))
        return {}

    monkeypatch.setattr("ingest.ollama_client.post_json", fake_post)
    IngestPipeline(IngestConfig(source="/x"))._warm_caption_model()

    assert [c[0] for c in calls] == ["generate", "embed"]
    assert calls[0][1]["model"] == CAPTION_MODEL
    assert calls[0][1]["options"]["num_ctx"] == CAPTION_NUM_CTX
    assert calls[0][1]["keep_alive"] == -1
    assert calls[1][1]["model"] == EMBED_MODEL


def test_warmup_failure_does_not_abort_the_run(monkeypatch):
    from ingest.pipeline import IngestConfig, IngestPipeline

    def boom(url, payload, timeout=180):
        raise OSError("ollama weg")

    monkeypatch.setattr("ingest.ollama_client.post_json", boom)
    # Darf nicht werfen -- der erste Caption-Aufruf laedt das Modell sonst eben.
    IngestPipeline(IngestConfig(source="/x"))._warm_caption_model()


def test_num_ctx_is_sent_by_default(monkeypatch):
    import ingest.captioner as cap

    monkeypatch.setattr(cap, "CAPTION_NUM_CTX", 8192)
    assert cap.caption_options()["num_ctx"] == 8192


def test_num_ctx_zero_lets_the_profile_decide(monkeypatch):
    """Sonst ueberschreibt der Captioner ein eigens angelegtes ctx8k/ctx16k-
    Profil und loest genau den Reload aus, den es vermeiden soll."""
    import ingest.captioner as cap

    monkeypatch.setattr(cap, "CAPTION_NUM_CTX", 0)
    options = cap.caption_options()
    assert "num_ctx" not in options
    assert options["num_predict"] == 200


def test_request_omits_num_ctx_when_disabled(monkeypatch):
    import ingest.captioner as cap

    sent = {}

    def fake_post(url, payload, timeout=180):
        sent.update(payload)
        return {"message": {"content": '{"caption_de": "x", "scene_tags": []}'}}

    monkeypatch.setattr(cap, "CAPTION_NUM_CTX", 0)
    monkeypatch.setattr(cap, "post_json", fake_post)
    cap.Captioner().caption_structured("/nix.jpg", {}, image_b64="AAAA")
    assert "num_ctx" not in sent["options"]


def test_warmup_follows_the_same_rule(monkeypatch):
    import ingest.captioner as cap
    from ingest.pipeline import IngestConfig, IngestPipeline

    calls = []
    monkeypatch.setattr(cap, "CAPTION_NUM_CTX", 0)
    monkeypatch.setattr("ingest.ollama_client.post_json",
                        lambda url, payload, timeout=180: calls.append(payload) or {})
    IngestPipeline(IngestConfig(source="/x"))._warm_caption_model()
    assert "num_ctx" not in calls[0]["options"]
    assert calls[0]["options"]["num_predict"] == 1
