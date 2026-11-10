"""Der Massstab des Modellvergleichs (#59).

Die Referenz-Tags entscheiden, ob ein Modellwechsel kommt; ein Muster, das
still zu viel trifft, verschiebt die Tabelle fuer beide Modelle, ohne dass
es jemand sieht.
"""
from tools.clip_vergleich import (
    ANFRAGEN,
    REFERENZ_MUSTER,
    average_precision,
    referenz_tags,
    tags_nach_regel,
)


def test_referenz_trifft_wortanfang_nicht_wortinneres():
    # Warum: "meer" in "Meerschweinchen" haette jedes Haustierfoto zum Meerbild gemacht.
    assert "meer" not in referenz_tags("Ein Meerschweinchen im Stall.", [])
    assert "meer" in referenz_tags("Blick aufs Meer bei Sonnenuntergang.", [])
    assert "strand" in referenz_tags("Liegen am Strandkorb", [])


def test_videowand_ist_kein_screenshot():
    # Warum: so stand es in der ersten Stichprobe -- eine Buehne mit Videowand.
    assert "screenshot" not in referenz_tags("Buehne mit Bildschirm und Publikum", ["bildschirm"])


def test_anfragen_zeigen_auf_bekannte_konzepte():
    # Pfad, der sonst still bricht: ein Tippfehler im Konzept liesse die Anfrage
    # nie relevant werden, sie fiele kommentarlos aus dem Mittel.
    from ingest.scene_tagger import SCENE_CONCEPTS

    assert set(ANFRAGEN.values()) <= set(REFERENZ_MUSTER) == set(SCENE_CONCEPTS)


def test_regel_wie_ingest_und_ap():
    labels = ["a", "b", "c", "d"]
    probs = [0.6, 0.3, 0.07, 0.03]
    # Boden max(0.04, 2/4=0.5): nur "a" bleibt -- dieselbe Rechnung wie SceneTagger.
    assert tags_nach_regel(probs, labels, top_k=5, min_prob=0.04, min_ratio=2.0) == ["a"]
    assert average_precision(probs, labels, {"a", "c"}) == (1 / 1 + 2 / 3) / 2
