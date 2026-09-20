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

from ingest.ollama_client import (
    CAPTION_NUM_CTX,
    caption_model,
    litellm_headers,
    litellm_url,
    ollama_url,
    post_json,
)

logger = logging.getLogger(__name__)

JSON_SCHEMA = (
    '{"caption_de": str, "scene_tags": [str], '
    '"location_guess": str|null, "person_count": int, "mood": str}'
)


#: Obergrenze fuer `scene_tags`. Das LLM liefert bis 8; 16 laesst Luft fuer
#: den Fall, dass einmal mehr kommt, ohne dass die Liste ins Unermessliche
#: waechst.
MAX_TAGS = 16

#: Tokens fuer die JSON-Antwort. Darunter bricht der Rahmen mitten im Satz ab.
CAPTION_NUM_PREDICT = 512


def fold_tag(tag: str) -> str:
    """Umlautgefaltet und kleingeschrieben -- der Vergleichsschluessel.

    `getraenke` (CLIP-Label, ASCII) und `getränke` (LLM, echtes Deutsch) sind
    ein Etikett, nicht zwei. Sonst stehen beide im Payload und blaehen jede
    Filterliste auf.
    """
    t = (tag or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return t.replace("-", " ").replace("_", " ")


def merge_tags(existing: list[str], extra: list[str], limit: int = MAX_TAGS) -> list[str]:
    """Die Etiketten aus der Beschreibung gewinnen; CLIP ist der Rueckfall.

    `existing` sind die CLIP-Etiketten (oder der letzte Stand), `extra` die
    aus der Bildbeschreibung. Bis hierher wurden beide zusammengelegt, CLIP
    vorn. Nachgemessen an 400 Fotos war das falsch herum: CLIP raet ueber 44
    feste Begriffe, und selbst im obersten Aehnlichkeitsband -- vier Prozent
    des Bestands -- stimmte nur jeder vierte Spitzenbegriff. Ein Wundfoto
    trug `radfahren, skifahren, screenshot, hund, kinder` *vor* den sechs
    richtigen Etiketten aus der Beschreibung, und war unter "skifahren"
    filterbar. Eine Schwelle rettet das nicht: die Kosinus-Verteilung der
    richtigen und der falschen Treffer liegt fast deckungsgleich.

    Deshalb: kommen Etiketten aus der Beschreibung, sind sie das Ergebnis.
    Kommen keine -- kein Vision-Modell, keine Grafikkarte, Antwort ohne
    Etiketten --, bleiben die groben CLIP-Etiketten stehen. Grob ist besser
    als nichts, aber nicht besser als richtig.

    Dubletten innerhalb der Liste fallen umlautgefaltet zusammen; behalten
    wird die zuerst genannte Schreibweise.
    """
    quelle = extra if any((t or "").strip() for t in extra) else existing
    out: list[str] = []
    seen: set[str] = set()
    for tag in quelle:
        tag = (tag or "").strip()
        key = fold_tag(tag)
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
    # 200 Tokens reichten nicht: JSON-Rahmen plus zwei deutsche Saetze
    # wurden abgeschnitten, der Parser scheiterte, und der Rohstring
    # landete als caption_de in der Detailansicht.
    options: dict[str, Any] = {"temperature": 0.2, "num_predict": CAPTION_NUM_PREDICT}
    if value:
        options["num_ctx"] = value
    return options


def build_caption_prompt(context: dict[str, Any] | None = None) -> str:
    kind = (context or {}).get("kind") or "photo"
    if kind == "video":
        head = (
            "Analysiere das Video anhand der Einzelbilder (Anfang, Mitte, Ende). "
            "Der KONTEXT stammt aus Datei/Ordner/EXIF/Face-ID — nicht aus den Bildern. "
            "Nutze ihn in caption_de (Ort, Jahr, Anlass), "
            "aber erfinde keine Namen und keinen Ort, die nicht im Kontext stehen. "
            "Beschreibe den Clip in 1-2 Saetzen, nicht jedes Einzelbild extra."
        )
    else:
        head = (
            "Analysiere das Foto. Der KONTEXT stammt aus Datei/Ordner/EXIF/Face-ID — "
            "nicht aus dem Bild. Nutze ihn in caption_de (Ort, Jahr, Anlass), "
            "aber erfinde keine Namen und keinen Ort, die nicht im Kontext stehen."
        )
    lines = [
        head,
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
        "- Nur beschreiben, was wirklich im Bild ist. CLIP-Tags sind schwach und oft falsch.",
        "- Suchbare Dinge beim Namen: Bierglas, Weinflasche, Torte, Hund, Gitarre. "
        "Ein erkennbares Getraenk heisst bier/wein/cola/sekt in Satz und Tags, "
        "nicht nur getraenke.",
        f"- Antworte NUR mit JSON: {JSON_SCHEMA}",
        "- caption_de: 1-2 Saetze Deutsch, mit Anlass und den konkreten Dingen.",
        "- scene_tags: klein, deutsch, max 8, konkrete Substantive (bier, garten). Nichts erfinden.",
    ]
    block = format_context(context or {})
    if block:
        lines.extend(["", "KONTEXT:", block])
    return "\n".join(lines)


def format_context(context: dict[str, Any]) -> str:
    rows: list[str] = []
    if context.get("event_name"):
        rows.append(f"- Anlass (benannt): {context['event_name']}")
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
        model: str | None = None,
        num_ctx: int | None = None,
    ):
        """`num_ctx=0` schickt gar keine Kontextgroesse mit (kein Reload).

        Ohne `model` gilt, was Umgebung oder Setup jetzt sagen -- nicht der
        Stand beim Import.
        """
        self._url = ollama_url(ollama)
        self._model = model or caption_model()
        self._num_ctx = num_ctx
        pool = litellm_url()
        if pool:
            logger.info("Captions via LiteLLM %s model=%s", pool, self._model)
        else:
            logger.info("Captions via Ollama %s model=%s", self._url, self._model)

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
        images_b64: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """`image_b64` ist ein bereits kodiertes JPEG.

        Ohne das liest diese Methode die Datei ein zweites Mal ueber SMB und
        dekodiert sie erneut -- rund 200 ms, die das Fliessband schon bezahlt
        hat. Der Leser-Pool reicht das Ergebnis deshalb durch.

        `images_b64` sind mehrere Frames desselben Videos in einer Anfrage.
        """
        try:
            if images_b64:
                images = [b for b in images_b64 if b]
            elif image_b64 is not None:
                images = [image_b64]
            else:
                images = [jpeg_b64(file_path)]
            if not images:
                return None
            raw = self._complete(build_caption_prompt(context), images)
            if not raw:
                return None
            parsed = _parse_json(raw)
            if parsed is None:
                recovered = unwrap_caption(raw)
                if recovered:
                    return {"caption_de": recovered, "scene_tags": []}
                if raw.lstrip().startswith("{"):
                    logger.warning("Caption JSON unparseable for %s", file_path)
                    return None
                return {"caption_de": raw, "scene_tags": []}
            caption = parsed.get("caption_de")
            if isinstance(caption, str):
                parsed["caption_de"] = unwrap_caption(caption) or caption.strip()
            tags = parsed.get("scene_tags") or []
            parsed["scene_tags"] = [str(t).lower().strip() for t in tags if str(t).strip()][:8]
            return parsed
        except Exception as e:
            logger.warning("Captioning failed for %s: %s", file_path, e)
            return None

    def ask_json(self, prompt: str) -> dict[str, Any] | None:
        """Eine Textfrage, eine JSON-Antwort -- ohne Bild.

        Fuer Aufgaben, die das Sprachmodell hinter den Captions auch ohne
        Bild loesen kann, etwa Kontinente benennen. Nutzt denselben Weg wie
        die Captions (Pool oder Ollama, `think` aus, JSON-Format), damit es
        eine einzige Stelle gibt, die weiss, wie das Modell erreicht wird.

        `None` bei jedem Fehler: der Aufrufer hat immer einen Rueckfall, und
        eine Karte ohne Titel ist besser als keine Karte.
        """
        try:
            raw = self._complete(prompt, [])
        except Exception as e:
            logger.warning("Textanfrage fehlgeschlagen: %s", e)
            return None
        if not raw:
            return None
        return _parse_json(raw)

    def _complete(self, prompt: str, images: list[str]) -> str:
        """LiteLLM `/v1/chat/completions`, sonst Ollama `/api/chat`.

        Das Modell ist ein Pool-Alias (`local`). Welches Gewicht haengt, steht
        nur in der LiteLLM-Config -- PhotoVault schickt keinen Ollama-Tag.
        """
        pool = litellm_url()
        if pool:
            return _content_from_openai(self._via_litellm(pool, prompt, images))
        return _content_from_ollama(self._via_ollama(prompt, images))

    def _via_litellm(self, pool: str, prompt: str, images: list[str]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for blob in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{blob}"},
            })
        options = caption_options(self._num_ctx)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "temperature": options.get("temperature", 0.2),
            "max_tokens": options.get("num_predict", CAPTION_NUM_PREDICT),
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        return post_json(
            f"{pool}/v1/chat/completions",
            payload,
            timeout=180,
            headers=litellm_headers(),
        )

    def _via_ollama(self, prompt: str, images: list[str]) -> dict[str, Any]:
        return post_json(
            f"{self._url}/api/chat",
            {
                "model": self._model,
                "stream": False,
                "think": False,
                "format": "json",
                "options": caption_options(self._num_ctx),
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": images,
                }],
            },
            timeout=180,
        )


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


def _content_from_openai(resp: dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if not choices:
        return ""
    return str(((choices[0].get("message") or {}).get("content") or "")).strip()


def _content_from_ollama(resp: dict[str, Any]) -> str:
    return str(((resp.get("message") or {}).get("content") or "")).strip()


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


def unwrap_caption(text: str | None) -> str:
    """Den deutschen Satz, auch wenn der Index den JSON-Blob gespeichert hat.

    Wenn das Modell den Rahmen nicht schliesst, legt `caption_structured`
    den Rohstring in `caption_de`. Die Detailseite zeigte ihn dann
    wortwoertlich. Hier wird der Satz herausgeholt -- fuer Anzeige, Suche
    und den naechsten Caption-Lauf.
    """
    if not text:
        return ""
    current = text.strip()
    for _ in range(4):
        if not current.startswith("{"):
            return current
        inner = _caption_from_blob(current)
        if not inner or inner == current:
            return ""
        current = inner.strip()
    return current


def _caption_from_blob(raw: str) -> str | None:
    parsed = _parse_json(raw)
    if isinstance(parsed, dict):
        cap = parsed.get("caption_de")
        if isinstance(cap, str) and cap.strip():
            return cap.strip()
        return None
    return _caption_from_truncated(raw)


def _caption_from_truncated(raw: str) -> str | None:
    match = re.search(r'"caption_de"\s*:\s*"', raw)
    if not match:
        return None
    return _read_json_string(raw, match.end())


def _read_json_string(raw: str, start: int) -> str | None:
    """JSON-String ab `start` lesen, auch ohne schliessendes Anfuehrungszeichen."""
    out: list[str] = []
    i = start
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= n:
                break
            nxt = raw[i + 1]
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            out.append(mapping.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    got = "".join(out).strip()
    return got or None
