"""Folder-Parser: _photovault.json + Ordner-/Datei-Namen als Kontext."""
from __future__ import annotations
import json, logging, re
from pathlib import Path

logger = logging.getLogger(__name__)
RE_SEQUENCE = re.compile(r"(IMG|DSCF|DSC|DSCI|PICT|IMGP|PANO|DJI|VID|P)[-_]?(\d{3,6})(?!\d)", re.IGNORECASE)
RE_DATE_IN_NAME = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")
# WhatsApp: IMG-20181021-WA0120.jpg -> Datum 2018-10-21, Sequenz 120
RE_WHATSAPP = re.compile(r"IMG-(\d{4})(\d{2})(\d{2})-WA(\d+)", re.IGNORECASE)
# Kompakt: 20130515_223527.jpg / IMG_20160423_101500.jpg -> 2013-05-15
RE_COMPACT_DATE = re.compile(
    r"(?<!\d)(19\d{2}|20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"
)
RE_YEAR_IN_NAME = re.compile(r"\b(19\d{2}|20\d{2})\b")
KNOWN_LOCATIONS = {"griechenland","greece","italien","italy","spanien","spain","portugal","turkey","thailand","bali","indonesien","deutschland","germany","osterreich","austria","schweiz","switzerland","frankreich","france","niederlande","netherlands","danemark","denmark","norwegen","norway","schweden","sweden","island","iceland","kroatien","croatia","ungarn","hungary","polen","poland","tschechien","czech","kanada","canada","usa","amerika","america","japan","china","suedostasien","southeast asia"}

#: Verzeichnisse, die kein Album benennen, sondern nur die Kamera oder eine
#: Ablagestruktur. "Abi 08/100MSDCF/foto.jpg" gehoert zum Album "Abi 08",
#: nicht zu "100MSDCF" -- sonst geht der Kontext samt Jahreshinweis verloren.
RE_CAMERA_DIR = re.compile(
    r"^(\d{3}[a-z_]{3,8}|dcim|\d{3}|misc|neuer ordner|new folder|"
    r"bilder|fotos|photos|images|pictures|kamera|camera|export|scan\d*|"
    r"unsortiert|sonstiges|div|diverse)$",
    re.IGNORECASE,
)


def album_dir(path: Path, max_up: int = 2, root: Path | None = None) -> Path:
    """Das Verzeichnis, das das Album benennt.

    Steigt ueber Kamera- und Sammelordner hinweg nach oben, aber hoechstens
    `max_up` Ebenen -- und niemals ueber `root`, die Wurzel des Scans. Sonst
    bekommen lose Dateien in "…/photo/Fotos" das Share-Verzeichnis "photo"
    als Album angehaengt.
    """
    current = path.parent
    for _ in range(max_up):
        if not RE_CAMERA_DIR.match(current.name):
            return current
        if root is not None and current == root:
            return current
        parent = current.parent
        if parent == current or not parent.name:
            return current
        if root is not None and root not in parent.parents:
            # Das Album muss *echt unterhalb* der Wurzel liegen. Frueher stand
            # hier zusaetzlich `and parent != root` -- was die Pruefung genau
            # dann aushebelte, wenn der naechste Schritt auf die Wurzel selbst
            # gefuehrt haette. Bei `--source /mnt/photo` bekam
            # "…/photo/Fotos/lose.jpg" dadurch das Album "photo".
            return current
        current = parent
    return current


class FolderParser:
    def __init__(self, root: str | list[str] | None = None):
        """`root` darf mehrere Wurzeln nennen.

        Bei mehreren Quellen zaehlt fuer ein Foto die Wurzel, unter der es
        tatsaechlich liegt -- sonst bekaeme eine Datei aus `/mnt/photo/Urlaub`
        die Wurzel `/mnt/photo/Fotos` vorgehalten und das Album waere falsch.
        """
        if root is None:
            self.roots: list[Path] = []
        elif isinstance(root, (str, Path)):
            self.roots = [Path(root).resolve()]
        else:
            self.roots = [Path(r).resolve() for r in root]

    @property
    def root(self) -> Path | None:
        """Erste Wurzel -- Rueckwaertskompatibilitaet."""
        return self.roots[0] if self.roots else None

    def _root_for(self, path: Path) -> Path | None:
        """Die tiefste Wurzel, unter der `path` liegt.

        Ohne `resolve()`: die Pfade kommen aus `rglob()` ueber genau diese
        Wurzeln, sind also schon absolut. Aufloesen wuerde je Foto einen
        Syscall ueber SMB kosten und Symlinks folgen.
        """
        matches = [r for r in self.roots if r == path or r in path.parents]
        return max(matches, key=lambda r: len(r.parts)) if matches else None

    def parse(self, file_path: str) -> dict:
        path = Path(file_path)
        result = {
            "folder_name": None,
            "folder_type": None,
            "people": [],
            "sequence": None,
            "location_hint": None,
            "location_key": None,
            "date_hint": None,
            "date_hint_source": None,
            "subfolder": None,
        }
        folder_json = self._find_folder_json(path)
        if folder_json:
            result["folder_name"] = folder_json.get("location") or path.parent.name
            result["folder_type"] = folder_json.get("type", "unknown")
            result["people"] = folder_json.get("people", [])
            dr = folder_json.get("date_range", {})
            if dr.get("start"):
                result["date_hint"] = dr["start"][:4]
                result["date_hint_source"] = "folder_json"
            if folder_json.get("location"):
                result["location_hint"] = folder_json["location"]
                result["location_key"] = str(folder_json["location"]).lower().strip()
        album = album_dir(path, root=self._root_for(path))
        folder_name = album.name or path.parent.name
        result["folder_name"] = result["folder_name"] or folder_name
        if album != path.parent:
            # Der Kameraordner bleibt als Untergliederung erhalten.
            result["subfolder"] = path.parent.name
        ym = RE_YEAR_IN_NAME.search(folder_name)
        if ym and not result["date_hint"]:
            result["date_hint"] = ym.group(1)
            result["date_hint_source"] = "folder_name"
        if not result["location_key"]:
            from ingest.locations import detect

            # Auch den Elternordner ansehen: "Urlaub/Griechenland 2015/DCIM".
            hit = detect(folder_name, album.parent.name if album.parent else None)
            if hit:
                label, key = hit
                result["location_hint"] = result["location_hint"] or label
                result["location_key"] = key
        filename = path.stem

        # WhatsApp zuerst: liefert volles Datum *und* Sequenz eindeutig.
        wa = RE_WHATSAPP.search(filename)
        if wa:
            result["sequence"] = int(wa.group(4))
            if not result["date_hint"]:
                result["date_hint"] = f"{wa.group(1)}-{wa.group(2)}-{wa.group(3)}"
                result["date_hint_source"] = "filename"
            return result

        sm = RE_SEQUENCE.search(filename)
        if sm:
            result["sequence"] = int(sm.group(2))
        if not result["date_hint"]:
            dm = RE_DATE_IN_NAME.search(filename)
            if dm:
                result["date_hint"] = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
                result["date_hint_source"] = "filename"
            else:
                cm = RE_COMPACT_DATE.search(filename)
                if cm:
                    result["date_hint"] = f"{cm.group(1)}-{cm.group(2)}-{cm.group(3)}"
                    result["date_hint_source"] = "filename"
        return result

    def _find_folder_json(self, path: Path) -> dict | None:
        """Beidatei suchen -- ohne vorheriges `exists()`.

        `Path.exists()` gibt bei einem abgestandenen SMB-Mount nicht `False`
        zurueck, sondern wirft `OSError: Host is down`. Der Aufruf lag frueher
        vor dem try-Block und riss damit den ganzen Datensatz mit; bei einem
        50k-Lauf ueber Netz reicht dafuer ein kurzer Aussetzer. Direkt oeffnen
        spart ausserdem einen Syscall pro Ebene.
        """
        for parent in [path.parent, path.parent.parent, path.parent.parent.parent]:
            jp = parent / "_photovault.json"
            try:
                with open(jp) as f:
                    return json.load(f)
            except FileNotFoundError:
                continue
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Cannot read _photovault.json in %s: %s", parent, e)
        return None
