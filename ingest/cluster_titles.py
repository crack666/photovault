"""Kontinente benennen, wie ein Mensch eine Schublade beschriftet.

Die Kontinentnamen kamen aus einem Ranking: die vier Woerter, die diesen
Haufen am staerksten von den anderen unterscheiden. Das ergibt
`nahaufnahme · gerät · kabel · dunklen` -- und das ist kein Name, das ist
die Ausgabe eines Rankings. Kein Mensch beschriftet so eine Schublade.

Ein Sprachmodell kann das, weil es die Beschreibungen *liest* statt sie zu
zaehlen. Nachgemessen an den Text-Clustern: aus `wiese · freien · bäume ·
entspannt` wird "Freizeit im Freien", aus `tisch · teller · essen ·
restaurant` wird "Essen und Restaurants". Und wo ein Haufen kein Thema
hat, sagt das Modell es -- "Diverse Nahaufnahmen" ist ehrlicher als vier
Rangwoerter, die ein Thema vortaeuschen.

Namen von Personen sind erlaubt. Fuer ein Privatarchiv sind "Fotos von
Mira" genau die Schubladen, die ein Mensch anlegt.

Ohne Sprachmodell -- keine Grafikkarte, Ollama aus -- bleiben die
Rangwoerter. Die Karte haengt nicht am Modell, nur ihre Lesbarkeit.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Wieviele Beschreibungen das Modell je Kontinent zu sehen bekommt. Acht
#: reichen, um ein Thema zu erkennen; mehr verlaengert nur den Prompt.
SAMPLES = 8

#: Ein Titel, der laenger ist, ist ein Satz. Und einer aus einem Wort ist
#: meist ein Rangwort, das das Modell durchgereicht hat.
MAX_WORDS = 5
MAX_CHARS = 40

#: Nach dem ersten Fehler wird nicht weiter versucht: 100 Kontinente gegen
#: ein abgeschaltetes Ollama waeren 100 Timeouts a drei Minuten.
_ABORT_AFTER = 1

PROMPT = """Du beschriftest eine Schublade in einem privaten Fotoarchiv.

In der Schublade liegen {n} Fotos. Ein Ranking hat diese Woerter als am
kennzeichnendsten ermittelt: {terms}.

{k} zufaellige Bildbeschreibungen aus der Schublade:
{beispiele}

Gib der Schublade einen Titel, wie ihn ein Mensch waehlen wuerde, der sein
Archiv ordnet: 2 bis 4 Woerter, Deutsch, ein Begriff fuer das Gemeinsame.
Kein Satz, keine Aufzaehlung der Rangwoerter, nichts erfinden, was nicht in
den Beschreibungen steht. Namen von Personen sind in Ordnung, wenn die
Schublade von dieser Person handelt. Wenn das Gemeinsame nur ein Bildstil
ist (Nahaufnahmen, Screenshots, Nachtaufnahmen), sag genau das.

Antworte als JSON: {{"titel": "..."}}"""


def title_prompt(n: int, terms: list[str], samples: list[str]) -> str:
    beispiele = "\n".join(f"- {s[:220]}" for s in samples) or "- (keine Beschreibungen)"
    return PROMPT.format(n=n, terms=" · ".join(terms) or "(keine)",
                         k=len(samples), beispiele=beispiele)


_SPACE = re.compile(r"\s+")


def clean_title(raw: Any) -> str | None:
    """Was das Modell zurueckgab, oder None, wenn es kein Titel ist."""
    if not isinstance(raw, str):
        return None
    t = _SPACE.sub(" ", raw).strip(" .;:,-–—\"'„“")
    if not t or len(t) > MAX_CHARS or len(t.split()) > MAX_WORDS:
        return None
    return t


def title_clusters(
    clusters: list[dict],
    samples_of: Callable[[int], list[str]],
    ask: Callable[[str], dict | None],
    step: Callable[[str], None] | None = None,
) -> list[str | None]:
    """Je Kontinent ein Titel -- oder None, wo keiner zu bekommen war.

    `clusters` sind die Eintraege mit `i`, `n`, `terms`; `samples_of(i)`
    liefert Beschreibungen aus dem Kontinent; `ask(prompt)` fragt das
    Modell und gibt das JSON zurueck (oder None).

    Der Rueckfall ist Sache des Aufrufers: er hat die Rangwoerter ohnehin.
    Hier wird nur nicht so getan, als waere ein Fehler ein Titel.
    """
    out: list[str | None] = []
    fehler = 0
    for c in clusters:
        if fehler >= _ABORT_AFTER:
            out.append(None)
            continue
        if step:
            step(f"titel {c['i']}")
        prompt = title_prompt(int(c.get("n", 0)), list(c.get("terms") or []),
                              list(samples_of(int(c["i"])) or [])[:SAMPLES])
        antwort = ask(prompt)
        titel = clean_title((antwort or {}).get("titel")) if isinstance(antwort, dict) else None
        if antwort is None:
            fehler += 1
            logger.warning("Kein Titel fuer Kontinent %s -- Modell antwortet nicht; "
                           "die uebrigen behalten ihre Rangwoerter.", c["i"])
        out.append(titel)
    return out
