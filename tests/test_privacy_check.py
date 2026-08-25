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
