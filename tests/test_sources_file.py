"""`sources.txt` bearbeiten, ohne sie umzuschreiben.

An dieser Datei hängt, was überhaupt in den Index kommt. Sie wird von Hand
gepflegt -- in diesem Bestand steht hinter jeder Zeile die gefundene
Bilderzahl, und ein Ordner ist mit „privat -- bewusst ausgelassen" versehen.
Eine Oberfläche, die daraus eine neu generierte Datei macht, wirft genau das
weg, was sie wertvoll macht.

Die Eigenschaft, die hier abgesichert wird, ist deshalb hart: ein Umschalten
verändert **eine** Zeile und an ihr **ein** Zeichen. Alles andere bleibt
byteweise gleich.
"""
import pytest

from ingest import sources

DATEI = """# Welche Verzeichnisse PhotoVault indiziert.
#
#   python -m ingest.pipeline --sources-file sources.txt

/mnt/photo/Handys                          #  23019 Bilder
#/mnt/photo/confidential                   #   8947 Bilder -- privat
/mnt/photo/Fotos                           #   2495 Bilder
#  /mnt/photo/Urlaub                       #   1398 Bilder

# Ausschluesse innerhalb aktiver Quellen.
-/mnt/photo/Handys/Screenshots
#-/mnt/photo/Handys/Download
"""


def lade(tmp_path, text=DATEI):
    p = tmp_path / "sources.txt"
    p.write_text(text, encoding="utf-8")
    return sources.read(str(p)), p


class TestLesen:
    def test_findet_alle_pfadzeilen(self, tmp_path):
        s, _ = lade(tmp_path)
        assert [e.path for e in s.entries] == [
            "/mnt/photo/Handys",
            "/mnt/photo/confidential",
            "/mnt/photo/Fotos",
            "/mnt/photo/Urlaub",
            "/mnt/photo/Handys/Screenshots",
            "/mnt/photo/Handys/Download",
        ]

    def test_ueberschriften_sind_keine_pfade(self, tmp_path):
        # "# Ausschluesse innerhalb aktiver Quellen." nennt keinen Pfad.
        s, _ = lade(tmp_path)
        assert all(e.path.startswith("/") for e in s.entries)

    def test_stillgelegt_wird_erkannt(self, tmp_path):
        s, _ = lade(tmp_path)
        an = {e.path for e in s.entries if e.enabled}
        assert an == {"/mnt/photo/Handys", "/mnt/photo/Fotos",
                      "/mnt/photo/Handys/Screenshots"}

    def test_luft_zwischen_raute_und_pfad(self, tmp_path):
        # "#  /mnt/photo/Urlaub" ist stillgelegt, nicht Prosa.
        s, _ = lade(tmp_path)
        urlaub = next(e for e in s.entries if e.path.endswith("Urlaub"))
        assert urlaub.enabled is False

    def test_ausschluss_wird_erkannt(self, tmp_path):
        s, _ = lade(tmp_path)
        aus = {e.path for e in s.entries if e.exclude}
        assert aus == {"/mnt/photo/Handys/Screenshots", "/mnt/photo/Handys/Download"}

    def test_notiz_bleibt_lesbar(self, tmp_path):
        s, _ = lade(tmp_path)
        conf = next(e for e in s.entries if "confidential" in e.path)
        assert "privat" in conf.note

    def test_active_nennt_nur_eingeschaltete(self, tmp_path):
        s, _ = lade(tmp_path)
        assert len(s.active) == 3


class TestUmschalten:
    def test_einschalten_entfernt_genau_die_raute(self, tmp_path):
        s, _ = lade(tmp_path)
        e = next(x for x in s.entries if "confidential" in x.path)
        neu = sources.toggle(s.lines, e.line, True)
        assert neu[e.line] == "/mnt/photo/confidential                   #   8947 Bilder -- privat"

    def test_ausschalten_setzt_die_raute_davor(self, tmp_path):
        s, _ = lade(tmp_path)
        e = next(x for x in s.entries if x.path.endswith("/Fotos"))
        neu = sources.toggle(s.lines, e.line, False)
        assert neu[e.line] == "#/mnt/photo/Fotos                           #   2495 Bilder"

    def test_einrueckung_bleibt(self, tmp_path):
        s, _ = lade(tmp_path, "    /mnt/photo/A\n")
        neu = sources.toggle(s.lines, 0, False)
        assert neu[0] == "    #/mnt/photo/A"

    def test_alle_anderen_zeilen_bleiben_byteweise_gleich(self, tmp_path):
        s, _ = lade(tmp_path)
        e = next(x for x in s.entries if "Urlaub" in x.path)
        neu = sources.toggle(s.lines, e.line, True)
        for i, (a, b) in enumerate(zip(s.lines, neu)):
            if i != e.line:
                assert a == b, f"Zeile {i + 1} hat sich mitverändert"

    def test_doppeltes_ausschalten_stapelt_keine_rauten(self, tmp_path):
        s, _ = lade(tmp_path)
        e = next(x for x in s.entries if x.path.endswith("/Fotos"))
        einmal = sources.toggle(s.lines, e.line, False)
        zweimal = sources.toggle(einmal, e.line, False)
        assert einmal == zweimal

    def test_hin_und_zurueck_ergibt_das_original(self, tmp_path):
        s, _ = lade(tmp_path)
        e = next(x for x in s.entries if x.path.endswith("/Fotos"))
        zurueck = sources.toggle(sources.toggle(s.lines, e.line, False), e.line, True)
        assert zurueck == s.lines

    def test_zeile_ohne_pfad_wird_abgelehnt(self, tmp_path):
        s, _ = lade(tmp_path)
        with pytest.raises(ValueError, match="keinen Pfad"):
            sources.toggle(s.lines, 1, False)   # "#"


class TestHinzufuegen:
    def test_haengt_hinten_an(self, tmp_path):
        s, _ = lade(tmp_path)
        neu = sources.add(s.lines, "/mnt/photo/Neu")
        assert neu[-2] == "/mnt/photo/Neu"

    def test_schlusszeile_waechst_nicht_mit(self, tmp_path):
        s, _ = lade(tmp_path)
        neu = sources.add(sources.add(s.lines, "/mnt/photo/A"), "/mnt/photo/B")
        assert neu[-3:] == ["/mnt/photo/A", "/mnt/photo/B", ""]

    def test_ausschluss_bekommt_den_strich(self, tmp_path):
        s, _ = lade(tmp_path)
        neu = sources.add(s.lines, "/mnt/photo/Handys/Neu", exclude=True)
        assert neu[-2] == "-/mnt/photo/Handys/Neu"

    def test_schrägstrich_am_ende_faellt_weg(self, tmp_path):
        s, _ = lade(tmp_path)
        assert sources.add(s.lines, "/mnt/photo/Neu/")[-2] == "/mnt/photo/Neu"

    def test_relativer_pfad_wird_abgelehnt(self, tmp_path):
        s, _ = lade(tmp_path)
        with pytest.raises(ValueError, match="Absoluter Pfad"):
            sources.add(s.lines, "photo/Neu")


class TestSchreiben:
    def test_unveraendert_geschrieben_ist_byteweise_gleich(self, tmp_path):
        s, p = lade(tmp_path)
        vorher = p.read_bytes()
        sources.write(str(p), s.lines)
        assert p.read_bytes() == vorher

    def test_kein_halbes_ergebnis_bleibt_liegen(self, tmp_path):
        s, p = lade(tmp_path)
        sources.write(str(p), s.lines)
        assert not (tmp_path / "sources.txt.tmp").exists()

    def test_geschriebenes_liest_sich_gleich(self, tmp_path):
        s, p = lade(tmp_path)
        e = next(x for x in s.entries if "Urlaub" in x.path)
        sources.write(str(p), sources.toggle(s.lines, e.line, True))
        wieder = sources.read(str(p))
        assert {x.path for x in wieder.active} == {
            "/mnt/photo/Handys", "/mnt/photo/Fotos", "/mnt/photo/Urlaub",
            "/mnt/photo/Handys/Screenshots",
        }
