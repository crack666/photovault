"""Job-Liste für die Fortschrittsseite."""
import time

from ingest.jobs import DEFAULT_VECTOR_SIZE, JobTracker, as_epoch, list_jobs


class _Point:
    def __init__(self, payload):
        self.payload = payload


class _Client:
    def __init__(self, payloads):
        self.payloads = payloads

    def scroll(self, collection_name, limit, with_payload, with_vectors):
        return [_Point(p) for p in self.payloads], None


class TestAsEpoch:
    def test_float_passes_through(self):
        assert as_epoch(1700000000.5) == 1700000000.5

    def test_iso_string_is_converted(self):
        """Ältere Einträge tragen ISO-Strings - die dürfen die Liste nicht sprengen."""
        assert as_epoch("2026-08-02T17:39:46.067414+00:00") > 0

    def test_garbage_becomes_zero(self):
        assert as_epoch("nicht-datum") == 0.0
        assert as_epoch(None) == 0.0


class TestListJobs:
    def test_newest_first(self):
        c = _Client([
            {"job_id": "alt", "started_at": 100.0},
            {"job_id": "neu", "started_at": 900.0},
        ])
        assert [j["job_id"] for j in list_jobs(c)] == ["neu", "alt"]

    def test_mixed_timestamp_formats_do_not_crash(self):
        c = _Client([
            {"job_id": "iso", "started_at": "2026-08-02T17:39:46+00:00"},
            {"job_id": "epoch", "started_at": 1.0},
            {"job_id": "leer"},
        ])
        assert len(list_jobs(c)) == 3

    def test_dead_run_is_marked_stale(self):
        """Ein abgestürzter Prozess bliebe sonst für immer 'running'."""
        c = _Client([{"job_id": "x", "status": "running",
                      "started_at": time.time() - 600, "updated_at": time.time() - 600}])
        assert list_jobs(c)[0]["status"] == "stale"

    def test_fresh_run_stays_running(self):
        c = _Client([{"job_id": "x", "status": "running",
                      "started_at": time.time(), "updated_at": time.time()}])
        assert list_jobs(c)[0]["status"] == "running"

    def test_unreachable_qdrant_returns_empty(self):
        class _Broken:
            def scroll(self, **kw):
                raise RuntimeError("down")

        assert list_jobs(_Broken()) == []


class _Params:
    def __init__(self, size):
        self.size = size


class _Info:
    def __init__(self, size):
        self.config = type("C", (), {"params": type("P", (), {"vectors": _Params(size)})()})()


class _TrackClient:
    def __init__(self, size=4):
        self.size = size
        self.upserts = []

    def get_collection(self, name):
        return _Info(self.size)

    def upsert(self, collection_name, points, wait=False):
        self.upserts.append(points[0])


class TestTrackerVectorSize:
    def test_vector_matches_existing_collection(self):
        """Passt die Dummy-Größe nicht, lehnt Qdrant jeden Upsert ab und die
        Fortschrittsseite bleibt ohne erkennbaren Grund leer."""
        c = _TrackClient(size=4)
        JobTracker(c, kind="ingest", source="/x")
        assert len(c.upserts[0].vector) == 4

    def test_other_size_is_honoured(self):
        c = _TrackClient(size=16)
        JobTracker(c, kind="ingest", source="/x")
        assert len(c.upserts[0].vector) == 16

    def test_payload_carries_the_basics(self):
        c = _TrackClient()
        t = JobTracker(c, kind="ingest", source="/mnt/photo")
        t.update(total=100, processed=25, force=True)
        p = c.upserts[-1].payload
        assert p["kind"] == "ingest" and p["source"] == "/mnt/photo"
        assert p["total"] == 100 and p["processed"] == 25
        assert p["percent"] == 25.0 and p["status"] == "running"

    def test_finish_sets_status_and_end_time(self):
        c = _TrackClient()
        t = JobTracker(c, kind="ingest")
        t.finish("done", processed=7)
        p = c.upserts[-1].payload
        assert p["status"] == "done" and p["finished_at"] is not None

    def test_write_failure_never_stops_the_run(self):
        class _Broken(_TrackClient):
            def upsert(self, **kw):
                raise RuntimeError("qdrant down")

        t = JobTracker(_Broken(), kind="ingest")
        t.update(processed=1, force=True)
        t.finish("done")

    def test_default_size_when_collection_is_new(self):
        class _Fresh(_TrackClient):
            def get_collection(self, name):
                raise RuntimeError("missing")

            def create_collection(self, **kw):
                pass

            def create_payload_index(self, *a, **kw):
                pass

        c = _Fresh()
        JobTracker(c, kind="ingest")
        assert len(c.upserts[0].vector) == DEFAULT_VECTOR_SIZE


class TestDoomedJobs:
    """Was aus der Liste darf und was nicht.

    Die Liste wächst mit jedem Lauf und jedem Abbruch. Entscheidend ist, dass
    ein *laufender* Eintrag nie verschwindet — sonst arbeitet ein Prozess
    weiter, den niemand mehr sieht.
    """

    def _jobs(self):
        # Neueste zuerst, so wie `list_jobs` liefert.
        return [
            {"job_id": "c1", "kind": "caption", "status": "running", "updated_at": 1000},
            {"job_id": "c2", "kind": "caption", "status": "done", "updated_at": 900},
            {"job_id": "c3", "kind": "caption", "status": "stale", "updated_at": 800},
            {"job_id": "c4", "kind": "caption", "status": "done", "updated_at": 700},
            {"job_id": "i1", "kind": "ingest", "status": "succeeded", "updated_at": 600},
            {"job_id": "i2", "kind": "ingest", "status": "done-with-errors", "updated_at": 500},
        ]

    def test_running_is_never_touched(self):
        from api.routes.jobs import doomed_jobs

        doomed = doomed_jobs(self._jobs(), "finished", 0, 0, now=2000)
        assert "c1" not in doomed

    def test_legacy_statuses_count_as_finished(self):
        """Im Bestand stehen `succeeded`, `partial`, `done-with-errors` aus
        älteren Programmständen. Eine Namensliste hätte sie nie erwischt."""
        from api.routes.jobs import doomed_jobs

        doomed = doomed_jobs(self._jobs(), "finished", 0, 0, now=2000)
        assert set(doomed) == {"c2", "c3", "c4", "i1", "i2"}

    def test_aborted_only_takes_the_stale_ones(self):
        from api.routes.jobs import doomed_jobs

        assert doomed_jobs(self._jobs(), "aborted", 0, 0, now=2000) == ["c3"]

    def test_the_newest_per_kind_stay(self):
        """Man will sehen, was zuletzt lief — auch wenn es fertig ist."""
        from api.routes.jobs import doomed_jobs

        doomed = doomed_jobs(self._jobs(), "finished", 0, 2, now=2000)
        # caption: c1 (läuft) und c2 sind die zwei jüngsten, also bleiben c3+c4 übrig.
        assert set(doomed) == {"c3", "c4"}

    def test_young_entries_survive_an_age_limit(self):
        from api.routes.jobs import doomed_jobs

        doomed = doomed_jobs(self._jobs(), "finished", 0.1, 0, now=1000)
        # Alles jünger als 360 s vor `now` bleibt: 900, 800, 700.
        assert set(doomed) == {"i1", "i2"}


class TestBuildArgv:
    """Die Kommandozeile entsteht im Server, nicht beim Aufrufer."""

    def test_plain_call(self):
        from api.routes.jobs import RUNNABLE, build_argv

        argv = build_argv(RUNNABLE["atlas"], dry_run=False, limit=None)
        assert argv[1:] == ["-m", "tools.atlas_build"]

    def test_unknown_flag_is_dropped_not_passed(self):
        """`atlas_build` kennt keinen Trockenlauf. Ihn mitzugeben ließe den
        Start scheitern, mit dem Fehler im Protokoll statt in der Antwort."""
        from api.routes.jobs import RUNNABLE, build_argv

        assert "--dry-run" not in build_argv(RUNNABLE["atlas"], dry_run=True, limit=None)

    def test_known_flags_are_passed(self):
        from api.routes.jobs import RUNNABLE, build_argv

        argv = build_argv(RUNNABLE["reembed"], dry_run=True, limit=500)
        assert "--dry-run" in argv
        assert argv[-2:] == ["--limit", "500"]

    def test_limit_becomes_an_integer_string(self):
        """Nichts vom Aufrufer landet unverändert in der Argumentliste."""
        from api.routes.jobs import RUNNABLE, build_argv

        argv = build_argv(RUNNABLE["reembed"], dry_run=False, limit=7)
        assert argv[-1] == "7"
        assert all(isinstance(a, str) for a in argv)
