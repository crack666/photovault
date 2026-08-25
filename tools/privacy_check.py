"""Prüfen, ob Persönliches ins Repository gerät.

Ein Foto-Archiv ist voller Namen von Menschen, die nie gefragt wurden, ob sie
in einem öffentlichen Repository auftauchen wollen. Beim Schreiben von Tests
und Dokumentation greift man aber genau nach diesen Namen — sie sind die
Beispiele, die zur Hand liegen. In dieser Codebasis standen so 176 Stellen in
33 Dateien, bevor jemand hinsah.

Der Kniff: **die Prüfung kennt die echten Namen**, weil sie im laufenden Index
stehen. Eine handgepflegte Liste veraltet mit jedem neuen Label; Qdrant ist
immer aktuell.

    python -m tools.privacy_check              # alle versionierten Dateien
    python -m tools.privacy_check --staged     # nur was im Commit landet
    python -m tools.privacy_check --message F  # eine Commit-Nachricht
    python -m tools.privacy_check --install    # als Git-Hooks einrichten

**Auch die Commit-Nachricht wird geprüft.** Sie ist Teil dessen, was ein
öffentliches Repository zeigt, und beim Beschreiben einer Änderung greift man
erst recht nach dem konkreten Beispiel — genau so ist hier eine echte Adresse
in eine Commit-Nachricht geraten.

Ist Qdrant nicht erreichbar, greift `.privacy-denylist` — eine lokale Kopie der
Namen, die selbst nicht versioniert wird.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

#: Von der Pruefung erzeugt: Kopie der Namen aus dem Index.
DENYLIST = ".privacy-denylist"

#: Von Hand gepflegt: alles Private, das kein Personenname ist -- Strassen,
#: Arbeitgeber, Vereine, Spitznamen. Die Namensliste kommt aus dem Index und
#: kann so etwas nicht wissen. Wird nie ueberschrieben, nie versioniert.
EXTRA_TERMS = ".privacy-terms"

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

#: Beispieladressen aus RFC 5737 und die Signatur der Commits.
ALLOWED = re.compile(r"192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+|noreply@anthropic\.com")

#: Hier sind Treffer erwartbar und harmlos.
SKIP_FILES = {DENYLIST, EXTRA_TERMS, "tools/privacy_check.py"}
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".ico", ".pdf", ".zip", ".onnx")

#: Welcher Git-Hook womit prueft.
HOOKS = {
    "pre-commit": "--staged",
    # Git uebergibt den Pfad zur Nachrichtendatei als erstes Argument.
    "commit-msg": '--message "$1"',
}


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return [line for line in out.stdout.split("\n") if line.strip()]


def _write_denylist(names: set[str]) -> None:
    """Namen lokal ablegen, damit die Pruefung auch ohne Qdrant greift."""
    try:
        with open(DENYLIST, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Von tools/privacy_check.py erzeugt. Nicht versionieren.\n")
            for name in sorted(names):
                fh.write(name + "\n")
    except OSError:
        pass


def names_from_index(url: str | None = None) -> tuple[set[str], str]:
    """Personennamen aus dem laufenden Index. Zweiter Wert: woher sie kommen."""
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url or os.environ.get("QDRANT_URL", "http://localhost:6333"))
        found: set[str] = set()
        offset = None
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
            cached = {ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")}
        if cached:
            return cached, f"{DENYLIST} (Index nicht erreichbar)"
    return set(), ""


def extra_terms() -> set[str]:
    """Selbst gepflegte Begriffe aus `.privacy-terms`."""
    if not os.path.exists(EXTRA_TERMS):
        return set()
    with open(EXTRA_TERMS, encoding="utf-8") as fh:
        return {ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")}


def terms_from(names: set[str]) -> list[str]:
    """Voller Name und seine Bestandteile, laengste zuerst."""
    terms = set(names)
    for name in names:
        for part in name.split():
            # Kurze Teile ("von", "de") wuerden zu viel Rauschen erzeugen.
            if len(part) > 3:
                terms.add(part)
    # Selbstgepflegte Begriffe bleiben unzerlegt -- sie in Teile zu schneiden
    # erzeugt nur Fehlalarme.
    terms |= extra_terms()
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


def hook_script(arg: str) -> str:
    """Git-Hook, der python3 findet — `python` gibt es unter Linux oft nicht."""
    return (
        "#!/bin/sh\n"
        "# Von tools/privacy_check.py eingerichtet.\n"
        'cd "$(git rev-parse --show-toplevel)" || exit 1\n'
        'if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then\n'
        '  PY="$VIRTUAL_ENV/bin/python"\n'
        "elif [ -x .venv/bin/python ]; then\n"
        "  PY=.venv/bin/python\n"
        'elif [ -x "$HOME/.venvs/photovault/bin/python" ]; then\n'
        '  PY="$HOME/.venvs/photovault/bin/python"\n'
        "elif command -v python3 >/dev/null 2>&1; then\n"
        "  PY=python3\n"
        "elif command -v python >/dev/null 2>&1; then\n"
        "  PY=python\n"
        "else\n"
        '  echo "Privacy-Check: kein Python gefunden (python3 oder python)."\n'
        '  echo "Abgebrochen. Mit --no-verify erzwingbar, wenn es ein Fehlalarm ist."\n'
        "  exit 1\n"
        "fi\n"
        f'"$PY" -m tools.privacy_check {arg} || {{\n'
        '  echo "Abgebrochen. Mit --no-verify erzwingbar, wenn es ein Fehlalarm ist."\n'
        "  exit 1\n"
        "}\n"
    )


def install_hook() -> int:
    root = (_git("rev-parse", "--git-dir") or [".git"])[0]
    for name, arg in HOOKS.items():
        hook = os.path.join(root, "hooks", name)
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(hook_script(arg))
        try:
            os.chmod(hook, 0o755)
        except OSError:
            pass
        print(f"Hook eingerichtet: {hook}")
    return 0


def _report(hits: list[tuple[str, int, str, str]], headline: str, with_path: bool) -> None:
    print(f"{len(hits)} Fundstelle(n) {headline}:")
    print()
    for path, no, why, text in hits[:60]:
        print(f"  {path}:{no}  {why}" if with_path else f"  Zeile {no}: {why}")
        print(f"      {text}")
    if len(hits) > 60:
        print(f"  … und {len(hits) - 60} weitere")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Nur was im Commit landet")
    parser.add_argument("--message", metavar="DATEI",
                        help="Eine Commit-Nachricht pruefen (fuer den commit-msg-Hook)")
    parser.add_argument("--install", action="store_true", help="Als Git-Hooks einrichten")
    parser.add_argument("--qdrant-url", default=None)
    args = parser.parse_args()

    if args.install:
        return install_hook()

    names, source = names_from_index(args.qdrant_url)
    extra = extra_terms()
    terms = terms_from(names)

    if args.message:
        hits = scan([args.message], terms)
        if not hits:
            return 0
        _report(hits, "in der Commit-Nachricht", with_path=False)
        print()
        print("Bitte umformulieren.")
        return 1

    paths = files_to_check(args.staged)
    if not paths:
        print("Nichts zu prüfen.")
        return 0

    hits = scan(paths, terms)
    scope = "im Commit" if args.staged else "im Repository"
    zusatz = f" + {len(extra)} eigene Begriffe" if extra else ""
    if names:
        print(f"{len(names)} Personennamen aus {source}{zusatz}, "
              f"{len(paths)} Dateien {scope} geprüft.")
    else:
        print(f"Keine Namensliste verfügbar — nur Muster{zusatz} geprüft "
              f"({len(paths)} Dateien {scope}).")
        print(f"Für die Namensprüfung muss Qdrant laufen oder {DENYLIST} vorhanden sein.")

    if not hits:
        print("Nichts gefunden.")
        return 0

    print()
    _report(hits, scope, with_path=True)
    print()
    print("Bitte durch erfundene Namen ersetzen. Beispiele in tests/ zeigen, wie.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
