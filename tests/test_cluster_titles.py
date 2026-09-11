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
    def test_nimmt_einen_kurzen_titel(self):
        assert clean_title("Freizeit im Freien") == "Freizeit im Freien"

    def test_strippt_satzzeichen_und_anfuehrungszeichen(self):
        assert clean_title('"Familienalltag."') == "Familienalltag"
        assert clean_title("  Klettern in der Halle  ") == "Klettern in der Halle"

    def test_ein_satz_ist_kein_titel(self):
        lang = "Diese Schublade enthaelt Fotos von Kindern beim Spielen im Garten"
        assert clean_title(lang) is None

    def test_nichts_ist_kein_titel(self):
        assert clean_title("") is None
        assert clean_title(None) is None
        assert clean_title(42) is None


class TestPrompt:
    def test_traegt_zahl_rangwoerter_und_beispiele(self):
        p = title_prompt(480, ["nahaufnahme", "gerät"], ["Eine Platine.", "Ein Kompressor."])
        assert "480 Fotos" in p
        assert "nahaufnahme · gerät" in p
        assert "- Eine Platine." in p
        assert "2 zufaellige" in p

    def test_erlaubt_namen_ausdruecklich(self):
        """Fuer ein Privatarchiv sind "Fotos von Mira" genau die Schubladen,
        die ein Mensch anlegt. Der Prompt verbietet sie nicht."""
        p = title_prompt(10, ["mira"], ["Mira im Garten."])
        assert "Namen von Personen sind in Ordnung" in p
        assert "keine Namen" not in p.lower()

    def test_beispiele_werden_gekappt(self):
        p = title_prompt(1, [], ["x" * 500])
        assert "x" * 221 not in p


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

    def test_nach_dem_ersten_ausfall_wird_nicht_weiter_gefragt(self):
        """100 Kontinente gegen ein abgeschaltetes Ollama waeren 100
        Timeouts a drei Minuten. Nach dem ersten `None` ist Schluss -- die
        uebrigen behalten ihre Rangwoerter, die Karte kommt trotzdem."""
        zaehler = {"n": 0}

        def ask(prompt):
            zaehler["n"] += 1
            return None

        viele = [{"i": i, "n": 5, "terms": ["x"]} for i in range(20)]
        out = title_clusters(viele, lambda c: [], ask)
        assert out == [None] * 20
        assert zaehler["n"] == 1

    def test_schritt_wird_gemeldet(self):
        schritte = []
        title_clusters(self.CLUSTERS, self._samples, lambda p: {"titel": "X Y"},
                       step=schritte.append)
        assert schritte == ["titel 0", "titel 1"]


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
