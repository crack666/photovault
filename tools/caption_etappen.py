"""Alle offenen Videos beschreiben, in Etappen hinter dem GPU-Tor (#65).

Ein Befehl fuer die Nacht, in WSL aus dem Repo heraus:

    ~/.venvs/photovault/bin/python -m tools.caption_etappen
    ~/.venvs/photovault/bin/python -m tools.caption_etappen --danach-atlas
    ~/.venvs/photovault/bin/python -m tools.caption_etappen --dry-run

Je Etappe (580 Videos): `gpu-tor.ps1 reservieren` -- das prueft das Tor und
weigert sich, wenn es zu ist --, rechnen, `freigeben`. Zwischen den Etappen
bleibt die Karte `--luft` Sekunden frei, damit ein wartender Lauf mit Vorrang
sie bekommt; hat er sie genommen, ist hier Schluss.

Wiederaufnehmbar: die Auswahl ist "Video ohne Beschreibung", jeder Aufruf
macht weiter, wo der letzte aufhoerte. Dateien bleiben unangetastet
(`write_exif=False`, wie `caption_pass --no-exif`). Ins Protokoll gehen nur
Zahlen und Gruende, keine Pfade, Namen oder Saetze; es liegt lokal unter
`~/.local/share/photovault/nachtlauf/`.

Exit: 0 fertig · 75 Tor zu, spaeter denselben Befehl noch einmal · 3 Rest
nicht beschreibbar (Datei unlesbar oder keine Antwort) · 1 Fehler.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ingest import caption_pass
from ingest.gpu_tor import GpuBusy

logger = logging.getLogger("etappen")

FERTIG, FEHLER, REST, TOR_ZU = 0, 1, 3, 75

#: Was aus `ingest.caption_pass` ins Protokoll darf: Vorlagen, die nur Zahlen
#: tragen. Alles andere dort nennt Dateien.
NUR_ZAHLEN = ("%d/%d", "%d Fotos", "Fertig:", "Frist", "Nothing")


class OhneNamen(logging.Filter):
    """Laesst durch, was keine Datei und keinen Menschen nennt."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "etappen":
            return True
        return record.name == "ingest.caption_pass" and str(record.msg).startswith(NUR_ZAHLEN)


class Tor:
    """`gpu-tor.ps1` des Hosts; der Exit-Code zaehlt, die Ausgabe ist der Grund."""

    def __init__(self, skript: str, von: str, run=subprocess.run):
        self.skript, self.von, self._run = skript, von, run

    def _ruf(self, *args: str) -> tuple[int, str]:
        p = self._run(["powershell.exe", "-NoProfile", "-File", self.skript, *args],
                      capture_output=True, timeout=180)
        text = (p.stdout or b"").decode("utf-8", errors="replace").strip()
        return p.returncode, (text.splitlines() or [""])[-1]

    def reservieren(self, grund: str, minuten: int) -> tuple[bool, str]:
        code, text = self._ruf("reservieren", "-Von", self.von, "-Grund", grund,
                               "-Minuten", str(minuten))
        if code not in (0, 1):
            raise RuntimeError(f"gpu-tor.ps1 Exit {code}: {text}")
        return code == 0, text

    def freigeben(self) -> None:
        code, text = self._ruf("freigeben", "-Von", self.von)
        if code:
            logger.warning("Freigabe: Exit %d (%s)", code, text)


def offen(client, kind: str) -> int:
    return client.count(collection_name="photos", exact=True,
                        count_filter=caption_pass.build_filter(missing_only=True, kind=kind)).count


def aufgeben(busy: GpuBusy) -> None:
    """Das Tor ging mitten in der Etappe zu: nicht warten, spaeter weitermachen."""
    raise busy


def vorwaermen(versuche: int = 4) -> None:
    """Das Modell laden, bevor vier Anfragen mit je 180 s Frist auf den Start warten.

    Gemessen 25.11.2026: bei belegtem VRAM dauerte das Laden 188 s, und vier
    von fuenf Videos der ersten Runde liefen in die Frist.
    """
    from ingest.ollama_client import (
        caption_model, gpu_tor_metadata, litellm_headers, litellm_url, llm_mode, post_json,
    )

    if llm_mode() == "ollama":
        return
    for n in range(versuche):
        try:
            post_json(f"{litellm_url()}/v1/chat/completions", {
                "model": caption_model(), "max_tokens": 1,
                "messages": [{"role": "user", "content": "ok"}],
                "metadata": gpu_tor_metadata(),
            }, timeout=600, headers=litellm_headers())
            return
        except GpuBusy:
            raise
        except Exception as e:
            logger.warning("Vorwaermen %d/%d: %s", n + 1, versuche, type(e).__name__)
    raise RuntimeError("Das Modell antwortet nicht")


def laufen(client, tor: Tor, *, kind: str, etappe: int, minuten: int, luft: float,
           hoechstens: int = 0, schlafen=time.sleep, waermen=vorwaermen) -> int:
    nummer = 0
    while True:
        rest = offen(client, kind)
        if not rest:
            logger.info("Nichts offen.")
            return FERTIG
        if hoechstens and nummer >= hoechstens:
            logger.info("%d Etappen gerechnet, %d offen.", nummer, rest)
            return FERTIG
        if nummer:
            schlafen(luft)
        nummer += 1
        frei, grund = tor.reservieren(f"photovault: Beschreibungen Etappe {nummer}", minuten)
        if not frei:
            logger.info("Tor zu, %d offen: %s", rest, grund)
            return TOR_ZU
        logger.info("Etappe %d: %d offen, bis zu %d in dieser", nummer, rest, etappe)
        try:
            waermen()
            got = caption_pass.run(
                client, limit=etappe, kind=kind, missing_only=True, write_exif=False,
                on_busy=aufgeben, deadline=time.time() + (minuten - 10) * 60,
            )
        except GpuBusy as busy:
            logger.info("Tor ging zu, %d offen: %s", offen(client, kind), busy.reason)
            return TOR_ZU
        finally:
            tor.freigeben()
        logger.info("Etappe %d: %d beschrieben, %d unlesbar, %d ohne Antwort",
                    nummer, got["captioned"], got["unreadable"], got["failed"])
        if not got["captioned"]:
            logger.info("Kein Fortschritt mehr, %d bleiben offen.", offen(client, kind))
            return REST


def atlas(tor: Tor, minuten: int = 45, run=subprocess.run) -> int:
    """Karte neu rechnen -- die Themen und Titel leben von den Beschreibungen."""
    frei, grund = tor.reservieren("photovault: Karte neu rechnen", minuten)
    if not frei:
        logger.info("Karte nicht gerechnet, Tor zu: %s", grund)
        return TOR_ZU
    try:
        p = run([sys.executable, "-m", "tools.atlas_build"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=(minuten - 2) * 60)
        logger.info("Karte: Exit %d", p.returncode)
        return FERTIG if p.returncode == 0 else FEHLER
    finally:
        tor.freigeben()


def protokoll(pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    form = logging.Formatter("%(asctime)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in (logging.FileHandler(pfad, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(form)
        h.addFilter(OhneNamen())
        root.addHandler(h)
    os.chmod(pfad, 0o600)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=("video", "photo"), default="video")
    ap.add_argument("--etappe", type=int, default=580, help="Videos je Reservierung")
    ap.add_argument("--hoechstens", type=int, default=0, help="so viele Etappen (0 = alle)")
    ap.add_argument("--minuten", type=int, default=110, help="Laenge der Reservierung je Etappe")
    ap.add_argument("--luft", type=float, default=90, help="Sekunden freie Karte zwischen Etappen")
    ap.add_argument("--von", default="photovault/nachtlauf", help="Kennung am GPU-Tor")
    ap.add_argument("--tor", default=os.environ.get("GPU_TOR_PS1", r"D:\ai\CrackAgent\gpu-tor.ps1"))
    ap.add_argument("--danach-atlas", action="store_true",
                    help="wenn alles beschrieben ist, die Karte neu rechnen")
    ap.add_argument("--dry-run", action="store_true", help="nur zaehlen")
    ap.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    args = ap.parse_args(argv)

    protokoll(Path.home() / ".local/share/photovault/nachtlauf" /
              f"{time.strftime('%Y-%m-%d')}.log")
    from ingest.ollama_client import apply_llm_env

    apply_llm_env()
    # Ohne die Kennung sperrte die eigene Reservierung den eigenen Lauf.
    os.environ["PHOTOVAULT_GPU_TOR_VON"] = args.von
    from qdrant_client import QdrantClient

    client = QdrantClient(url=args.qdrant_url, timeout=60)
    if args.dry_run:
        logger.info("%d offen, %d je Etappe.", offen(client, args.kind), args.etappe)
        return FERTIG
    # SIGTERM wie ein Abbruch: `finally` gibt die Karte frei.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    tor = Tor(args.tor, args.von)
    try:
        code = laufen(client, tor, kind=args.kind, etappe=args.etappe,
                      minuten=args.minuten, luft=args.luft, hoechstens=args.hoechstens)
        if args.danach_atlas and code in (FERTIG, REST):
            return atlas(tor) or code
        return code
    except Exception as e:
        # Nur die Art: eine Meldung kann einen Pfad tragen.
        logger.info("Abgebrochen: %s", type(e).__name__)
        return FEHLER


if __name__ == "__main__":
    sys.exit(main())
