"""NAS-Scanner: Fotos finden, Delta-Erkennung."""
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".webp", ".bmp"}
SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store", "albumart.jpg"}


def _is_image(path: Path) -> bool:
    if not path.is_file():
        return False
    name = path.name
    if name.startswith("._") or name.startswith("."):
        return False
    if name.lower() in SKIP_NAMES:
        return False
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    try:
        if path.stat().st_size < 32:
            return False
    except OSError:
        return False
    return True


class NASScanner:
    """Fotos unter einer oder mehreren Wurzeln finden.

    Mehrere Wurzeln statt einer, weil ein Archiv selten als Ganzes gewollt ist:
    neben den Alben liegen Screenshots, Scans und Privates. Ein Ordner wie
    `confidential` darf nicht deshalb im Index landen, weil er zufaellig unter
    demselben Share liegt. Was aufgenommen wird, steht darum explizit da --
    siehe `load_sources()`.
    """

    def __init__(self, source: str | list[str], exclude: list[str] | None = None):
        raw = [source] if isinstance(source, (str, Path)) else list(source)
        self.sources = [Path(str(s).rstrip("/").rstrip("\\")) for s in raw]
        self.exclude = [Path(str(e).rstrip("/").rstrip("\\")) for e in (exclude or [])]
        for src in self.sources:
            logger.info("Scanner source: %s", src)
        for ex in self.exclude:
            logger.info("Scanner exclude: %s", ex)

    @property
    def source(self) -> Path:
        """Erste Wurzel -- fuer Aufrufer, die nur eine erwarten."""
        return self.sources[0]

    def _excluded(self, path: Path) -> bool:
        return any(path == ex or ex in path.parents for ex in self.exclude)

    @staticmethod
    def _in_hidden_dir(path: Path, root: Path) -> bool:
        """Liegt die Datei unter einem Punkt-Verzeichnis?

        `_is_image` filtert Dateinamen mit fuehrendem Punkt, aber nicht
        Verzeichnisse. Androids `Pictures/.thumbnails` haelt allein im
        Handy-Ordner 5119 Miniaturbilder -- 22 % davon waeren als eigene Fotos
        im Index gelandet. Dasselbe gilt fuer `.gs`, `.trashed` und Konsorten.
        """
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        return any(part.startswith(".") for part in rel.parts[:-1])

    def scan(self) -> list[str]:
        missing = [str(s) for s in self.sources if not s.exists()]
        if missing:
            raise FileNotFoundError(f"Source path not found: {', '.join(missing)}")
        seen: set[str] = set()
        files: list[str] = []
        for src in self.sources:
            n = 0
            for p in src.rglob("*"):
                if self._in_hidden_dir(p, src) or self._excluded(p) or not _is_image(p):
                    continue
                sp = str(p)
                if sp in seen:      # ueberlappende Wurzeln doppeln sonst
                    continue
                seen.add(sp)
                files.append(sp)
                n += 1
            logger.info("  %s: %d files", src, n)
        files.sort()
        logger.info("Found %d image files across %d source(s)", len(files), len(self.sources))
        return files


def load_sources(path: str) -> tuple[list[str], list[str]]:
    """Quellenliste aus einer Textdatei lesen.

    Eine Zeile je Verzeichnis, `#` kommentiert aus, ein fuehrendes `-`
    schliesst aus. Bewusst kein YAML: es ist eine Liste von Pfaden, und eine
    Textdatei laesst sich ohne Zusatzpaket lesen, im Editor pflegen und
    versionieren.
    """
    include: list[str] = []
    exclude: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("-"):
                exclude.append(line[1:].strip())
            else:
                include.append(line)
    if not include:
        raise ValueError(
            f"{path} nennt kein einziges Verzeichnis -- nichts zu tun. "
            "Zeilen ohne fuehrendes '#' werden aufgenommen."
        )
    return include, exclude
