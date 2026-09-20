"""Netzwerkfreigaben als CIFS-Volumes.

Gemessen 19.09.2026 gegen einen Samba-Container unter Docker Desktop: die
Docker-VM mountet eine Freigabe als Volume vom Typ cifs, lesend unter
/host/nas/<name>, gewaehlte Unterordner am selben Pfad beschreibbar. Was
hier geprueft wird, ist die Datei, die das beschreibt -- und dass ein
Passwort nie an eine Stelle geraet, die nach aussen geht.
"""
from __future__ import annotations

import json

import pytest

from ingest import nas, settings

UNC_WIN = "\\\\nas\\fotos"           # \\nas\fotos, wie Windows es schreibt
GEHEIM = "streng-geheim"            # Attrappe -- nie als Literal an der Aufrufstelle


class TestAdresse:
    def test_windows_und_posix_schreibweise(self):
        assert nas.normalize_unc(UNC_WIN) == "//nas/fotos"
        assert nas.normalize_unc("//nas/fotos/Alben/") == "//nas/fotos/Alben"
        assert nas.normalize_unc("smb://nas.local/fotos") == "//nas.local/fotos"
        assert nas.suggest_name("\\\\Meine-NAS\\Fotos 2024") == "meine-nas-fotos-2024"

    def test_ohne_freigabe_oder_mit_punktpfad_abgelehnt(self):
        for schlecht in ("nas", "\\\\nas", "//nas/fotos/../geheim", ""):
            with pytest.raises(ValueError):
                nas.normalize_unc(schlecht)


class TestFragment:
    def _quellen(self, tmp_path, zeilen):
        f = tmp_path / "sources.txt"
        f.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        return str(f)

    def test_freigabe_lesend_gewaehlte_unterordner_beschreibbar(self, tmp_path):
        share = nas.add_share(UNC_WIN, "kai", "geheim", "fotos")
        src = self._quellen(tmp_path, [
            "/host/nas/fotos/Alben",            # beschreibbar
            "-/host/nas/fotos/Alben/Screens",   # Ausschluss: nein
            "#/host/nas/fotos/Alt",             # still: nein
            "/host/nas/fotos",                  # die Freigabe selbst: nie ganz beschreibbar
            "/host/d/Fotos",                    # Laufwerk: nicht Sache dieser Datei
        ])
        text = nas.fragment_text([share], src)
        assert text.count("type: cifs") == 2
        assert 'device: "//nas/fotos"' in text and 'device: "//nas/fotos/Alben"' in text
        assert "target: /host/nas/fotos\n" in text and "target: /host/nas/fotos/Alben\n" in text
        ro, rw = [l for l in text.splitlines() if l.strip().startswith("o:")]
        assert ro.endswith(',ro"') and "username=kai,password=geheim" in ro
        assert not rw.endswith(',ro"')
        assert "Screens" not in text and "/host/d/" not in text

    def test_geaenderte_zugangsdaten_ergeben_ein_anderes_volume(self, tmp_path):
        src = self._quellen(tmp_path, [])
        a = nas.fragment_text([{"name": "n", "unc": "//nas/f", "user": "u", "password": "1"}], src)
        b = nas.fragment_text([{"name": "n", "unc": "//nas/f", "user": "u", "password": "2"}], src)
        name = lambda t: next(l for l in t.splitlines() if l.startswith("  nas_ro_"))
        assert name(a) != name(b)   # Docker behielte sonst das alte Passwort im Volume

    def test_ohne_freigaben_eine_leere_aber_gueltige_datei(self, tmp_path):
        text = nas.fragment_text([], self._quellen(tmp_path, []))
        assert "services: {}" in text and "cifs" not in text

    def test_datei_wird_geschrieben_und_bei_quellenaenderung_erneuert(self, tmp_path, monkeypatch):
        from api.routes import sources as sr

        src = tmp_path / "sources.txt"
        monkeypatch.setenv("PHOTOVAULT_SOURCES", str(src))
        monkeypatch.setattr(sr, "FILE", str(src))
        monkeypatch.setenv("PHOTOVAULT_BROWSE_ROOT", str(tmp_path / "host"))
        (tmp_path / "host" / "nas" / "fotos" / "Alben").mkdir(parents=True)
        monkeypatch.setattr(nas, "NAS_ROOT", str(tmp_path / "host" / "nas"))
        nas.add_share("//nas/fotos", "kai", "geheim", name="fotos")
        frag = nas.fragment_path()
        assert frag.is_file() and frag.read_text(encoding="utf-8").count("type: cifs") == 1
        sr.add_source(sr.AddRequest(path=str(tmp_path / "host" / "nas" / "fotos" / "Alben")))
        assert frag.read_text(encoding="utf-8").count("type: cifs") == 2

    def test_komma_im_passwort_wird_abgelehnt(self):
        with pytest.raises(ValueError, match="Komma"):
            nas.add_share("//nas/f", "u", "a,b")


class TestNachAussen:
    def test_passwort_steht_nie_in_zustand_oder_antwort(self, monkeypatch):
        from api.routes import setup as su

        monkeypatch.setattr(su.cap, "llm_models", lambda: set())
        monkeypatch.setattr("api.routes.jobs.sources_ready", lambda: "keine")
        monkeypatch.setattr(su, "_index_count", lambda: 0)
        out = su.add_nas(su.NasRequest(unc=UNC_WIN, user="kai", password=GEHEIM))
        assert GEHEIM not in json.dumps(out)
        st = su.state()
        assert GEHEIM not in json.dumps(st)
        assert st["nas"][0]["has_password"] is True and st["nas"][0]["mount"] == "/host/nas/nas-fotos"
        assert settings.load()["nas"][0]["password"] == GEHEIM   # aber gespeichert

    def test_passwort_bleibt_wenn_es_nicht_mitgeschickt_wird(self):
        from api.routes import setup as su

        erstes = "pw-eins"
        su.add_nas(su.NasRequest(unc="//nas/fotos", user="kai", password=erstes))
        su.add_nas(su.NasRequest(unc="//nas/fotos", user="kai"))
        assert settings.load()["nas"][0]["password"] == erstes
