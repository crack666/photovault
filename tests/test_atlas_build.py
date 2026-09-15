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


# --------------------------------------------------------------------------
# Fortschritt melden
# --------------------------------------------------------------------------

class _FakeJob:
    def __init__(self):
        self.calls = []

    def update(self, **fields):
        self.calls.append(fields)


class TestProgressReporter:
    """Melden, was *läuft* — nicht, was fertig wurde.

    Der erste Entwurf meldete den abgeschlossenen Schritt. Weil UMAP allein die
    Hälfte der Zeit braucht, stand auf der Jobs-Seite eine halbe Minute lang
    „laden", während längst projiziert wurde.
    """

    def test_the_phase_names_what_is_running(self):
        from tools.atlas_build import progress_reporter

        job = _FakeJob()
        step = progress_reporter(job)
        step("laden")
        step("umap")
        assert [c["phase"] for c in job.calls] == ["laden", "umap"]

    def test_the_percentage_counts_only_finished_work(self):
        from tools.atlas_build import PHASES, progress_reporter

        job = _FakeJob()
        step = progress_reporter(job)
        step("laden")
        step("umap")
        assert job.calls[0]["processed"] == 0
        assert job.calls[1]["processed"] == int(dict(PHASES)["laden"] * 100)

    def test_it_reaches_the_last_phase(self):
        from tools.atlas_build import PHASES, progress_reporter

        job = _FakeJob()
        step = progress_reporter(job)
        for name, _ in PHASES:
            step(name)
        assert job.calls[-1]["phase"] == PHASES[-1][0]
        assert job.calls[-1]["processed"] < 100  # fertig meldet erst `build`

    def test_without_a_job_it_does_nothing(self):
        """Ohne Qdrant-Collection läuft die Rechnung trotzdem."""
        from tools.atlas_build import progress_reporter

        step = progress_reporter(None)
        step("laden")  # darf nicht werfen

    def test_shares_add_up(self):
        """Sonst endet der Balken bei 80 % oder springt über 100."""
        from tools.atlas_build import PHASES

        assert abs(sum(share for _, share in PHASES) - 1.0) < 1e-9


# --------------------------------------------------------------------------
# Kontinentnamen: was ein Name nicht sein darf
# --------------------------------------------------------------------------

def test_ein_stamm_belegt_nur_einen_platz():
    """In 9 von 40 Namen war ein Platz verschenkt: `kinder · kindern`,
    `autos · auto`, `schlafen · schläft`, `holzoberfläche · oberfläche`.
    Zwei Formen desselben Worts sagen nicht mehr als eine."""
    from tools.atlas_build import distinct_stems

    assert distinct_stems(["kinder", "kindern", "göring", "liana", "x"], 4) == \
        ["kinder", "göring", "liana", "x"]
    assert distinct_stems(["autos", "auto", "straße", "ansicht", "fahrzeuge"], 4) == \
        ["autos", "straße", "ansicht", "fahrzeuge"]
    assert distinct_stems(["verschneite", "märzlich", "verschneiten", "berg"], 3) == \
        ["verschneite", "märzlich", "berg"]
    # Teilwort am Ende: `oberfläche` steckt in `holzoberfläche`.
    assert distinct_stems(["holzoberfläche", "oberfläche", "gerät"], 4) == \
        ["holzoberfläche", "gerät"]


def test_aehnlicher_anfang_ist_noch_kein_stamm():
    """`garten` und `gas`, `berge` und `bericht` sind verschiedene Woerter.
    Vier gemeinsame Buchstaben plus ein kurzer Rest -- nicht jeder Anfang."""
    from tools.atlas_build import distinct_stems

    assert distinct_stems(["garten", "gas"], 2) == ["garten", "gas"]
    assert distinct_stems(["berge", "bericht"], 2) == ["berge", "bericht"]
    assert distinct_stems(["schlafen", "schläft"], 2) == ["schlafen", "schläft"] or \
        distinct_stems(["schlafen", "schläft"], 2) == ["schlafen"]  # Umlaut trennt, das ist vertretbar


def test_der_besser_bewertete_bleibt():
    """Die Liste kommt sortiert; der erste Vertreter eines Stamms gewinnt."""
    from tools.atlas_build import distinct_stems

    assert distinct_stems(["kind", "kinder"], 4) == ["kind"]
    assert distinct_stems(["kinder", "kind"], 4) == ["kinder"]


def test_monatsnamen_werden_kein_kontinentname():
    """`august` stand in zwei von 40 Namen, `märz` in zwei, dazu `september`,
    `juni`, `montag` -- aus "aufgenommen am 12. März". Die Zeit hat die Karte
    als Jahresbaender; ein Motiv ist sie nicht."""
    labels, meta = _caption_corpus()
    for i in range(10):                      # Cluster 0: Garten im August, montags
        meta[i]["caption"] = f"Kinder spielen im Garten, aufgenommen am Montag im August {2000 + i}"
    out = label_clusters(labels, meta, 10)
    assert "august" not in out[0]["terms"]
    assert "montag" not in out[0]["terms"]
    assert "garten" in out[0]["terms"]


def test_unterlage_wird_kein_kontinentname():
    """`holzoberfläche` war Name eines Haufens aus 480 Nahaufnahmen von
    Dingen -- es stand in 37 Beschreibungen, immer als das, worauf etwas
    liegt. Was nie Motiv ist, benennt keinen Kontinent."""
    labels, meta = _caption_corpus()
    for i in range(10):
        meta[i]["caption"] = "Nahaufnahme eines Geräts, das auf einer Holzoberfläche liegt"
    out = label_clusters(labels, meta, 10)
    assert "holzoberfläche" not in out[0]["terms"]
    assert "nahaufnahme" in out[0]["terms"]


# --------------------------------------------------------------------------
# Themen-Anordnung
# --------------------------------------------------------------------------

def test_themen_ohne_beschreibung_ueber_visuelle_nachbarn(monkeypatch):
    """Ein Foto ohne Beschreibung hat einen Text-Vektor nur aus Metadaten.
    Im Text-Raum ballen sich solche Fotos zu Haufen, die woertlich nach dem
    Ordner heissen. Deshalb bekommen sie ihren Platz von ihren *visuellen*
    Nachbarn, die eine Beschreibung haben -- und damit auch die Insel, in
    der diese liegen."""
    from tools import atlas_build as ab

    # UMAP durch etwas Deterministisches ersetzen: die ersten beiden
    # Vektorkomponenten. Es geht um die Zuordnung, nicht um die Projektion.
    monkeypatch.setattr(ab, "project", lambda X, **kw: X[:, :2].astype(np.float32))
    # Zehn Stimmen bei vier Kandidaten hiesse: alle stimmen ab, und die Mitte
    # gewinnt. Mit zwei Stimmen zaehlen nur die naechsten.
    monkeypatch.setattr(ab, "THEME_VOTERS", 2)
    monkeypatch.setattr(ab, "THEME_MIN", 2)

    # Zwei visuelle Gruppen: A um (1,0,0), B um (0,1,0). Foto 4 ist visuell
    # in A, hat aber keine Beschreibung -- sein Text-Vektor ist Unsinn.
    Xn = np.asarray([
        [1, 0, 0], [0.9, 0.1, 0], [0, 1, 0], [0.1, 0.9, 0], [0.95, 0.05, 0],
    ], dtype=np.float32)
    Xt = np.asarray([
        [1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0, 0, 1],   # Unsinn fuer Foto 4
    ], dtype=np.float32)
    meta = [{"caption": "a"}, {"caption": "a"}, {"caption": "b"}, {"caption": "b"}, {"caption": ""}]

    coords = ab.theme_layout(Xn, Xt, meta)

    # Foto 4 landet bei Gruppe A -- am Platz seiner Nachbarn.
    assert np.allclose(coords[4], coords[[0, 1]].mean(axis=0), atol=1e-3)
    # Und die Fotos mit Beschreibung liegen dort, wo ihr Text sie hinlegt.
    assert np.allclose(coords[0], coords[1]) and np.allclose(coords[2], coords[3])
    assert not np.allclose(coords[0], coords[2])


def test_themen_brauchen_genug_beschriebene_fotos(monkeypatch):
    from tools import atlas_build as ab

    Xn = np.eye(3, dtype=np.float32)
    Xt = np.eye(3, dtype=np.float32)
    meta = [{"caption": "a"}, {"caption": ""}, {"caption": ""}]
    import pytest
    with pytest.raises(SystemExit):
        ab.theme_layout(Xn, Xt, meta)


def test_load_second_fuellt_fehlende_mit_nan():
    from tools.atlas_build import load_second

    class _P:
        def __init__(self, pid, vec):
            self.id, self.vector = pid, ({"text": vec} if vec else {})

    class _Q:
        def retrieve(self, collection_name, ids, with_payload, with_vectors):
            store = {"a": [1.0, 2.0], "c": [3.0, 4.0]}
            return [_P(i, store.get(i)) for i in ids]

    X = load_second(_Q(), ["a", "b", "c"], "text")
    assert X.shape == (3, 2)
    assert np.allclose(X[0], [1, 2]) and np.allclose(X[2], [3, 4])
    assert np.isnan(X[1]).all()


def test_describe_clusters_traegt_leere_kontinente_ehrlich():
    """k-means kann einen leeren Cluster liefern. Der Eintrag muss da sein --
    die Oberflaeche indiziert nach `i` -- aber nichts vortaeuschen."""
    from tools.atlas_build import describe_clusters

    labels = np.asarray([0, 0, 0])
    coords = np.asarray([[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]], dtype=np.float32)
    meta = [_meta(caption="Kinder im Garten spielen") for _ in range(3)]
    out = describe_clusters(labels, coords, meta, heads=set(), k=2)
    assert out[0]["n"] == 3 and out[0]["cover"] == meta[0]["id"]
    assert out[1]["n"] == 0 and out[1]["cover"] is None and out[1]["terms"] == []


# --------------------------------------------------------------------------
# Namen im Kontinentnamen
# --------------------------------------------------------------------------

def test_namen_stehen_nie_im_kontinentnamen():
    """Eine Schublade hiess "Mira Faller", waehrend sie auf zwei von drei
    Fotos fehlte. Keine Schwelle heilt das: Naehe traegt nicht, wer auf
    einem Foto ist. Namen sind eine eigene Schicht (Anker), nie der Titel."""
    labels, meta = _caption_corpus()
    for i in range(10):
        meta[i]["caption"] = f"Mira Faller feiert Miras Geburtstag mit Freunden im Garten ({i})"
        meta[i]["person_names"] = ["Mira Faller"]        # sogar auf allen
    out = label_clusters(labels, meta, 10)
    assert not {"mira", "faller", "miras"} & set(out[0]["terms"])   # auch der Genitiv nicht
    assert "garten" in out[0]["terms"]


def test_familienname_ist_auch_ein_name():
    labels, meta = _caption_corpus()
    for i in range(10):
        meta[i]["caption"] = f"Die Familie Krueger beim Abendessen ({i})"
        meta[i]["person_names"] = ["Tobias Krueger"]
    out = label_clusters(labels, meta, 10)
    assert "krueger" not in out[0]["terms"]
    assert "abendessen" in out[0]["terms"]


def test_confirmed_people_liefert_anteile():
    from tools.atlas_build import confirmed_people

    labels = np.asarray([0] * 4)
    meta = [_meta(person_names=["Mira Faller"]), _meta(person_names=["Mira Faller", "Tobias Krueger"]),
            _meta(person_names=[]), _meta(person_names=["Mira Faller"])]
    assert confirmed_people(labels, meta, 0) == [("Mira Faller", 0.75), ("Tobias Krueger", 0.25)]


# --------------------------------------------------------------------------
# Inseln und Anker
# --------------------------------------------------------------------------

def test_inseln_sind_das_was_man_sieht():
    """k-means teilte in genau k Stuecke; die Inseln der Karte sind variabel
    viele, und was zu keiner gehoert, ist Streuung mit eigenem Index."""
    from tools.atlas_build import island_clusters

    rng = np.random.default_rng(1)
    a = rng.normal([0.2, 0.2], 0.02, (200, 2))
    b = rng.normal([0.8, 0.8], 0.02, (200, 2))
    streu = rng.uniform(0, 1, (30, 2))
    labels, loose = island_clusters(np.vstack([a, b, streu]).astype(np.float32),
                                    min_size=50, min_samples=5)
    assert loose == 2                                   # zwei Inseln, Streuung = Index 2
    assert len(set(labels[:200].tolist())) == 1         # a ist eine Insel
    assert len(set(labels[200:400].tolist())) == 1      # b auch
    assert labels[0] != labels[200]
    assert (labels == loose).sum() >= 1                 # Streuung gibt es


def test_anker_sitzen_bei_den_fotos_die_sie_tragen():
    """"Wintersport 2022 · Jonas": der Anker liegt am Schwerpunkt genau der
    Fotos, auf denen die Person bestaetigt ist -- er kann nicht auf ein Foto
    zeigen, auf dem sie fehlt."""
    from tools.atlas_build import ANCHOR_MIN, anchors_for

    n = ANCHOR_MIN * 2
    labels = np.zeros(n, dtype=int)
    coords = np.zeros((n, 2), dtype=np.float32)
    meta = []
    for i in range(n):
        links = i < ANCHOR_MIN
        coords[i] = (0.1, 0.5) if links else (0.9, 0.5)
        meta.append(_meta(taken_at="2022-02-01T10:00:00Z" if links else "2024-02-01T10:00:00Z",
                          person_names=["Mira Faller"] if links else ["Tobias Krueger"]))
    anker = {(a["kind"], a["label"]): a for a in anchors_for(labels, coords, meta, 0)}
    assert anker[("person", "Mira Faller")]["x"] < 0.2
    assert anker[("person", "Tobias Krueger")]["x"] > 0.8
    assert anker[("year", "2022")]["x"] < 0.2 and anker[("year", "2024")]["x"] > 0.8
    assert anker[("person", "Mira Faller")]["n"] == ANCHOR_MIN


def test_zu_kleine_gruppen_bekommen_keinen_anker():
    from tools.atlas_build import ANCHOR_MIN, anchors_for

    n = ANCHOR_MIN - 1
    labels = np.zeros(n, dtype=int)
    coords = np.full((n, 2), 0.5, dtype=np.float32)
    meta = [_meta(taken_at="2022-02-01T10:00:00Z", person_names=["Mira Faller"]) for _ in range(n)]
    assert anchors_for(labels, coords, meta, 0) == []


def test_benannte_serie_wird_anker():
    from tools.atlas_build import EVENT_ANCHOR_MIN, anchors_for

    n = EVENT_ANCHOR_MIN
    labels = np.zeros(n, dtype=int)
    coords = np.full((n, 2), 0.3, dtype=np.float32)
    meta = [_meta() for _ in range(n)]
    events = [{"i": 0, "name": "Skiurlaub 2022"}]
    anker = anchors_for(labels, coords, meta, 0, event_of_photo=[0] * n, events=events)
    assert [(a["kind"], a["label"], a["ref"]) for a in anker] == [("event", "Skiurlaub 2022", 0)]


def test_streuung_ist_ein_eintrag_ohne_schild():
    from tools.atlas_build import describe_clusters

    labels = np.asarray([0, 0, 0, 1, 1])
    coords = np.asarray([[0.1, 0.1], [0.1, 0.2], [0.2, 0.1], [0.9, 0.9], [0.5, 0.5]], dtype=np.float32)
    meta = [_meta(caption="Kinder im Garten spielen") for _ in range(5)]
    out = describe_clusters(labels, coords, meta, heads=set(), k=2, loose=1)
    assert out[1]["loose"] is True and out[1]["terms"] == [] and out[1]["anchors"] == []
    assert out[1]["n"] == 2 and out[0]["loose"] is False


class TestRetitle:
    """Titel neu rechnen, ohne die Karte neu zu bauen.

    Ein Kaltstart kostete einmal alle 76 Kontinente ihren Titel; die
    Karte darunter war gut. Vierzig Minuten UMAP fuer zwanzig Minuten
    Titel waeren der falsche Preis."""

    def _karte(self, tmp_path):
        import json
        ids = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(6)]
        payload = {
            "version": 3, "ids": ids, "x": [0] * 6, "y": [0] * 6, "fl": [0] * 6,
            "cl": [0, 0, 0, 1, 1, 2],
            "clusters": [
                {"i": 0, "n": 3, "terms": ["strand"], "title": None, "loose": False, "anchors": []},
                {"i": 1, "n": 2, "terms": ["schnee"], "title": "Alt", "loose": False, "anchors": []},
                {"i": 2, "n": 1, "terms": [], "title": None, "loose": True, "anchors": []},
            ],
            "themes": {"cl": [0, 0, 0, 0, 0, 1], "k": 2, "x": [0] * 6, "y": [0] * 6,
                       "clusters": [
                           {"i": 0, "n": 5, "terms": ["wiese"], "title": None, "loose": False},
                           {"i": 1, "n": 1, "terms": [], "title": None, "loose": True},
                       ]},
            "built_at": "2020-01-01T00:00:00+00:00",
        }
        (tmp_path / "atlas.json").write_text(json.dumps(payload), encoding="utf-8")
        return ids

    def test_beide_ebenen_neu_betitelt_rest_unangetastet(self, tmp_path):
        import json
        from tools.atlas_build import retitle

        ids = self._karte(tmp_path)

        class Punkt:
            def __init__(self, i, cap, names):
                self.id, self.payload = i, {"caption_de": cap, "person_names": names}

        class QC:
            def retrieve(self, collection_name, ids, with_payload, with_vectors):
                return [Punkt(i, f"Beschreibung {k}", ["Mira Faller"] if k < 3 else [])
                        for k, i in enumerate(ids)]

        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return {"titel": "Mira Faller am Strand"} if "strand" in prompt else {"titel": "Neu"}

        out = retitle(tmp_path, qc=QC(), ask=ask)
        titel = [c["title"] for c in out["clusters"]]
        assert titel[0] == "Am Strand"            # Name durchgesetzt, auch beim Neu-Titeln
        assert titel[1] == "Neu" and titel[2] is None   # Streuung wird nicht gefragt
        assert [c["title"] for c in out["themes"]["clusters"]] == ["Neu", None]
        assert "Mira Faller 100 %" in prompts[0]   # die Anteile stehen im Prompt
        gespeichert = json.loads((tmp_path / "atlas.json").read_text(encoding="utf-8"))
        assert gespeichert["clusters"][0]["title"] == "Am Strand"
        assert gespeichert["ids"] == ids and gespeichert["cl"] == [0, 0, 0, 1, 1, 2]
        assert gespeichert["built_at"] != "2020-01-01T00:00:00+00:00"
