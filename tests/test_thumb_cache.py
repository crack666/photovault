"""Den Vorschaubild-Cache abraeumen und beziffern.

Der Cache ist der Grund, warum der Atlas schnell ist: 295 MB gegen 17,5 GB
Originale, Faktor 61. Sein Schluessel ist aber der Dateipfad -- verschiebt
oder loescht man ein Foto, bleiben die alten Kacheln liegen. Gemessen: 14.858
Waisen, 94 MB.

Zwei Eigenschaften werden hier abgesichert. Das Erkennen von Waisen darf
nichts wegwerfen, was noch gebraucht wird -- ein Fehler in dieser Richtung
kostet einen Neuaufbau ueber das Netzlaufwerk. Und die Kostenschaetzung muss
stimmen: sie stand einmal bei 34 GB fuer 118 Kacheln, weil der Mittelwert
durch die Zahl der Dateien im leeren Zielverzeichnis geteilt wurde.
"""
import hashlib

import pytest

from tools import thumbs


def lege(base, path: str, size: int, inhalt: bytes = b"x" * 100):
    dig = hashlib.sha256(path.encode()).hexdigest()
    d = base / dig[:2]
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{dig}_{size}.jpg"
    f.write_bytes(inhalt)
    return f


class TestDigest:
    def test_folgt_dem_pfad(self):
        assert thumbs.digest("/a/b.jpg") == hashlib.sha256(b"/a/b.jpg").hexdigest()

    def test_verschieben_aendert_ihn(self):
        # Genau deshalb entstehen Waisen -- der Test haelt die Ursache fest.
        assert thumbs.digest("/a/b.jpg") != thumbs.digest("/c/b.jpg")


class TestScan:
    def test_findet_nach_groesse_getrennt(self, tmp_path):
        lege(tmp_path, "/foto/a.jpg", 160)
        lege(tmp_path, "/foto/a.jpg", 320)
        lege(tmp_path, "/foto/b.jpg", 160)
        da = thumbs.scan(tmp_path, (160, 320))
        assert len(da[160]) == 2
        assert len(da[320]) == 1

    def test_ignoriert_ungefragte_groessen(self, tmp_path):
        lege(tmp_path, "/foto/a.jpg", 1280)
        da = thumbs.scan(tmp_path, (160, 320))
        assert da == {160: {}, 320: {}}

    def test_merkt_die_groesse_in_bytes(self, tmp_path):
        lege(tmp_path, "/foto/a.jpg", 160, b"y" * 4321)
        da = thumbs.scan(tmp_path, (160,))
        assert next(iter(da[160].values()))[1] == 4321

    def test_leeres_verzeichnis(self, tmp_path):
        assert thumbs.scan(tmp_path / "gibtsnicht", (160,)) == {160: {}}

    def test_dateien_ohne_unterstrich_stoeren_nicht(self, tmp_path):
        (tmp_path / "aa").mkdir()
        (tmp_path / "aa" / "kaputt.jpg").write_bytes(b"x")
        assert thumbs.scan(tmp_path, (160,)) == {160: {}}

    def test_unlesbare_groesse_stoert_nicht(self, tmp_path):
        (tmp_path / "aa").mkdir()
        (tmp_path / "aa" / "abc_gross.jpg").write_bytes(b"x")
        assert thumbs.scan(tmp_path, (160,)) == {160: {}}


class TestWaisenUndKosten:
    """Die Rechnung, die `report()` anstellt -- hier ohne Qdrant nachgebaut."""

    def rechne(self, tmp_path, im_index, im_cache):
        for p, s in im_cache:
            lege(tmp_path, p, s, b"z" * 1000)
        soll = {thumbs.digest(p) for p in im_index}
        da = thumbs.scan(tmp_path, (160,))[160]
        gut = {d: v for d, v in da.items() if d in soll}
        muell = {d: v for d, v in da.items() if d not in soll}
        fehlt = soll - set(da)
        schnitt = sum(v[1] for v in gut.values()) / len(gut) if gut else 0
        return gut, muell, fehlt, len(fehlt) * schnitt

    def test_verschobenes_foto_hinterlaesst_eine_waise(self, tmp_path):
        gut, muell, fehlt, _ = self.rechne(
            tmp_path, im_index=["/neu/a.jpg"], im_cache=[("/alt/a.jpg", 160)])
        assert len(muell) == 1 and len(gut) == 0 and len(fehlt) == 1

    def test_gebrauchtes_wird_nicht_als_waise_gezaehlt(self, tmp_path):
        gut, muell, fehlt, _ = self.rechne(
            tmp_path, im_index=["/a.jpg", "/b.jpg"],
            im_cache=[("/a.jpg", 160), ("/b.jpg", 160)])
        assert len(gut) == 2 and not muell and not fehlt

    def test_kosten_kommen_aus_dem_vorhandenen(self, tmp_path):
        # Zwei da (1000 Bytes), zwei fehlen -> 2000 Bytes geschaetzt.
        _, _, fehlt, kosten = self.rechne(
            tmp_path, im_index=["/a.jpg", "/b.jpg", "/c.jpg", "/d.jpg"],
            im_cache=[("/a.jpg", 160), ("/b.jpg", 160)])
        assert len(fehlt) == 2
        assert kosten == pytest.approx(2000)

    def test_leerer_cache_schaetzt_nicht_ins_blaue(self, tmp_path):
        # Ohne Vorbild gibt es keinen Mittelwert -- dann lieber 0 als 34 GB.
        _, _, fehlt, kosten = self.rechne(tmp_path, im_index=["/a.jpg"], im_cache=[])
        assert len(fehlt) == 1 and kosten == 0


class TestAtlasGroessen:
    def test_nur_was_die_karte_braucht(self):
        # 1280 fuer alle waeren 2,4 GB fuer eine Ansicht, die man je Foto
        # einmal aufmacht -- die entsteht bei Bedarf.
        assert thumbs.ATLAS_SIZES == (160, 320)
