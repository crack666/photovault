"""Lesen von einer Netzfreigabe, die kurz weg sein darf.

Das Archiv liegt auf einer SMB-Freigabe. Startet der SMB-Dienst der NAS neu
oder zuckt das Netz, liefert der Mount fuer einige Sekunden `EHOSTDOWN`,
`ECONNRESET` oder `ETIMEDOUT` -- danach ist er von selbst wieder da.

Ohne Wiederholung kostet so ein Aussetzer bei einem Lauf ueber Stunden
dutzende Fotos, die anschliessend als "unlesbar" gelten, obwohl mit ihnen
nichts ist. Die Unterscheidung, auf die es ankommt: eine *fehlende* oder
*kaputte* Datei ist endgueltig und darf nicht wiederholt werden, ein
*weggebrochener Transport* schon.

Beim `drvfs`-Mount unter WSL ist das noetig, weil er schnell scheitert statt
zu warten. Ein nativer `cifs`-Mount mit `hard` blockiert stattdessen bis der
Server zurueck ist -- dann laeuft diese Wiederholung nie an und stoert auch
nicht.
"""
from __future__ import annotations

import errno
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Fehler, die fuer einen unterbrochenen Transport sprechen -- nicht fuer eine
#: kaputte Datei.
TRANSIENT_ERRNOS = frozenset({
    errno.EHOSTDOWN,      # 112 -- SMB-Sitzung weg (WSL/drvfs meldet das)
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETUNREACH,
    errno.ENETRESET,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ETIMEDOUT,
    errno.EAGAIN,
    errno.EBUSY,
    errno.EIO,            # generisch, aber bei Netzpfaden meist Transport
    errno.ESTALE,         # veraltetes Handle nach Reconnect
})

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0


def is_transient(exc: BaseException) -> bool:
    """Spricht der Fehler fuer einen Aussetzer statt fuer eine kaputte Datei?"""
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError,
                        PermissionError)):
        return False
    if isinstance(exc, OSError):
        return exc.errno in TRANSIENT_ERRNOS
    return False


def retry_io(
    fn: Callable[[], T],
    what: str = "",
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """`fn` ausfuehren und bei Transportfehlern wiederholen.

    Wartezeit verdoppelt sich (1 s, 2 s, 4 s) -- zusammen rund sieben Sekunden,
    genug fuer einen Dienstneustart auf der NAS und kurz genug, dass ein
    wirklich toter Pfad den Lauf nicht aufhaelt. Alles andere fliegt sofort
    weiter, damit eine fehlende Datei nicht viermal probiert wird.
    """
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt >= attempts or not is_transient(e):
                raise
            logger.warning(
                "Transient I/O error%s (%s), retry %d/%d in %.0fs",
                f" on {what}" if what else "", e, attempt, attempts - 1, delay,
            )
            sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")
