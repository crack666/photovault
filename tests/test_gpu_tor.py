"""GPU-Tor: Ablehnung erkennen, Batch pausieren, nach Freigabe weitermachen.

Kein echtes Tor, kein LiteLLM, kein Ollama: eine Attrappe auf 127.0.0.1
antwortet so, wie der Hook in ai-stack (`config/litellm_gpu_tor.py`) es tut --
HTTP 503, `{"error": {"message": "GPU belegt: …", "type": "gpu_busy"}}` und
`Retry-After`.
"""
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ingest import gpu_tor
from ingest.captioner import Captioner, run_captions
from ingest.gpu_tor import GpuBusy, Pause, call_with_pause, from_http_error

GRUND = "GPU belegt: Spiel im Vordergrund (cs2.exe)"
TEXT = f"{GRUND}. Lokale Modelle (Pool local) sind gesperrt; Anfrage nicht angenommen, " \
       "bitte in 60 s erneut versuchen."


def _body(message=TEXT, kind="gpu_busy"):
    return json.dumps({"error": {"message": message, "type": kind, "param": None, "code": "503"}})


class _Job:
    """Doppel fuer `JobTracker`: merkt sich jedes `update`."""

    def __init__(self):
        self.updates = []

    def update(self, force=False, **fields):
        self.updates.append(fields)

    def paused_reasons(self):
        return [u["paused"] for u in self.updates if "paused" in u]


class TestErkennen:
    def test_ablehnung_des_tors(self):
        busy = from_http_error(503, _body(), {"Retry-After": "60"})
        assert isinstance(busy, GpuBusy)
        assert busy.reason.startswith(GRUND)
        assert busy.retry_after == 60

    def test_grund_auch_hinter_einem_vorsatz(self):
        """Falls LiteLLM der Meldung etwas voranstellt, zaehlt der Text ab „GPU belegt“."""
        busy = from_http_error(503, _body(f"litellm.ProxyException: {TEXT}", kind=""), {})
        assert busy.reason.startswith(GRUND)

    def test_fehlendes_retry_after_wird_zur_vorgabe(self):
        busy = from_http_error(503, _body(), {})
        assert busy.retry_after == gpu_tor.DEFAULT_RETRY_AFTER
        assert from_http_error(503, _body(), {"Retry-After": "Unsinn"}).retry_after == 30

    def test_andere_503_bleibt_ein_fehler(self):
        """Eine 503 ohne „GPU belegt“ ist ein echter Ausfall, kein Wartegrund."""
        assert from_http_error(503, _body("Service Unavailable", kind="internal"), {}) is None
        assert from_http_error(503, "<html>Bad Gateway</html>", {}) is None

    def test_anderer_status_ist_nie_das_tor(self):
        assert from_http_error(500, _body(), {"Retry-After": "60"}) is None
        assert from_http_error(429, _body(), {"Retry-After": "60"}) is None


class _FakeLiteLLM:
    """LiteLLM-Attrappe: solange `closed` > 0, lehnt sie ab wie das Tor."""

    def __init__(self, closed=0, plain_503=False):
        self.closed = closed
        self.plain_503 = plain_503
        self.requests = 0
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 -- Name kommt von http.server
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                with outer.lock:
                    outer.requests += 1
                    shut = outer.closed > 0
                    if shut:
                        outer.closed -= 1
                if outer.plain_503:
                    self._send(503, _body("upstream down", kind="internal"), {})
                elif shut:
                    self._send(503, _body(), {"Retry-After": "45", "x-should-retry": "false"})
                else:
                    answer = {"choices": [{"message": {"content":
                              '{"caption_de": "Ein Foto", "scene_tags": ["test"]}'}}]}
                    self._send(200, json.dumps(answer), {})

            def _send(self, code, body, headers):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def litellm(monkeypatch):
    def start(**kw):
        fake = _FakeLiteLLM(**kw)
        monkeypatch.setenv("LITELLM_URL", fake.url)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        return fake
    return start


class TestUeberHttp:
    def test_post_json_meldet_das_tor_als_gpu_busy(self, litellm):
        from ingest.ollama_client import post_json

        with litellm(closed=1) as fake:
            with pytest.raises(GpuBusy) as info:
                post_json(f"{fake.url}/v1/chat/completions", {"model": "local"})
        assert info.value.reason.startswith(GRUND)
        assert info.value.retry_after == 45

    def test_echte_503_bleibt_ein_http_fehler(self, litellm):
        from ingest.ollama_client import post_json

        with litellm(plain_503=True) as fake:
            with pytest.raises(urllib.error.HTTPError):
                post_json(f"{fake.url}/v1/chat/completions", {"model": "local"})

    def test_batch_pausiert_mit_grund_und_laeuft_nach_freigabe_weiter(self, litellm):
        """Der Kern von photovault#19, Ende zu Ende ueber HTTP.

        Das Tor ist fuer die ersten zwei Anfragen zu. Der Stapel wartet
        `Retry-After`, der Job zeigt den Grund, danach bekommt jedes Foto
        seine Caption und keines zaehlt als Fehler.
        """

        class Rec:
            def __init__(self, i):
                self.file_path = f"/p/{i}.jpg"
                self.caption_de = None

        records = [Rec(i) for i in range(6)]
        job = _Job()
        slept = []

        with litellm(closed=2) as fake:
            captioner = Captioner(model="local")

            def caption_one(rec):
                result = captioner.caption_structured(rec.file_path, {}, image_b64="AAAA")
                rec.caption_de = result and result.get("caption_de")

            run_captions(records, caption_one, workers=1,
                         on_busy=Pause(job, sleep=slept.append))

        assert [r.caption_de for r in records] == ["Ein Foto"] * 6
        assert slept == [45, 45], "je Ablehnung genau eine Pause von Retry-After"
        reasons = job.paused_reasons()
        assert reasons[0].startswith(GRUND), "der Grund steht sichtbar im Job"
        assert reasons[-1] == "", "nach der Freigabe ist die Pause wieder weg"
        assert fake.requests == 6 + 2, "kein Wiederholsturm: je Pause eine Probe"


class TestStapel:
    class Rec:
        def __init__(self, i):
            self.file_path = f"/p/{i}.jpg"
            self.caption_de = None

    def test_ohne_on_busy_geht_die_ablehnung_an_den_aufrufer(self):
        def closed(rec):
            raise GpuBusy(GRUND, 30)

        with pytest.raises(GpuBusy):
            run_captions([self.Rec(i) for i in range(3)], closed, workers=1)

    def test_parallel_schickt_nach_der_ablehnung_nichts_mehr(self):
        """Hoechstens die schon laufenden Anfragen sehen die 503, nicht der ganze Stapel."""
        tor = {"zu": True, "versuche_zu": 0}
        lock = threading.Lock()
        records = [self.Rec(i) for i in range(24)]

        def caption_one(rec):
            with lock:
                if tor["zu"]:
                    tor["versuche_zu"] += 1
                    raise GpuBusy(GRUND, 30)
            rec.caption_de = "ok"

        def freigeben(busy):
            assert busy.reason == GRUND
            tor["zu"] = False

        run_captions(records, caption_one, workers=4, on_busy=freigeben)
        assert all(r.caption_de == "ok" for r in records)
        assert tor["versuche_zu"] <= 4

    def test_andere_fehler_kosten_weiter_nur_das_eine_foto(self):
        records = [self.Rec(i) for i in range(3)]
        pausen = []

        def caption_one(rec):
            if rec.file_path.endswith("1.jpg"):
                raise RuntimeError("kaputt")
            rec.caption_de = "ok"

        run_captions(records, caption_one, workers=1, on_busy=pausen.append)
        assert [r.caption_de for r in records] == ["ok", None, "ok"]
        assert pausen == []


class TestPause:
    def test_lange_pause_meldet_sich_zwischendurch(self):
        """Ohne Lebenszeichen haelt die Jobs-Seite den Lauf nach 120 s fuer tot."""
        job = _Job()
        slept = []
        Pause(job, sleep=slept.append, clock=lambda: 1000.0)(GpuBusy(GRUND, 150))
        assert slept == [60, 60, 30]
        assert job.paused_reasons() == [GRUND, GRUND, GRUND, ""]
        assert job.updates[0]["resume_at"] == 1150

    def test_ohne_job_wird_nur_gewartet(self):
        slept = []
        Pause(None, sleep=slept.append)(GpuBusy(GRUND, 5))
        assert slept == [5]

    def test_call_with_pause_wiederholt_dieselbe_anfrage(self):
        antworten = iter([GpuBusy(GRUND, 7), GpuBusy(GRUND, 7), "fertig"])
        pausen = []

        def anfrage():
            a = next(antworten)
            if isinstance(a, Exception):
                raise a
            return a

        assert call_with_pause(anfrage, lambda b: pausen.append(b.retry_after)) == "fertig"
        assert pausen == [7, 7]


def test_captioner_reicht_die_ablehnung_durch(monkeypatch):
    """Sonst wuerde das Foto still als „keine Caption“ verbucht."""

    def closed(url, payload, timeout=180, headers=None):
        raise GpuBusy(GRUND, 30)

    monkeypatch.setattr("ingest.captioner.post_json", closed)
    with pytest.raises(GpuBusy):
        Captioner().caption_structured("/nix.jpg", {}, image_b64="AAAA")


def test_textfrage_faellt_bei_belegter_karte_auf_den_rueckfall(monkeypatch):
    """`ask_json` (Atlas-Titel) hat immer einen Rueckfall -- dort genuegt `None`."""

    def closed(url, payload, timeout=180, headers=None):
        raise GpuBusy(GRUND, 30)

    monkeypatch.setattr("ingest.captioner.post_json", closed)
    assert Captioner().ask_json("Kontinent?") is None
