"""Prüfen, ob Persönliches ins Repository gerät.

Ein Foto-Archiv ist voller Namen von Menschen, die nie gefragt wurden, ob sie
in einem öffentlichen Repository auftauchen wollen. Beim Schreiben von Tests
und Dokumentation greift man aber genau nach diesen Namen -- sie sind die
Beispiele, die zur Hand liegen. In dieser Codebasis standen so 176 Stellen in
33 Dateien, bevor jemand hinsah.

Der Trick dieser Prüfung: **sie kennt die echten Namen**, weil sie im
laufenden Index stehen. Eine handgepflegte Liste veraltet mit jedem neuen
Label; Qdrant ist immer aktuell.

    python -m tools.privacy_check              # alle versionierten Dateien
    python -m tools.privacy_check --staged     # nur was im Commit landet
    python -m tools.privacy_check --install    # als pre-commit-Hook einrichten

Ist Qdrant nicht erreichbar, greift `.privacy-denylist` -- eine lokale Kopie
der Namen, die selbst nicht versioniert wird. Ohne beides prüft das Werkzeug
nur die Muster (Schlüssel, private Adressen) und sagt das deutlich.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

DENYLIST = ".privacy-denylist"

#: Muster, die unabhaengig vom Bestand nie ins Repository gehoeren.
PATTERNS: list[tuple[str, str]] = [
    (r"sk-[A-Za-z0-9_\-]{16,}", "API-Schluessel"),
    (r"(?i)\b(password|passwd|passwort)\s*[:=]\s*['\"][^'\"]{3,}", "Passwort im Klartext"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "privater Schluessel"),
    (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "private IP-Adresse"),
    (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "private IP-Adresse"),
    (r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", "E-Mail-Adresse"),
    (r"\bfritz\.box\b", "Router-Hostname"),
]

#: Ausnahmen: Beispieladressen aus RFC 5737 und die Signatur der Commits.
ALLOWED = re.compile(r"192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+|noreply@anthropic\.com")

#: Dateien, in denen Treffer erwartbar und harmlos sind.
SKIP_FILES = {DENYLIST, "tools/privacy_check.py"}
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".pdf", ".zip", ".onnx")


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return [line for line in out.stdout.split("\n") if line.strip()]


def names_from_index(url: str | None = None) -> tuple[set[str], str]:
    """Personennamen aus dem laufenden Index. Zweiter Wert: woher sie kommen."""
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url or os.environ.get("QDRANT_URL", "http://localhost:6333"))
        found, offset = set(), None
        while True:
            batch, offset = client.scroll(
                collection_name="faces", limit=512, offset=offset,
                with_payload=["person_name"], with_vectors=False,
            )
            for point in batch:
                name = (point.payload or {}).get("person_name")
                if name and not name.startswith("_"):
                    found.add(name)
            if offset is None:
                break
        found.discard("Übersprungen")
        found.discard("Ignoriert")
        if found:
            _write_denylist(found)
            return found, "Index"
    except Exception:
        pass
    if os.path.exists(DENYLIST):
        with open(DENYLIST, encoding="utf-8") as fh:
            cached = {line.strip() for line in fh if line.strip() and not line.startswith("#")}
        if cached:
            return cached, f"{DENYLIST} (Index nicht erreichbar)"
    return set(), ""


def _write_denylist(names: set[str]) -> None:
    """Namen lokal ablegen, damit die Pruefung auch ohne Qdrant greift.

    Die Datei gehoert in `.gitignore` -- sie enthaelt genau das, was nicht ins
    Repository soll.
    """
    try:
        with open(DENYLIST, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Von tools/privacy_check.py erzeugt. Nicht versionieren.\n")
            for name in sorted(names):
                fh.write(name + "\n")
    except OSError:
        pass


def terms_from(names: set[str]) -> list[str]:
    """Voller Name und seine Bestandteile, laengste zuerst."""
    terms = set(names)
    for name in names:
        for part in name.split():
            # Kurze Teile ("von", "de") wuerden zu viel Rauschen erzeugen.
            if len(part) > 3:
                terms.add(part)
    return sorted(terms, key=len, reverse=True)


def files_to_check(staged: bool) -> list[str]:
    paths = _git("diff", "--cached", "--name-only", "--diff-filter=ACM") if staged \
        else _git("ls-files")
    return [p for p in paths
            if p not in SKIP_FILES and not p.lower().endswith(SKIP_SUFFIXES)]


def scan(paths: list[str], terms: list[str]) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    compiled = [(re.compile(r"\b" + re.escape(t) + r"\b"), t) for t in terms]
    patterns = [(re.compile(p), why) for p, why in PATTERNS]

    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue
        for no, line in enumerate(lines, 1):
            for rx, term in compiled:
                if rx.search(line):
                    hits.append((path, no, f"Name „{term}“", line.strip()[:90]))
                    break
            for rx, why in patterns:
                m = rx.search(line)
                if m and not ALLOWED.search(m.group(0)):
                    hits.append((path, no, why, line.strip()[:90]))
    return hits


def install_hook() -> int:
    root = (_git("rev-parse", "--git-dir") or [".git"])[0]
    hook = os.path.join(root, "hooks", "pre-commit")
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    body = (
        "#!/bin/sh\n"
        "# Von tools/privacy_check.py eingerichtet.\n"
        "python -m tools.privacy_check --staged || {\n"
        '  echo "Commit abgebrochen. Mit --no-verify erzwingbar, wenn es ein Fehlalarm ist."\n'
        "  exit 1\n"
        "}\n"
    )
    with open(hook, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    try:
        os.chmod(hook, 0o755)
    except OSError:
        pass
    print(f"Hook eingerichtet: {hook}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Nur was im Commit landet")
    parser.add_argument("--install", action="store_true", help="Als pre-commit-Hook einrichten")
    parser.add_argument("--qdrant-url", default=None)
    args = parser.parse_args()

    if args.install:
        return install_hook()

    names, source = names_from_index(args.qdrant_url)
    paths = files_to_check(args.staged)
    if not paths:
        print("Nichts zu prüfen.")
        return 0

    hits = scan(paths, terms_from(names))
    scope = "im Commit" if args.staged else "im Repository"
    if names:
        print(f"{len(names)} Personennamen aus {source}, {len(paths)} Dateien {scope} geprüft.")
    else:
        print(f"Keine Namensliste verfügbar — nur Muster geprüft ({len(paths)} Dateien {scope}).")
        print("Für die Namensprüfung muss Qdrant laufen oder "
              f"{DENYLIST} vorhanden sein.")

    if not hits:
        print("Nichts gefunden.")
        return 0

    print(f"\n{len(hits)} Fundstelle(n):\n")
    for path, no, why, text in hits[:60]:
        print(f"  {path}:{no}  {why}")
        print(f"      {text}")
    if len(hits) > 60:
        print(f"  … und {len(hits) - 60} weitere")
    print("\nBitte durch erfundene Namen ersetzen. Beispiele in tests/ zeigen, wie.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
