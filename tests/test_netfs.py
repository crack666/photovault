"""Wiederholung bei Netzaussetzern — und ihre Grenzen."""
from __future__ import annotations

import errno

import pytest

from ingest.netfs import is_transient, retry_io


def _oserror(code: int) -> OSError:
    return OSError(code, "kaputt")


class TestClassification:
    """Wiederholt wird nur, was nach Transport aussieht."""

    def test_host_down_is_transient(self):
        # errno 112 -- genau das, was WSL/drvfs bei toter SMB-Sitzung meldet.
        assert is_transient(_oserror(errno.EHOSTDOWN))

    @pytest.mark.parametrize("code", [
        errno.ECONNRESET, errno.ETIMEDOUT, errno.ENETDOWN, errno.ESTALE, errno.EIO,
    ])
    def test_transport_errors(self, code):
        assert is_transient(_oserror(code))

    def test_missing_file_is_final(self):
        """Sonst wartet der Lauf sieben Sekunden auf eine Datei, die es nicht gibt."""
        assert not is_transient(FileNotFoundError(errno.ENOENT, "weg"))

    def test_permission_is_final(self):
        assert not is_transient(PermissionError(errno.EACCES, "nein"))

    def test_broken_image_is_final(self):
        assert not is_transient(ValueError("kein JPEG"))

    def test_enospc_is_final(self):
        assert not is_transient(_oserror(errno.ENOSPC))


class TestRetry:
    def test_returns_immediately_on_success(self):
        calls = []
        assert retry_io(lambda: calls.append(1) or "da", sleep=lambda s: None) == "da"
        assert len(calls) == 1

    def test_recovers_after_a_blip(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise _oserror(errno.EHOSTDOWN)
            return "endlich"

        assert retry_io(flaky, sleep=lambda s: None) == "endlich"
        assert state["n"] == 3

    def test_gives_up_and_reraises(self):
        def dead():
            raise _oserror(errno.EHOSTDOWN)

        with pytest.raises(OSError) as e:
            retry_io(dead, attempts=3, sleep=lambda s: None)
        assert e.value.errno == errno.EHOSTDOWN

    def test_final_errors_are_not_retried(self):
        calls = []

        def missing():
            calls.append(1)
            raise FileNotFoundError(errno.ENOENT, "weg")

        with pytest.raises(FileNotFoundError):
            retry_io(missing, sleep=lambda s: None)
        assert len(calls) == 1, "eine fehlende Datei darf nicht wiederholt werden"

    def test_backoff_doubles(self):
        waits: list[float] = []

        def dead():
            raise _oserror(errno.EHOSTDOWN)

        with pytest.raises(OSError):
            retry_io(dead, attempts=4, base_delay=1.0, sleep=waits.append)
        assert waits == [1.0, 2.0, 4.0]

    def test_total_wait_covers_a_service_restart(self):
        """Sieben Sekunden — genug fuer einen SMB-Neustart, kurz genug fuer 50k Fotos."""
        waits: list[float] = []

        with pytest.raises(OSError):
            retry_io(lambda: (_ for _ in ()).throw(_oserror(errno.EHOSTDOWN)),
                     sleep=waits.append)
        assert 5 <= sum(waits) <= 10
