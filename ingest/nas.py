"""Netzwerkfreigaben (NAS) im Docker-Verbund: als CIFS-Volumes, nicht als Laufwerk.

Der erste Fremde, der PhotoVault ausprobierte, hatte seine Fotos auf einem
NAS -- und die alte `start.bat` versprach „ein Netzlaufwerk geht auch, wenn
es im Explorer sichtbar ist". Es ging nicht: Docker Desktop reicht gemappte
Netzlaufwerke nicht als Bind-Mount durch. Was geht, gemessen am 19.09.2026
gegen einen Samba-Container unter Docker Desktop (WSL2): ein **Volume vom
Typ cifs**. Die Docker-VM mountet die Freigabe selbst, ueber das Netz, ohne
Laufwerksbuchstaben -- und dasselbe Muster wie bei den Laufwerken traegt:
die Freigabe lesend unter ``/host/nas/<name>``, die gewaehlten Unterordner
am selben Pfad noch einmal beschreibbar (CIFS mountet auch Unterpfade).

Die Definitionen schreibt der Server in ``data/nas-volumes.yml``: er kennt
Zugangsdaten (``settings.json``) und gewaehlte Ordner (``sources.txt``), und
er hat Python -- die Startskripte muessten sonst JSON in Batch parsen. Die
Skripte tragen die Datei nur in ``COMPOSE_FILE`` ein, wenn es sie gibt;
Compose fuehrt beliebig viele Dateien zusammen.

Das Passwort steht in dieser Datei im Klartext (``o=...,password=...``) --
anders kennt der Kernel-Mount es nicht. Die Datei liegt in ``data/``
(ignoriert, 0600 wo das Dateisystem es kann), neben ``settings.json``, das
es ohnehin enthaelt. Wer das nicht will, mountet die Freigabe selbst und
waehlt den Ordner ueber ein Laufwerk.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

from ingest import settings

logger = logging.getLogger(__name__)

#: Wo Freigaben im Container erscheinen. Unter dem Browse-Root (`/host`),
#: damit Ordnerwaehler und Grenze nichts Neues lernen muessen.
NAS_ROOT = "/host/nas"

_NAME = re.compile(r"[^a-z0-9]+")


def fragment_path() -> Path:
    return settings.settings_path().parent / "nas-volumes.yml"


def normalize_unc(raw: str) -> str:
    """``\\\\nas\\fotos\\alben`` oder ``//nas/fotos/alben`` -> ``//nas/fotos/alben``.

    Mindestens Server und Freigabe; alles dahinter ist ein Unterpfad, den
    CIFS beim Mounten versteht.
    """
    s = (raw or "").strip().replace("\\", "/")
    s = re.sub(r"/{2,}", "//", s, count=1) if s.startswith("//") else s
    if s.startswith("smb://"):
        s = "//" + s[len("smb://"):]
    if not s.startswith("//"):
        raise ValueError("Adresse muss mit \\\\server\\freigabe beginnen")
    parts = [p for p in s[2:].split("/") if p]
    if len(parts) < 2:
        raise ValueError("Adresse braucht Server und Freigabe: \\\\server\\freigabe")
    if any(p in (".", "..") for p in parts):
        raise ValueError("Adresse darf kein . oder .. enthalten")
    return "//" + "/".join(parts)


def suggest_name(unc: str) -> str:
    """Ein kurzer, dateisystemtauglicher Name aus Server und Freigabe."""
    parts = normalize_unc(unc)[2:].split("/")
    base = _NAME.sub("-", "-".join(parts[:2]).lower()).strip("-")
    return base or "nas"


def server_of(unc: str) -> str:
    return normalize_unc(unc)[2:].split("/")[0]


def shares() -> list[dict[str, Any]]:
    return list(settings.load().get("nas") or [])


def add_share(unc: str, user: str, password: str, name: str = "") -> dict[str, Any]:
    """Eintragen oder ersetzen (gleicher Name). Gibt den Eintrag zurueck."""
    unc = normalize_unc(unc)
    name = _NAME.sub("-", (name or suggest_name(unc)).lower()).strip("-")
    if not name:
        raise ValueError("Name fehlt")
    if "," in password or "," in user:
        # Der Kernel-Mount trennt Optionen am Komma; ein Komma im Passwort
        # kaeme als zwei Optionen an. Lieber vorher sagen als still scheitern.
        raise ValueError("Benutzer und Passwort duerfen kein Komma enthalten")
    entry = {"name": name, "unc": unc, "user": user.strip(), "password": password}
    rest = [s for s in shares() if s.get("name") != name]
    settings.save({"nas": rest + [entry]})
    write_fragment()
    return entry


def remove_share(name: str) -> bool:
    rest = [s for s in shares() if s.get("name") != name]
    if len(rest) == len(shares()):
        return False
    settings.save({"nas": rest})
    write_fragment()
    return True


def mount_point(name: str) -> str:
    return f"{NAS_ROOT}/{name}"


def mounted(name: str) -> bool:
    """Ob die Freigabe im Container gerade eingebunden ist -- nach dem Neustart."""
    return os.path.ismount(mount_point(name)) or (
        os.path.isdir(mount_point(name)) and bool(os.listdir(mount_point(name)))
    )


def _volume_name(kind: str, device: str, opts: str) -> str:
    """Name aus Inhalt: geaenderte Zugangsdaten ergeben ein neues Volume.

    Docker merkt sich die Optionen eines Volumes beim Anlegen; ein Volume
    mit altem Passwort bliebe sonst still stehen. Ein anderer Name ist ein
    anderes Volume, das alte bleibt ungenutzt liegen (`docker volume prune`).
    """
    h = hashlib.sha256(f"{device}|{opts}".encode("utf-8")).hexdigest()[:8]
    return f"nas_{kind}_{h}"


def chosen_subpaths(share: dict[str, Any], sources_file: str) -> list[str]:
    """Aktive Quellen unterhalb dieser Freigabe, relativ (``Alben/2019``)."""
    root = mount_point(share["name"]).rstrip("/") + "/"
    out: list[str] = []
    try:
        from ingest import sources as src

        book = src.read(sources_file)
    except Exception:
        return out
    for e in book.entries:
        if not e.enabled or e.exclude:
            continue
        p = e.path.rstrip("/")
        if p.startswith(root) and len(p) > len(root):
            rel = p[len(root):].strip("/")
            if rel and ".." not in rel.split("/"):
                out.append(rel)
    return out


def fragment_text(share_list: list[dict[str, Any]], sources_file: str) -> str:
    """Eine eigene Compose-Datei: Volumes und ihre Mounts in `api`.

    Leer (nur Kopf), wenn keine Freigabe eingetragen ist -- die Datei darf
    in COMPOSE_FILE stehen, ohne dass etwas passiert.
    """
    lines = [
        "# Von PhotoVault geschrieben (ingest/nas.py) -- bei jeder Aenderung an",
        "# Freigaben oder Quellen neu. Nicht von Hand aendern.",
        "# Enthaelt Zugangsdaten: nicht weitergeben.",
    ]
    if not share_list:
        return "\n".join(lines + ["services: {}", ""])
    vols: list[str] = []
    mounts: list[str] = []
    for share in share_list:
        unc = normalize_unc(share["unc"])
        server = unc[2:].split("/")[0]
        cred = f"username={share.get('user') or 'guest'},password={share.get('password') or ''}"
        base_opts = f"addr={server},{cred},vers=3.0,iocharset=utf8"
        ro_opts = base_opts + ",ro"
        ro_name = _volume_name("ro", unc, ro_opts)
        vols += [
            f"  {ro_name}:",
            "    driver: local",
            "    driver_opts:",
            "      type: cifs",
            f"      device: \"{unc}\"",
            f"      o: \"{ro_opts}\"",
        ]
        mounts += [
            "      - type: volume",
            f"        source: {ro_name}",
            f"        target: {mount_point(share['name'])}",
        ]
        for rel in chosen_subpaths(share, sources_file):
            device = f"{unc}/{rel}"
            rw_name = _volume_name("rw", device, base_opts)
            vols += [
                f"  {rw_name}:",
                "    driver: local",
                "    driver_opts:",
                "      type: cifs",
                f"      device: \"{device}\"",
                f"      o: \"{base_opts}\"",
            ]
            mounts += [
                "      - type: volume",
                f"        source: {rw_name}",
                f"        target: {mount_point(share['name'])}/{rel}",
            ]
    return "\n".join(lines + ["services:", "  api:", "    volumes:"] + mounts
                     + ["volumes:"] + vols + [""])


def write_fragment(sources_file: str | None = None) -> Path:
    """`data/nas-volumes.yml` neu schreiben -- nach jeder Aenderung an Freigaben
    oder Quellen. Atomar, 0600 wo moeglich."""
    if sources_file is None:
        from api.routes.jobs import sources_path

        sources_file = str(sources_path())
    path = fragment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yml.tmp")
    tmp.write_text(fragment_text(shares(), sources_file), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return path


def redacted(share_list: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for s in (share_list if share_list is not None else shares()):
        out.append({
            "name": s.get("name"), "unc": s.get("unc"), "user": s.get("user") or "",
            "has_password": bool(s.get("password")),
            "mount": mount_point(s.get("name") or ""),
            "mounted": mounted(s.get("name") or ""),
        })
    return out
