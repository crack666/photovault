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

Namen von Personen sind kein Titel. Eine Schublade hiess "Mira Faller",
waehrend sie auf zwei von drei Fotos fehlte: das Modell sah acht
Beschreibungen, fuenf nannten sie, und schloss auf das Thema. Keine
Schwelle heilt das -- Naehe traegt nicht, wer auf einem Foto ist. Personen
sind deshalb eine eigene Schicht auf der Karte (Anker am Schwerpunkt ihrer
bestaetigten Fotos, `tools.atlas_build.anchors_for`); der Titel beschreibt
die Situation. Die bestaetigten Anteile bekommt das Modell trotzdem, als
Kontext: "Selfies" ist der richtige Titel fuer 96 % Jonas.

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
{personen}
{k} zufaellige Bildbeschreibungen aus der Schublade:
{beispiele}

Gib der Schublade einen Titel, wie ihn ein Mensch waehlen wuerde, der sein
Archiv ordnet: 2 bis 4 Woerter, Deutsch, ein Begriff fuer das Gemeinsame.
Kein Satz, keine Aufzaehlung der Rangwoerter, nichts erfinden, was nicht in
den Beschreibungen steht. Benenne die Schublade nach der Situation, nie nach
einer Person -- Personen bekommen auf der Karte eigene Wegweiser, mit ihrem
Anteil oben. Wenn das Gemeinsame nur ein Bildstil ist (Nahaufnahmen,
Screenshots, Nachtaufnahmen), sag genau das.

Antworte als JSON: {{"titel": "..."}}"""


def title_prompt(n: int, terms: list[str], samples: list[str],
                 people: list[tuple[str, float]] | None = None) -> str:
    """`people`: bestaetigte Personen mit Anteil, haeufigste zuerst.

    Das Modell sieht nur acht Beschreibungen. Nennen die eine Person
    fuenfmal, wirkt sie wie das Thema -- auch wenn sie auf zwei von drei
    Fotos fehlt. Der Anteil steht deshalb ausdruecklich dabei, als Kontext
    fuer die Situation ("Selfies", nicht "Jonas") -- und die Regel, dass
    ein Name nie der Titel ist. Personen sind eine eigene Schicht auf der
    Karte, per Konstruktion nur dort, wo sie bestaetigt sind.
    """
    beispiele = "\n".join(f"- {s[:220]}" for s in samples) or "- (keine Beschreibungen)"
    if people:
        zeile = ", ".join(f"{name} {int(round(share * 100))} %" for name, share in people)
        personen = f"\nBestaetigte Personen (Anteil der Fotos): {zeile}.\n"
    else:
        personen = "\nBestaetigte Personen: keine.\n"
    return PROMPT.format(n=n, terms=" · ".join(terms) or "(keine)", personen=personen,
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


def disambiguate(clusters: list[dict], titles: list[str | None]) -> list[str | None]:
    """Gleiche Titel unterscheidbar machen.

    Der erste Bau ergab einen Familiennamen zweimal und einen Vornamen
    dreimal -- in der Sprungliste nicht zu unterscheiden, auf der Karte auch
    nicht. Das Modell sieht jeden Kontinent fuer sich; dass ein anderer
    denselben Titel bekam, kann es nicht wissen.

    Angehaengt wird das erste Rangwort, das im Titel noch nicht steckt und
    das der Dublette fehlt: "Familie Mira · wiese" gegen "Familie Mira ·
    strand". Der groesste Kontinent behaelt den blanken Titel.
    """
    out = list(titles)
    gruppen: dict[str, list[int]] = {}
    for i, t in enumerate(out):
        if t:
            gruppen.setdefault(t.lower(), []).append(i)
    for _, idx in gruppen.items():
        if len(idx) < 2:
            continue
        idx.sort(key=lambda i: -int(clusters[i].get("n", 0)))
        for i in idx[1:]:
            eigene = [w for w in (clusters[i].get("terms") or [])
                      if w.lower() not in out[i].lower()]
            fremde = {w for j in idx if j != i for w in (clusters[j].get("terms") or [])}
            zusatz = next((w for w in eigene if w not in fremde), None) or (eigene[0] if eigene else None)
            if zusatz:
                out[i] = f"{out[i]} · {zusatz}"
    return out


def title_clusters(
    clusters: list[dict],
    samples_of: Callable[[int], list[str]],
    ask: Callable[[str], dict | None],
    step: Callable[[str], None] | None = None,
    people_of: Callable[[int], list[tuple[str, float]]] | None = None,
) -> list[str | None]:
    """Je Kontinent ein Titel -- oder None, wo keiner zu bekommen war.

    `clusters` sind die Eintraege mit `i`, `n`, `terms`; `samples_of(i)`
    liefert Beschreibungen aus dem Kontinent; `people_of(i)` die
    bestaetigten Personen mit Anteil; `ask(prompt)` fragt das Modell und
    gibt das JSON zurueck (oder None).

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
                              list(samples_of(int(c["i"])) or [])[:SAMPLES],
                              people=people_of(int(c["i"])) if people_of else None)
        antwort = ask(prompt)
        titel = clean_title((antwort or {}).get("titel")) if isinstance(antwort, dict) else None
        if antwort is None:
            fehler += 1
            logger.warning("Kein Titel fuer Kontinent %s -- Modell antwortet nicht; "
                           "die uebrigen behalten ihre Rangwoerter.", c["i"])
        out.append(titel)
    return disambiguate(clusters, out)
