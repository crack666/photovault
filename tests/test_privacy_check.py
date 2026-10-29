import sys

from tools import privacy_check
from tools.privacy_check import hook_script


def test_hook_finds_python3_when_python_is_missing():
    text = hook_script("--staged")
    assert "command -v python3" in text
    assert '"$PY" -m tools.privacy_check --staged' in text
    assert text.startswith("#!/bin/sh")


class TestAllowedParts:
    """Alltagswoerter als Namensbestandteil durchlassen -- aber nur die.

    Nachnamen werden zerlegt, damit einer auch ohne Vornamen auffaellt.
    Deutsche Nachnamen sind aber oft Alltagswoerter, und dann schlaegt die
    Pruefung bei jedem Fachbegriff an -- viermal in Folge an derselben Stelle,
    was dazu verleitet, entweder die Prosa zu verbiegen oder `--no-verify` zu
    nehmen. Beides ist schlechter als eine ausdrueckliche Ausnahme.

    Die Grenze: das Einzelteil ist erlaubt, der vollstaendige Name nie.
    """

    def _terms(self, tmp_path, monkeypatch, allow_lines, names):
        import tools.privacy_check as pc

        allow = tmp_path / "allow"
        allow.write_text("\n".join(allow_lines), encoding="utf-8")
        monkeypatch.setattr(pc, "ALLOW_TERMS", str(allow))
        monkeypatch.setattr(pc, "EXTRA_TERMS", str(tmp_path / "fehlt"))
        return set(pc.terms_from(names))

    def test_erlaubtes_einzelteil_faellt_weg(self, tmp_path, monkeypatch):
        terms = self._terms(tmp_path, monkeypatch, ["Quastel"], {"Kim Quastel"})
        assert "Quastel" not in terms

    def test_voller_name_bleibt(self, tmp_path, monkeypatch):
        # Sonst waere ein Eintrag ein Freibrief statt einer Ausnahme.
        terms = self._terms(tmp_path, monkeypatch, ["Quastel"], {"Kim Quastel"})
        assert "Kim Quastel" in terms

    def test_andere_bestandteile_bleiben(self, tmp_path, monkeypatch):
        terms = self._terms(tmp_path, monkeypatch, ["Quastel"], {"Zelda Quastel"})
        assert "Zelda" in terms

    def test_gross_klein_egal(self, tmp_path, monkeypatch):
        terms = self._terms(tmp_path, monkeypatch, ["quastel"], {"Kim Quastel"})
        assert "Quastel" not in terms

    def test_kommentare_und_leerzeilen(self, tmp_path, monkeypatch):
        terms = self._terms(
            tmp_path, monkeypatch, ["# nur ein Hinweis", "", "  Quastel  "], {"Kim Quastel"})
        assert "Quastel" not in terms

    def test_ohne_datei_aendert_sich_nichts(self, tmp_path, monkeypatch):
        import tools.privacy_check as pc

        monkeypatch.setattr(pc, "ALLOW_TERMS", str(tmp_path / "gibtsnicht"))
        monkeypatch.setattr(pc, "EXTRA_TERMS", str(tmp_path / "auchnicht"))
        terms = set(pc.terms_from({"Kim Quastel"}))
        assert {"Kim Quastel", "Quastel"} <= terms

    def test_die_liste_selbst_wird_nie_geprueft(self):
        import tools.privacy_check as pc

        assert pc.ALLOW_TERMS in pc.SKIP_FILES


class TestTextPruefung:
    """`--text` fuer Issue- und PR-Texte.

    Der Commit-Hook sieht sie nie: sie gehen per `gh` direkt an GitHub, und
    bei einem oeffentlichen Repository sind sie oeffentlich, sobald sie
    entstehen. Am 27.10.2026 stand so ein echter Name in einem Issue-Text,
    waehrend Code und Commits sauber waren.

    Nachtraegliches Pruefen hilft dort nicht -- ein veroeffentlichter Text
    steht auch nach dem Bearbeiten noch in der Versionsgeschichte des Issues.
    Darum muss die Pruefung **vor** dem Absenden greifen, und darum ist der
    Rueckgabewert das Wesentliche: `tools/gh-privat` entscheidet daran, ob es
    `gh` ueberhaupt aufruft.
    """

    def _lauf(self, tmp_path, monkeypatch, inhalt, namen=("Erfundene Person",)):
        datei = tmp_path / "text.md"
        datei.write_text(inhalt, encoding="utf-8")
        monkeypatch.setattr(privacy_check, "names_from_index",
                            lambda *a, **k: (list(namen), "Test"))
        monkeypatch.setattr(privacy_check, "extra_terms", lambda *a, **k: set())
        monkeypatch.setattr(sys, "argv", ["pv", "--text", str(datei)])
        return privacy_check.main()

    def test_name_im_text_sperrt(self, tmp_path, monkeypatch, capsys):
        rc = self._lauf(tmp_path, monkeypatch,
                        "Gemessen an der Gruppe Erfundene Person.\n")
        assert rc == 1, "ein Fund muss != 0 liefern, sonst sendet der Vorbau trotzdem"
        assert "Bitte umformulieren" in capsys.readouterr().out

    def test_sauberer_text_geht_durch(self, tmp_path, monkeypatch):
        rc = self._lauf(tmp_path, monkeypatch,
                        "Gemessen an der groessten Gruppe.\n")
        assert rc == 0

    def test_teil_eines_wortes_ist_kein_treffer(self, tmp_path, monkeypatch):
        """Wortgrenzen: sonst meldet jeder Dateiname mit einer Silbe darin."""
        rc = self._lauf(tmp_path, monkeypatch, "Erfundenes Verfahren.\n")
        assert rc == 0
