"""Kontinente benennen, wie ein Mensch eine Schublade beschriftet.

`nahaufnahme · gerät · kabel · dunklen` ist kein Name, das ist die Ausgabe
eines Rankings. Ein Sprachmodell macht daraus "Technik-Nahaufnahmen" --
oder sagt "Diverse Aufnahmen", wo ein Haufen kein Thema hat. Beides ist
besser als vier Rangwoerter.

Abgesichert wird hier nicht das Modell, sondern der Rahmen darum: dass
ein Fehler kein Titel wird, dass die Karte ohne Modell nicht haengt, und
dass Unsinn als Unsinn erkannt wird.
"""
from __future__ import annotations

import numpy as np

from ingest.cluster_titles import clean_title, title_clusters, title_prompt


class TestCleanTitle:
    def test_strippt_satzzeichen_und_anfuehrungszeichen(self):
        assert clean_title('"Familienalltag."') == "Familienalltag"
        assert clean_title("  Klettern in der Halle  ") == "Klettern in der Halle"

    def test_ein_satz_ist_kein_titel(self):
        lang = "Diese Schublade enthaelt Fotos von Kindern beim Spielen im Garten"
        assert clean_title(lang) is None


class TestPrompt:
    def test_traegt_zahl_rangwoerter_und_beispiele(self):
        p = title_prompt(480, ["nahaufnahme", "gerät"], ["Eine Platine.", "Ein Kompressor."])
        assert "480 Fotos" in p
        assert "nahaufnahme · gerät" in p
        assert "- Eine Platine." in p
        assert "2 zufaellige" in p

    def test_nie_nach_einer_person(self):
        """Eine Schublade hiess nach einer Person, die auf zwei von drei
        Fotos fehlte. Der Titel beschreibt die Situation; Personen sind auf
        der Karte eine eigene Schicht, mit ihrem Anteil."""
        p = title_prompt(10, ["garten"], ["Mira im Garten."], people=[("Mira Faller", 0.9)])
        assert "nie nach einer Person" in " ".join(p.split())
        assert "Mira Faller 90 %" in p


class TestTitleClusters:
    CLUSTERS = [
        {"i": 0, "n": 480, "terms": ["nahaufnahme", "gerät", "kabel"]},
        {"i": 1, "n": 230, "terms": ["flasche", "bier", "glas"]},
    ]

    @staticmethod
    def _samples(c):
        return {0: ["Nahaufnahme einer Platine.", "Ein Kompressor."],
                1: ["Bierflaschen auf dem Tisch."]}[c]

    def test_titel_je_kontinent(self):
        antworten = {0: {"titel": "Technik-Nahaufnahmen"}, 1: {"titel": "Bier und Getränke"}}
        gefragt = []

        def ask(prompt):
            gefragt.append(prompt)
            return antworten[0] if "nahaufnahme" in prompt else antworten[1]

        assert title_clusters(self.CLUSTERS, self._samples, ask) == \
            ["Technik-Nahaufnahmen", "Bier und Getränke"]
        assert len(gefragt) == 2
        assert "Nahaufnahme einer Platine." in gefragt[0]

    def test_unsinn_wird_kein_titel(self):
        """Das Modell antwortet mit einem Satz -- der Kontinent behaelt
        seine Rangwoerter, statt einen Satz als Ueberschrift zu tragen."""
        ask = lambda p: {"titel": "Hier sind viele verschiedene Fotos von technischen Geraeten"}
        assert title_clusters(self.CLUSTERS, self._samples, ask) == [None, None]

    def test_falsches_json_wird_kein_titel(self):
        ask = lambda p: {"name": "Technik"}          # falscher Schluessel
        assert title_clusters(self.CLUSTERS, self._samples, ask) == [None, None]

    def test_nach_drei_ausfaellen_in_folge_wird_nicht_weiter_gefragt(self):
        """100 Kontinente gegen ein abgeschaltetes Ollama waeren 100
        Timeouts a drei Minuten. Nach drei `None` in Folge ist Schluss -- die
        uebrigen behalten ihre Rangwoerter, die Karte kommt trotzdem."""
        zaehler = {"n": 0}

        def ask(prompt):
            zaehler["n"] += 1
            return None

        viele = [{"i": i, "n": 5, "terms": ["x"]} for i in range(20)]
        out = title_clusters(viele, lambda c: [], ask)
        assert out == [None] * 20
        assert zaehler["n"] == 3


class TestApplyTitles:
    def test_schreibt_title_und_faellt_sauber_zurueck(self):
        """`apply_titles` haengt den Titel an den Eintrag; wo keiner kommt,
        bleibt `title` None und die Rangwoerter stehen unveraendert."""
        from tools.atlas_build import apply_titles

        clusters = [{"i": 0, "n": 3, "terms": ["garten"]}, {"i": 1, "n": 2, "terms": ["auto"]}]
        labels = np.asarray([0, 0, 0, 1, 1])
        meta = [{"caption": f"Bild {i}"} for i in range(5)]
        antworten = iter([{"titel": "Garten"}, {"titel": "ein viel zu langer titel mit sehr vielen woertern"}])
        n = apply_titles(clusters, labels, meta, ask=lambda p: next(antworten))
        assert n == 1
        assert clusters[0]["title"] == "Garten"
        assert clusters[1]["title"] is None
        assert clusters[1]["terms"] == ["auto"]


class TestDisambiguate:
    """Der erste Bau ergab einen Familiennamen zweimal und einen Vornamen
    dreimal. Das Modell sieht jeden Kontinent fuer sich."""

    def test_zweiter_bekommt_ein_eigenes_rangwort(self):
        from ingest.cluster_titles import disambiguate

        clusters = [
            {"i": 0, "n": 463, "terms": ["florian", "kinder", "liana", "wiese"]},
            {"i": 1, "n": 409, "terms": ["kinder", "göring", "liana", "cataleya"]},
        ]
        out = disambiguate(clusters, ["Familie Mira", "Familie Mira"])
        assert out[0] == "Familie Mira"                 # der groessere bleibt blank
        assert out[1] == "Familie Mira · göring"        # erstes Wort, das dem anderen fehlt

    def test_rangwort_das_schon_im_titel_steckt_zaehlt_nicht(self):
        from ingest.cluster_titles import disambiguate

        clusters = [
            {"i": 0, "n": 10, "terms": ["mira", "garten"]},
            {"i": 1, "n": 5, "terms": ["mira", "strand"]},
        ]
        assert disambiguate(clusters, ["Mira", "Mira"]) == ["Mira", "Mira · strand"]

    def test_dreifach(self):
        from ingest.cluster_titles import disambiguate

        clusters = [
            {"i": 0, "n": 3, "terms": ["a", "x"]},
            {"i": 1, "n": 2, "terms": ["a", "y"]},
            {"i": 2, "n": 1, "terms": ["a", "z"]},
        ]
        out = disambiguate(clusters, ["T", "T", "T"])
        assert out == ["T", "T · y", "T · z"]

    def test_verschiedene_und_leere_bleiben(self):
        from ingest.cluster_titles import disambiguate

        clusters = [{"i": 0, "n": 3, "terms": ["a"]}, {"i": 1, "n": 2, "terms": ["b"]}]
        assert disambiguate(clusters, ["Eins", None]) == ["Eins", None]
        assert disambiguate(clusters, ["Eins", "Zwei"]) == ["Eins", "Zwei"]

    def test_title_clusters_liefert_eindeutige_titel(self):
        from ingest.cluster_titles import title_clusters

        clusters = [{"i": 0, "n": 9, "terms": ["kind", "garten"]},
                    {"i": 1, "n": 4, "terms": ["kind", "strand"]}]
        out = title_clusters(clusters, lambda c: ["x"], lambda p: {"titel": "Kinder"})
        assert out == ["Kinder", "Kinder · strand"]


class TestPersonenImPrompt:
    def test_anteile_und_regel_stehen_im_prompt(self):
        """Das Modell sieht acht Beschreibungen; nennen fuenf eine Person,
        wirkt sie wie das Thema. Der bestaetigte Anteil steht deshalb
        ausdruecklich dabei -- und die Schwelle, ab der ein Name traegt."""
        p = title_prompt(259, ["tracht", "dirndl", "fest"], ["Ein Fest."],
                         people=[("Nele Sturm", 0.34), ("Mira Faller", 0.32)])
        assert "Nele Sturm 34 %" in p and "Mira Faller 32 %" in p

    def test_ohne_personen_steht_das_auch_da(self):
        p = title_prompt(10, ["garten"], ["x"], people=[])
        assert "Bestaetigte Personen: keine" in p

    def test_title_clusters_reicht_die_personen_durch(self):
        gesehen = []

        def ask(prompt):
            gesehen.append(prompt)
            return {"titel": "Gartenfeste"}

        clusters = [{"i": 0, "n": 9, "terms": ["garten"]}]
        title_clusters(clusters, lambda c: ["x"], ask,
                       people_of=lambda c: [("Mira Faller", 0.32)])
        assert "Mira Faller 32 %" in gesehen[0]


class TestNamenWerdenDurchgesetzt:
    """Das Modell hat "nie nach einer Person" acht Mal ignoriert -- gerade
    dort, wo die Person auf ueber 90 % der Fotos war. Eine Regel, die nur
    im Prompt steht, ist keine."""

    FORBIDDEN = {"mira", "faller", "jonas"}

    def test_namensteile_werden_gestrichen(self):
        from ingest.cluster_titles import without_names

        assert without_names("Mira Faller Alltag", self.FORBIDDEN) == ("Alltag", True)
        assert without_names("Baby Mira Faller", self.FORBIDDEN) == ("Baby", True)
        assert without_names("Jonas im Kindergarten", self.FORBIDDEN) == ("Im Kindergarten", True)
        # Der Genitiv ist derselbe Name: nach dem ersten durchgesetzten Lauf
        # stand "<Vorname>s 18. Geburtstag" wieder auf der Karte.
        assert without_names("Miras 18. Geburtstag", self.FORBIDDEN) == ("18. Geburtstag", True)
        assert without_names("Jonas' Abschied", self.FORBIDDEN) == ("Abschied", True)

    def test_ohne_tragendes_wort_kein_titel(self):
        from ingest.cluster_titles import without_names

        assert without_names("Mira Faller", self.FORBIDDEN) == (None, True)
        assert without_names("Mira und Jonas", self.FORBIDDEN) == (None, True)   # "und" traegt nicht

    def test_ohne_namen_unveraendert(self):
        from ingest.cluster_titles import without_names

        assert without_names("Feste im Freien", self.FORBIDDEN) == ("Feste im Freien", False)
        assert without_names(None, self.FORBIDDEN) == (None, False)

    def test_erst_nachfragen_dann_streichen(self):
        """Beim Verstoss einmal nachfragen; kommt wieder ein Name, bleibt
        der gestrichene erste Vorschlag."""
        from ingest.cluster_titles import title_clusters

        antworten = iter([{"titel": "Mira Faller Alltag"}, {"titel": "Familienalltag"}])
        gefragt = []

        def ask(prompt):
            gefragt.append(prompt)
            return next(antworten)

        out = title_clusters([{"i": 0, "n": 9, "terms": ["alltag"]}], lambda c: ["x"], ask,
                             forbidden=self.FORBIDDEN)
        assert out == ["Familienalltag"]
        assert len(gefragt) == 2 and "enthielt einen Personennamen" in gefragt[1]

    def test_zweiter_verstoss_faellt_auf_das_gestrichene(self):
        from ingest.cluster_titles import title_clusters

        antworten = iter([{"titel": "Mira Faller Alltag"}, {"titel": "Jonas Alltag"}])
        out = title_clusters([{"i": 0, "n": 9, "terms": ["alltag"]}], lambda c: ["x"],
                             lambda p: next(antworten), forbidden=self.FORBIDDEN)
        assert out == ["Alltag"]

    def test_abbruch_erst_nach_drei_ausfaellen_in_folge(self):
        """Der allererste Aufruf nach der UMAP-Phase lief in einen Kaltstart --
        mit Abbruch nach dem ersten blieben 76 Kontinente ohne Titel."""
        from ingest.cluster_titles import title_clusters

        antworten = iter([None, {"titel": "Feste"}, None, None, None, {"titel": "Nie"}])
        clusters = [{"i": i, "n": 5, "terms": ["x"]} for i in range(6)]
        out = title_clusters(clusters, lambda c: [], lambda p: next(antworten))
        assert out == [None, "Feste", None, None, None, None]   # nach drei in Folge Schluss
