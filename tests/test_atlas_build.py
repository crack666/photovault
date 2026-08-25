"""Die Karte des Archivs — Anordnung, Stapel, Beschriftung.

Geprüft wird das, was die Karte behauptet: dass Nahduplikate zu einem Bild
zusammenfallen, dass davon das aussagekräftigste obenauf liegt, und dass die
Kontinentnamen aus den Captions kommen statt aus Prompt-Floskeln.

UMAP und k-means bleiben außen vor — sie brauchen Zusatzpakete und sind
fremder, getesteter Code. Hier steht, was PhotoVault selbst entscheidet.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from tools.atlas_build import (
    FLAG_CAPTION,
    FLAG_EVENT,
    FLAG_EXIF_DATE,
    FLAG_FACES_UNNAMED,
    FLAG_IN_STACK,
    FLAG_NO_CLOCK,
    FLAG_PERSON,
    FLAG_STACK_HEAD,
    day_number,
    fallback_terms,
    find_stacks,
    label_clusters,
    photo_flags,
    pick_stack_heads,
    to_unit,
)


def _meta(**kw) -> dict:
    base = {
        "id": "p", "taken_at": None, "channel": "camera", "caption": "", "tags": [],
        "person_ids": [], "person_names": [], "event_name": None, "date_source": None,
        "gps": None, "face_count": 0, "folder": "",
    }
    base.update(kw)
    return base


def _unit(rows: list[list[float]]) -> np.ndarray:
    X = np.asarray(rows, dtype=np.float32)
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


# --------------------------------------------------------------------------
# Anordnung
# --------------------------------------------------------------------------

def test_to_unit_passt_in_das_einheitsquadrat():
    coords = np.array([[0.0, 0.0], [10.0, 5.0], [5.0, 2.5]], dtype=np.float32)
    out = to_unit(coords)
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_to_unit_haelt_das_seitenverhaeltnis():
    """Beide Achsen denselben Maßstab — sonst bedeutet Abstand in x etwas
    anderes als in y, und die Karte lügt über Ähnlichkeit."""
    coords = np.array([[0.0, 0.0], [10.0, 5.0]], dtype=np.float32)
    out = to_unit(coords)
    breite = out[:, 0].max() - out[:, 0].min()
    hoehe = out[:, 1].max() - out[:, 1].min()
    assert breite / hoehe == 2.0


def test_to_unit_zentriert_die_kuerzere_achse():
    coords = np.array([[0.0, 0.0], [10.0, 5.0]], dtype=np.float32)
    out = to_unit(coords)
    oben = out[:, 1].min()
    unten = 1.0 - out[:, 1].max()
    assert abs(oben - unten) < 1e-6


# --------------------------------------------------------------------------
# Stapel
# --------------------------------------------------------------------------

def test_find_stacks_fasst_gleiche_aufnahmen_zusammen():
    X = _unit([[1, 0, 0], [1, 0.01, 0], [1, 0, 0.01], [0, 1, 0]])
    roots = find_stacks(X, 0.95)
    assert roots[0] == roots[1] == roots[2]
    assert roots[3] != roots[0]


def test_find_stacks_laesst_verschiedene_motive_in_ruhe():
    X = _unit([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    roots = find_stacks(X, 0.95)
    assert len(set(roots.tolist())) == 3


def test_find_stacks_ueber_blockgrenzen_hinweg():
    """Die Ähnlichkeitsmatrix wird blockweise gerechnet. Zwei Kopien
    derselben Aufnahme dürfen nicht deshalb getrennt bleiben, weil sie in
    verschiedene Blöcke fielen."""
    rows = [[0, 1, 0]] * 5 + [[1, 0, 0]] + [[0, 0, 1]] * 5 + [[1, 0, 0]]
    X = _unit(rows)
    roots = find_stacks(X, 0.95, block=4)
    assert roots[5] == roots[11]


def test_stapelkopf_bevorzugt_das_aussagekraeftigste_bild():
    roots = np.array([0, 0, 0], dtype=np.int32)
    meta = [
        _meta(channel="whatsapp"),
        _meta(channel="camera", person_ids=["jonas-meyer"], caption="ein Satz"),
        _meta(channel="camera"),
    ]
    assert pick_stack_heads(roots, meta) == {1}


def test_stapelkopf_zieht_die_eigene_aufnahme_der_kopie_vor():
    """Das eigene Foto und die per WhatsApp zurückgekommene Kopie sind
    derselbe Moment. Oben liegen soll das Original."""
    roots = np.array([0, 0], dtype=np.int32)
    meta = [_meta(channel="whatsapp-sent"), _meta(channel="camera")]
    assert pick_stack_heads(roots, meta) == {1}


# --------------------------------------------------------------------------
# Beschriftung
# --------------------------------------------------------------------------

def _caption_corpus() -> tuple[np.ndarray, list[dict]]:
    """Zehn Kontinente à zehn beschriebene Fotos.

    „aufgenommen" steht in jeder Caption, „abistreich" nur in Kontinent 0.
    """
    labels, meta = [], []
    for c in range(10):
        eigen = "abistreich" if c == 0 else f"motiv{c}"
        for _ in range(10):
            labels.append(c)
            meta.append(_meta(caption=f"Hier wurde aufgenommen ein {eigen} gesehen"))
    return np.asarray(labels), meta


def test_beschriftung_nennt_das_unterscheidende_wort():
    labels, meta = _caption_corpus()
    out = label_clusters(labels, meta, 10)
    assert "abistreich" in out[0]["terms"]


def test_beschriftung_wirft_prompt_floskeln_weg():
    """„aufgenommen" steht in 24 % aller echten Captions. Ein Wort, das
    überall vorkommt, trennt nichts und darf kein Kontinentname werden."""
    labels, meta = _caption_corpus()
    out = label_clusters(labels, meta, 10)
    assert all("aufgenommen" not in c["terms"] for c in out)


def test_beschriftung_ignoriert_einzelfaelle():
    """Ein Wort aus ein, zwei Captions beschreibt ein Foto, keinen Kontinent."""
    labels, meta = _caption_corpus()
    meta[0]["caption"] += " Sonderfall einmalwort"
    meta[1]["caption"] += " einmalwort"
    out = label_clusters(labels, meta, 10)
    assert "einmalwort" not in out[0]["terms"]


def test_beschriftung_meldet_die_caption_abdeckung():
    """Ein Kontinent, dessen Name aus drei Prozent seiner Fotos stammt, muss
    das sagen — die UI blendet ihn dann zurück."""
    labels = np.asarray([0] * 10 + [1] * 10)
    meta = [_meta(caption="Abistreich auf dem Schulhof") for _ in range(5)]
    meta += [_meta() for _ in range(5)]
    meta += [_meta(caption="Skifahren auf der Piste") for _ in range(10)]
    out = label_clusters(labels, meta, 2)
    assert out[0]["cap_share"] == 0.5
    assert out[1]["cap_share"] == 1.0


def test_ohne_captions_bleiben_die_terme_leer():
    """Kein erfundener Name: ohne Captions liefert die Beschriftung nichts
    und der Aufrufer greift sichtbar auf die Tags zurück."""
    labels = np.asarray([0] * 6)
    meta = [_meta(tags=["party", "innenraum"]) for _ in range(6)]
    out = label_clusters(labels, meta, 1)
    assert out[0]["terms"] == []
    assert out[0]["cap_share"] == 0.0
    assert fallback_terms(labels, meta, 0) == ["party", "innenraum"]


# --------------------------------------------------------------------------
# Zustand
# --------------------------------------------------------------------------

def test_flags_bilden_den_ordnungszustand_ab():
    m = _meta(
        person_ids=["jonas-meyer"], caption="ein Satz", date_source="exif",
        event_name="Abiball", taken_at="2008-06-27T16:30:59Z", face_count=3,
    )
    f = photo_flags(m, in_stack=False, is_head=False)
    assert f & FLAG_PERSON
    assert f & FLAG_CAPTION
    assert f & FLAG_EXIF_DATE
    assert f & FLAG_EVENT
    assert not f & FLAG_NO_CLOCK
    assert not f & FLAG_FACES_UNNAMED


def test_gesichter_ohne_namen_werden_gemeldet():
    f = photo_flags(_meta(face_count=4), in_stack=False, is_head=False)
    assert f & FLAG_FACES_UNNAMED


def test_datum_ohne_uhrzeit_wird_gemeldet():
    """Mitternacht ist kein Aufnahmezeitpunkt, sondern ein fehlender."""
    f = photo_flags(_meta(taken_at="2024-06-15T00:00:00Z"), in_stack=False, is_head=False)
    assert f & FLAG_NO_CLOCK


def test_stapelkopf_nur_innerhalb_eines_stapels():
    """Ein Einzelfoto ist kein Stapelkopf — sonst zählt die UI es doppelt."""
    allein = photo_flags(_meta(), in_stack=False, is_head=True)
    assert not allein & FLAG_STACK_HEAD
    assert not allein & FLAG_IN_STACK

    obenauf = photo_flags(_meta(), in_stack=True, is_head=True)
    assert obenauf & FLAG_STACK_HEAD
    assert obenauf & FLAG_IN_STACK


# --------------------------------------------------------------------------
# Zeitachse
# --------------------------------------------------------------------------

def test_day_number_zaehlt_tage_seit_1970():
    tag = day_number("2024-06-15T12:00:00Z")
    erwartet = (datetime(2024, 6, 15, tzinfo=timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).days
    assert tag == erwartet


def test_day_number_meldet_fehlendes_datum():
    assert day_number(None) == -1
    assert day_number("kein Datum") == -1


# --------------------------------------------------------------------------
# „Mehr davon": Beispiele ausdünnen
# --------------------------------------------------------------------------

def test_beispiele_werden_gleichmaessig_ausgeduennt():
    """Eine Auswahl von 1 200 Fotos darf nicht nur durch ihre ersten 64
    beschrieben werden — sonst beschreibt der Schwerpunkt eine Ecke."""
    from api.routes.photos import _sample

    ids = [f"p{i:04d}" for i in range(1200)]
    got = _sample(ids, 64)
    assert len(got) == 64
    assert got[0] == "p0000"
    # Der letzte Griff liegt im letzten Zwölftel, nicht bei p0063.
    assert got[-1] > "p1100"
    assert len(set(got)) == 64


def test_kleine_auswahl_bleibt_unangetastet():
    from api.routes.photos import _sample

    ids = ["a", "b", "c"]
    assert _sample(ids, 64) == ids
