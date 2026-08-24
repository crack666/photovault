"""Deutsche Captions via Qwen 3.8 Vision (Ollama), thinking aus.

Das Bild allein reicht nicht: Datum, Ordner, Sequenz, Face-Count und
bekannte Namen werden als Kontext in den Prompt gelegt (Anfuettern).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any

from ingest.ollama_client import CAPTION_MODEL, CAPTION_NUM_CTX, ollama_url, post_json

logger = logging.getLogger(__name__)

JSON_SCHEMA = (
    '{"caption_de": str, "scene_tags": [str], '
    '"location_guess": str|null, "person_count": int, "mood": str}'
)


#: Obergrenze fuer `scene_tags`. CLIP liefert ~4, das LLM bis 8 -- 16 laesst
#: Luft und deckelt zugleich das Wachstum bei wiederholten Caption-Laeufen.
MAX_TAGS = 16


def merge_tags(existing: list[str], extra: list[str], limit: int = MAX_TAGS) -> list[str]:
    """CLIP-Tags und LLM-Tags zusammenfuehren, ohne Dubletten.

    Naiv verglichen stehen hinterher `getraenke` (CLIP-Label, ASCII) und
    `getränke` (LLM, echtes Deutsch) nebeneinander im Payload und blaehen
    Filterlisten auf. Verglichen wird deshalb umlautgefaltet; behalten wird die
    Schreibweise, die zuerst da war.

    Das Limit deckelt das Wachstum: der Caption-Lauf ist auf Wiederholung
    ausgelegt, und ohne Grenze legt jeder Durchlauf ein paar neue Formulierungen
    obendrauf.
    """
    def fold(tag: str) -> str:
        t = tag.strip().lower()
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            t = t.replace(a, b)
        return t.replace("-", " ").replace("_", " ")

    out = list(existing)
    seen = {fold(t) for t in out}
    for tag in extra:
        tag = (tag or "").strip()
        key = fold(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


def run_captions(items: list, caption_one, workers: int) -> None:
    """Captions eines Stapels holen, hoechstens `workers` gleichzeitig.

    Ollama serialisiert die Anfragen ohnehin -- der Gewinn kommt allein daraus,
    dass HTTP-Weg und Serverrechnung sich ueberlappen. Ueber etwa vier
    gleichzeitige Anfragen waechst nur die Warteschlange (docs/performance.md).

    Ein Fehler darf nur das eine Foto kosten, nicht den Stapel -- dieselbe
    Lehre wie beim CLIP-Batch, den frueher ein einzelnes defektes Bild
    mitgerissen hat.
    """

    def guarded(item):
        try:
            caption_one(item)
        except Exception as e:
            logger.warning("Captioning failed for %s: %s",
                           getattr(item, "file_path", item), e)

    if workers <= 1 or len(items) == 1:
        for item in items:
            guarded(item)
        return

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        list(pool.map(guarded, items))


def caption_options(num_ctx: int | None = None) -> dict[str, Any]:
    """Ollama-Optionen fuer eine Caption-Anfrage.

    ``num_ctx`` faellt weg, wenn der Wert 0 ist -- dann gilt die Kontextgroesse
    des geladenen Modells, und es gibt **keinen Reload**. Ohne Angabe greift
    ``CAPTION_NUM_CTX`` (Standard 8192).

    Wer welchen Wert braucht: die Pipeline erzwingt 8192, weil sie insightface
    und CLIP auf derselben Karte haelt und den Platz braucht. Der abgetrennte
    Caption-Lauf laesst den Kontext in Ruhe -- er hat keine GPU-Modelle daneben.
    """
    value = CAPTION_NUM_CTX if num_ctx is None else num_ctx
    options: dict[str, Any] = {"temperature": 0.2, "num_predict": 200}
    if value:
        options["num_ctx"] = value
    return options


def build_caption_prompt(context: dict[str, Any] | None = None) -> str:
    lines = [
        "Analysiere das Foto. Der KONTEXT stammt aus Datei/Ordner/EXIF/Face-ID — "
        "nicht aus dem Bild. Nutze ihn in caption_de (Ort, Jahr, Anlass), "
        "aber erfinde keine Namen und keinen Ort, die nicht im Kontext stehen.",
        "",
        "Regeln zu Namen — streng:",
        "- Einen Namen NUR dann nennen, wenn er unter 'Zugeordnet (Face-Match)' steht.",
        "- Steht dort nichts, schreibe 'eine Person' / 'zwei Personen' / 'eine Gruppe'.",
        "- Stehen dort Namen, MUSS mindestens einer davon in caption_de vorkommen. "
        "Nicht auf 'zwei Personen' ausweichen, wenn ein Name bekannt ist.",
        "- Sind mehr Gesichter erkannt als Namen bekannt: die bekannten benennen, "
        "den Rest generisch dazu ('Tobias Krueger und eine weitere Person').",
        "- Der Ordnername nennt oft den Anlass ('Junggesellenabschied'). Diese Person "
        "muss NICHT im Bild sein. Den Namen dann fuer den Anlass verwenden "
        "('bei einem JGA'), aber niemand im Bild bekommt ihn zugewiesen.",
        "- Nie schreiben, WER das Foto aufgenommen hat — das steht in keinem Metadatum.",
        "",
        "Weitere Regeln:",
        "- Datum/Ordner in den Satz einbauen, wenn vorhanden (z.B. Griechenland 2015).",
        "- Nur beschreiben, was wirklich im Bild ist. Die CLIP-Tags sind schwach und "
        "oft falsch — ignoriere sie, wenn sie dem Bild widersprechen.",
        f"- Antworte NUR mit JSON: {JSON_SCHEMA}",
        "- caption_de: 1-2 Saetze Deutsch. scene_tags: klein, deutsch, max 8, "
        "nur was du im Bild siehst.",
    ]
    block = format_context(context or {})
    if block:
        lines.extend(["", "KONTEXT:", block])
    return "\n".join(lines)


def format_context(context: dict[str, Any]) -> str:
    rows: list[str] = []
    if context.get("folder_name"):
        rows.append(f"- Ordner/Album: {context['folder_name']}")
    if context.get("date"):
        src = context.get("date_source") or "unbekannt"
        rows.append(f"- Aufnahmedatum: {context['date']} (Quelle: {src})")
    if context.get("file_ctime"):
        rows.append(f"- Datei erstellt: {context['file_ctime']}")
    if context.get("file_mtime"):
        rows.append(f"- Datei geaendert: {context['file_mtime']}")
    if context.get("filename"):
        seq = context.get("sequence")
        if seq is not None:
            rows.append(f"- Dateiname: {context['filename']} (Sequenz {seq} im Ordner)")
        else:
            rows.append(f"- Dateiname: {context['filename']}")
    elif context.get("sequence") is not None:
        rows.append(f"- Sequenz im Ordner: {context['sequence']}")
    if context.get("location"):
        rows.append(f"- Ort (Metadaten): {context['location']}")
    nfaces = context.get("face_count")
    if nfaces is not None:
        rows.append(f"- Erkannte Gesichter: {nfaces}")
    assigned = context.get("people_assigned") or []
    if assigned:
        rows.append(f"- Zugeordnet (Face-Match, verwenden): {', '.join(assigned)}")
    album = context.get("people_album") or []
    if album:
        rows.append(f"- Album-Personen (Hinweis, nur bei passender Anzahl): {', '.join(album)}")
    tags = context.get("clip_tags") or []
    if tags:
        rows.append(f"- CLIP-Tags (schwach): {', '.join(tags[:8])}")
    return "\n".join(rows)


def grounded_text(context: dict[str, Any], caption: str | None = None) -> str:
    """Dokument fuer den Text-Vektor: Metadaten + Caption, nicht nur Bildsatz."""
    parts: list[str] = []
    if context.get("folder_name"):
        parts.append(f"Ordner: {context['folder_name']}")
    if context.get("date"):
        parts.append(f"Aufnahmedatum: {context['date']}")
    if context.get("file_ctime"):
        parts.append(f"Dateierstellung: {context['file_ctime']}")
    if context.get("sequence") is not None:
        parts.append(f"Sequenz: {context['sequence']}")
    if context.get("location"):
        parts.append(f"Ort: {context['location']}")
    names = context.get("people_assigned") or context.get("people_album") or []
    if names:
        parts.append(f"Personen: {', '.join(names)}")
    if caption:
        parts.append(caption)
    return "\n".join(parts)


class Captioner:
    def __init__(
        self,
        ollama: str | None = None,
        model: str = CAPTION_MODEL,
        num_ctx: int | None = None,
    ):
        """`num_ctx=0` schickt gar keine Kontextgroesse mit (kein Reload)."""
        self._url = ollama_url(ollama)
        self._model = model
        self._num_ctx = num_ctx

    def caption(self, file_path: str, context: dict[str, Any] | None = None) -> str | None:
        result = self.caption_structured(file_path, context)
        if not result:
            return None
        return result.get("caption_de") or None

    def caption_structured(
        self,
        file_path: str,
        context: dict[str, Any] | None = None,
        image_b64: str | None = None,
    ) -> dict[str, Any] | None:
        """`image_b64` ist ein bereits kodiertes JPEG.

        Ohne das liest diese Methode die Datei ein zweites Mal ueber SMB und
        dekodiert sie erneut -- rund 200 ms, die das Fliessband schon bezahlt
        hat. Der Leser-Pool reicht das Ergebnis deshalb durch.
        """
        try:
            b64 = image_b64 if image_b64 is not None else jpeg_b64(file_path)
            payload = {
                "model": self._model,
                "stream": False,
                "think": False,
                "format": "json",
                "options": caption_options(self._num_ctx),
                "messages": [
                    {
                        "role": "user",
                        "content": build_caption_prompt(context),
                        "images": [b64],
                    }
                ],
            }
            resp = post_json(f"{self._url}/api/chat", payload, timeout=180)
            raw = ((resp.get("message") or {}).get("content") or "").strip()
            if not raw:
                return None
            parsed = _parse_json(raw)
            if parsed is None:
                return {"caption_de": raw, "scene_tags": []}
            caption = parsed.get("caption_de")
            if isinstance(caption, str):
                parsed["caption_de"] = caption.strip()
            tags = parsed.get("scene_tags") or []
            parsed["scene_tags"] = [str(t).lower().strip() for t in tags if str(t).strip()][:8]
            return parsed
        except Exception as e:
            logger.warning("Captioning failed for %s: %s", file_path, e)
            return None


def jpeg_b64(file_path: str, image=None, max_side: int = 1024) -> str:
    """Foto auf `max_side` verkleinern und als base64-JPEG zurueckgeben.

    `image` ist ein bereits geladenes PIL-Image; dann faellt das Lesen und
    Dekodieren weg. `thumbnail` arbeitet in-place, deshalb auf einer Kopie.
    """
    if image is None:
        from PIL import Image

        image = Image.open(file_path)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image = image.copy()
    image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


#: Alter Name, bis nichts mehr darauf zeigt.
_jpeg_b64 = jpeg_b64


def _parse_json(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
