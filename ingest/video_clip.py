"""Ein Video auf die visuelle Karte: drei Frames, der typischste zaehlt.

Die Karte hat einen Punkt je Foto, und ein Foto hat einen clip-Vektor.
Videos hatten keinen -- der Haupt-Ingest dekodierte nur den Poster-Frame
fuers Vorschaubild, und vor dem CLIP-Schritt stand `kind != "video"`.
1.722 Videos lagen deshalb auf keiner Karte.

Die Wahl, welcher Vektor ein Video vertritt:

*Nicht ein Punkt je Frame.* Ein Video ist auf der Karte ein Ding. Drei
Punkte hiessen, es dreifach zu zaehlen -- im Lasso, in der Groesse einer
Schublade, beim Stapeln.

*Nicht der Mittelwert.* Bei drei aehnlichen Frames ist er harmlos; bei
einem Clip mit zwei verschiedenen Haelften liegt er *zwischen* beiden, an
einem Punkt, an dem kein Bild ist. "Mehr davon" von so einem Video aus
findet Dinge, die keinem seiner Frames aehneln.

*Der Medoid*: der Frame, der dem Mittel am naechsten liegt. Immer ein
echtes Bild. Und er ist per Konstruktion der repraesentativste -- deshalb
wird er auch das Poster. Das bisherige Poster war ein fester Zeitpunkt bei
zehn Prozent, also gern ein Schwarzbild oder ein Uebergang.

Die drei Frames sind dieselben, die Caption und Gesichter schon benutzen
(`sample_offsets`): Anfang, Mitte, Ende; unter drei Sekunden ein
einziger. Vektor, Beschreibung und Gesichter eines Videos beziehen sich
damit auf dieselben Bilder.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: Laengste Kante, auf die ein Frame vor dem Weiterreichen gebracht wird.
#: CLIP sieht 224 px, das groesste Vorschaubild 1280 -- ein 4K-Frame in
#: voller Groesse (25 MB als RGB) laege nur in der Warteschlange herum.
FRAME_MAX_SIDE = 1280


def pick_medoid(embeddings: list[list[float]]) -> int:
    """Index des Vektors, der dem gemeinsamen Mittel am naechsten liegt."""
    if not embeddings:
        raise ValueError("keine Vektoren")
    if len(embeddings) == 1:
        return 0
    E = np.asarray(embeddings, dtype=np.float32)
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    mitte = E.mean(axis=0)
    mitte = mitte / (np.linalg.norm(mitte) + 1e-9)
    return int(np.argmax(E @ mitte))


def shrink(image: Any, max_side: int = FRAME_MAX_SIDE) -> Any:
    """Auf eine handliche Groesse -- RGB, laengste Kante `max_side`."""
    img = image.convert("RGB") if image.mode != "RGB" else image
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side))
    return img


def sample_frames(file_path: str, duration: float | None = None) -> list[tuple[float, Any]]:
    """Die Caption-Frames als (Zeitpunkt, PIL), schon verkleinert.

    Ein Offset, an dem ffmpeg nichts liefert, faellt weg statt das Video
    zu kosten. Der Fall ist real: ein aus einem GIF gewandelter Clip
    meldete 3,24 s Dauer, hatte aber vor 90 % keinen Frame mehr -- ffmpeg
    beendete sich mit 0 und leerer Ausgabe. Erst wenn kein einziger
    Frame kommt, ist die Datei wirklich nicht lesbar.
    """
    from ingest.video import frame_image, probe, sample_offsets

    if duration is None:
        duration = probe(file_path).get("duration")
    out: list[tuple[float, Any]] = []
    letzter: Exception | None = None
    for ss in sample_offsets(duration):
        try:
            out.append((ss, shrink(frame_image(file_path, ss))))
        except Exception as e:  # ein Offset, nicht das Video
            letzter = e
            logger.debug("kein Frame bei %.2fs in %s: %s", ss, file_path, e)
    if not out and letzter is not None:
        raise letzter
    return out


def choose(results: list[dict], frames: list[tuple[float, Any]]) -> dict:
    """Aus den CLIP-Ergebnissen der Frames den Vertreter des Videos machen.

    `results` wie von `SceneTagger.process_images`: je Frame `embedding`
    und `tags`. Zurueck kommt der Eintrag des Medoids plus `poster_ss`
    (sein Zeitpunkt) und `frame` (sein Bild, fuer das Vorschaubild).
    """
    gueltig = [(i, r) for i, r in enumerate(results) if r.get("embedding")]
    if not gueltig:
        return {"embedding": None, "tags": [], "poster_ss": None, "frame": None}
    k = pick_medoid([r["embedding"] for _, r in gueltig])
    i, r = gueltig[k]
    ss, img = frames[i]
    return {"embedding": r["embedding"], "tags": list(r.get("tags") or []),
            "poster_ss": float(ss), "frame": img}


def clip_video(scene: Any, frames: list[tuple[float, Any]]) -> dict:
    """Frames einbetten und den Vertreter waehlen -- der Weg ohne Stapel."""
    if not frames:
        return {"embedding": None, "tags": [], "poster_ss": None, "frame": None}
    results = scene.process_images([img for _, img in frames])
    return choose(results, frames)
