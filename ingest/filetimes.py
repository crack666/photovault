"""Dateizeiten lesen und wiederherstellen, soweit das OS mitmacht.

mtime ist die WhatsApp-Uhr und muss jede In-Place-Änderung überleben.
Erstellzeit unter Windows/SMB bleibt bei rename von allein; nach einem Write
setzen wir sie best-effort zurück. Linux-ctime (Inode-Änderung) ist danach neu
— das ist kein Fehler, den wir wegzaubern.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def snapshot(path: str | Path) -> dict:
    """atime, mtime, optional birth (Windows CreationTime / st_birthtime)."""
    st = os.stat(path)
    out = {"atime": st.st_atime, "mtime": st.st_mtime, "size": st.st_size}
    birth = getattr(st, "st_birthtime", None)
    if birth is None and os.name == "nt":
        birth = st.st_ctime
    if birth is not None:
        out["birth"] = birth
    return out


def restore(path: str | Path, snap: dict) -> dict:
    """Zeiten aus dem Snapshot zurücksetzen. Gibt, was geklappt hat."""
    result = {"mtime": False, "birth": False}
    try:
        os.utime(path, (snap["atime"], snap["mtime"]))
        result["mtime"] = True
    except OSError as e:
        logger.warning("mtime von %s nicht wiederherstellbar: %s", path, e)
    if "birth" in snap:
        result["birth"] = _restore_birth(path, snap["birth"])
    return result


def rename_same_volume(src: Path, dst: Path) -> None:
    """Verzeichnis oder Datei verschieben — nie Copy+Delete.

    Anderes Volume: abbrechen, nicht still kopieren. Copy setzte unter
    Windows ein neues CreationTime und verdoppelt kurz den Platz.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        raise FileExistsError(dst)
    if src.stat().st_dev != dst.parent.stat().st_dev:
        raise OSError(
            f"{src} und {dst.parent} liegen nicht auf demselben Volume — "
            "kein Copy, Abbruch"
        )
    src.rename(dst)


def _restore_birth(path: str | Path, birth: float) -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GENERIC_WRITE = 0x40000000
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        OPEN_EXISTING = 3
        handle = kernel32.CreateFileW(
            str(Path(path)),
            GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == wintypes.HANDLE(-1).value or handle == 0xFFFFFFFF:
            return False

        # FILETIME: 100-ns intervals since 1601-01-01 UTC.
        ft = int(birth * 10_000_000) + 116444736000000000
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        created = FILETIME(ft & 0xFFFFFFFF, ft >> 32)
        ok = kernel32.SetFileTime(handle, ctypes.byref(created), None, None)
        kernel32.CloseHandle(handle)
        return bool(ok)
    except Exception as e:
        logger.debug("Erstellzeit von %s nicht setzbar: %s", path, e)
        return False
