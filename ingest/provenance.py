"""Woher ein Bild stammt — als Kanal, nicht als Gerät.

Zeitliche Nähe allein reicht nicht, um Zusammengehörigkeit zu behaupten. Bei
einer Kamera stimmt die Annahme noch: man nimmt sie zu einer Gelegenheit mit,
und was in derselben Stunde entsteht, gehört zusammen. Ein Handy ist immer
dabei. Dort fallen am selben Nachmittag ein Partyfoto, drei Screenshots und
ein weitergeleitetes Meme an -- zeitlich dicht, inhaltlich ohne jeden Bezug.

Der Kanal trennt das. Bewusst **nicht** das Kameramodell: zwei Handys auf
derselben Feier sollen zusammenfinden, das ist dieselbe Gelegenheit aus zwei
Blickwinkeln. Getrennt gehört, was aus verschiedenen Quellen *stammt* --
selbst aufgenommen, empfangen, gesendet, vom Bildschirm abfotografiert.

Erkannt wird am Pfad und am Dateinamen, weil beides ohne Dateizugriff
verfügbar ist und bei WhatsApp ohnehin die einzige Auskunft bleibt: EXIF ist
dort gestrippt.
"""
from __future__ import annotations

import re

#: Selbst aufgenommen -- der Normalfall und die Vorgabe.
CAMERA = "camera"
#: Von jemandem geschickt.
WHATSAPP = "whatsapp"
#: Selbst verschickt. Oft dieselben Motive wie `camera`, aber verkleinert und
#: ohne EXIF -- als eigener Kanal, damit Duplikate nicht als Serie erscheinen.
WHATSAPP_SENT = "whatsapp-sent"
#: Bildschirmfotos.
SCREENSHOT = "screenshot"
#: Heruntergeladen, weitergeleitet, aus Apps gespeichert.
DOWNLOAD = "download"
#: Gescannt oder abfotografierte Dokumente.
DOCUMENT = "document"

_RE_WA_NAME = re.compile(r"^(IMG|VID)-\d{8}-WA\d+", re.IGNORECASE)
_RE_SHOT_NAME = re.compile(r"^(screenshot|bildschirmfoto|scr_)", re.IGNORECASE)
_RE_SOCIAL = re.compile(r"^(FB_IMG|Snapchat|Instagram|received_|Screenshot)", re.IGNORECASE)

#: Verzeichnisnamen, die den Kanal verraten. Reihenfolge zaehlt: "Sent" muss
#: vor "WhatsApp" greifen, sonst verschwindet die Unterscheidung.
_DIR_RULES: tuple[tuple[str, str], ...] = (
    ("sent", WHATSAPP_SENT),
    ("screenshots", SCREENSHOT),
    ("office lens", DOCUMENT),
    ("scans", DOCUMENT),
    ("documents", DOCUMENT),
    ("whatsapp", WHATSAPP),
    ("download", DOWNLOAD),
    ("downloads", DOWNLOAD),
    ("instagram", DOWNLOAD),
    ("ebay", DOWNLOAD),
    ("chatgpt", DOWNLOAD),
    ("telegram", DOWNLOAD),
)


def channel(file_path: str) -> str:
    """Kanal eines Fotos aus Pfad und Dateiname.

    Der Dateiname schlaegt das Verzeichnis: ein `IMG-20181021-WA0081.jpg`
    bleibt eine WhatsApp-Datei, auch wenn jemand sie in einen Albumordner
    einsortiert hat. Umgekehrt sagt ein Ordner `Screenshots` mehr als ein
    nichtssagender Dateiname.
    """
    if not file_path:
        return CAMERA
    norm = file_path.replace("\\", "/")
    parts = norm.split("/")
    name = parts[-1] if parts else ""
    stem = name.rsplit(".", 1)[0]

    if _RE_SHOT_NAME.match(stem):
        return SCREENSHOT
    if _RE_WA_NAME.match(stem):
        # Ein "Sent"-Ordner ueberstimmt den Dateinamen, weil beide Richtungen
        # gleich heissen.
        return WHATSAPP_SENT if any(p.lower() == "sent" for p in parts[:-1]) else WHATSAPP

    for part in reversed(parts[:-1]):
        low = part.lower()
        for needle, result in _DIR_RULES:
            if low == needle:
                return result

    if _RE_SOCIAL.match(stem):
        return DOWNLOAD
    return CAMERA


#: Kanaele, die in eine Foto-Bibliothek gehoeren. Der Rest ist Beiwerk --
#: nuetzlich zum Wiederfinden, aber nichts, was ein Ereignis begruendet.
WORTH_KEEPING = frozenset({CAMERA, WHATSAPP, WHATSAPP_SENT})
