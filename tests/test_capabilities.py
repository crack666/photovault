"""Was diese Installation kann.

Der Sinn dieser Tabelle ist, dass die Oberfläche nichts anbietet, was hier
nicht steht. Ein Merkmal ohne `lost` wäre deshalb ein halbes Merkmal: der
Nutzer erfährt, dass etwas fehlt, aber nicht, was ihm dadurch fehlt.
"""
from api.capabilities import FEATURES, missing


class TestFeatureTable:
    def test_every_feature_says_what_is_lost(self):
        for key, spec in FEATURES.items():
            assert spec.get("lost"), f"{key} sagt nicht, was ohne es fehlt"

    def test_every_feature_has_a_label_and_a_remedy(self):
        for key, spec in FEATURES.items():
            assert spec.get("label"), f"{key} hat keine Bezeichnung"
            assert spec.get("hint"), f"{key} sagt nicht, wie man es behebt"

    def test_every_feature_actually_requires_something(self):
        """Ein Merkmal ohne Voraussetzung ist immer verfügbar und gehört
        nicht in diese Tabelle — es würde nur Prüfaufwand kosten."""
        for key, spec in FEATURES.items():
            assert spec.get("modules") or spec.get("models"), f"{key} braucht nichts"


class TestMissing:
    def test_nothing_required_is_always_fine(self):
        assert missing() == ""

    def test_absent_package_is_named_with_its_remedy(self):
        msg = missing(modules=("gibtesnicht_xyz",), hint="pip install irgendwas")
        assert "gibtesnicht_xyz" in msg
        assert "pip install irgendwas" in msg

    def test_unreachable_ollama_is_said_plainly(self):
        assert "nicht erreichbar" in missing(models=("egal:1b",), have_models=None)

    def test_model_not_pulled(self):
        msg = missing(models=("fehlt:1b",), hint="ollama pull fehlt:1b",
                      have_models={"anderes:7b"})
        assert "fehlt:1b" in msg
        assert "ollama pull" in msg

    def test_present_model_passes(self):
        assert missing(models=("da:7b",), have_models={"da:7b"}) == ""

    def test_packages_are_checked_before_the_network(self):
        """Erst das, was ohne Netz feststellbar ist — sonst wartet die Antwort
        auf einen Zeitablauf, obwohl sie schon feststeht."""
        msg = missing(modules=("gibtesnicht_xyz",), models=("egal:1b",), have_models=None)
        assert "gibtesnicht_xyz" in msg
        assert "erreichbar" not in msg
