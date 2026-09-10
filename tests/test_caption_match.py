"""Suchsatz gegen Beschreibung/Tags — nicht gegen Cosinus-Rauschen."""
from api.caption_match import collapse_copies, fold, payload_matches, tokens


class TestTokens:
    def test_stopwords_and_short_bits_drop_out(self):
        assert tokens("Feuerwerk in der Nacht") == ["feuerwerk", "nacht"]

    def test_umlauts_fold(self):
        assert tokens("Vögel") == ["vogel"]

    def test_empty_after_stopwords(self):
        assert tokens("in der") == []


class TestPayloadMatch:
    def test_caption_hit(self):
        pl = {"caption_de": "Eine Flasche Bier steht auf dem Tisch."}
        assert payload_matches(pl, ["bier"])

    def test_balkon_does_not_match_a_sofa(self):
        pl = {"caption_de": "Jonas liegt auf dem Sofa.", "scene_tags": ["wohnzimmer"]}
        assert not payload_matches(pl, ["balkon"])

    def test_tag_counts(self):
        pl = {"caption_de": "Zwei Personen im Garten.", "scene_tags": ["terrasse"]}
        assert payload_matches(pl, ["terrasse"])

    def test_all_tokens_required(self):
        pl = {"caption_de": "Feuerwerk über der Stadt."}
        assert not payload_matches(pl, ["feuerwerk", "nacht"])
        pl["caption_de"] = "Feuerwerk in der Nacht über der Stadt."
        assert payload_matches(pl, ["feuerwerk", "nacht"])

    def test_prefix_hits_balkone(self):
        assert payload_matches({"caption_de": "Blick auf die Balkone."}, ["balkon"])

    def test_fold_matches_voegel(self):
        assert fold("Vögel") == "vogel"
        assert payload_matches({"caption_de": "Viele Vögel am Himmel."}, ["vogel"])

    def test_json_blob_matches_inner_words_not_keys(self):
        pl = {"caption_de": '{"caption_de": "Zahlungsaufforderung für Sounio.", "scene_tags": []}'}
        assert payload_matches(pl, ["sounio"])
        assert not payload_matches(pl, ["scene_tags"])


class TestCollapse:
    def test_same_hash_once(self):
        a = type("P", (), {"payload": {"content_sha256": "aa"}, "id": "1"})()
        b = type("P", (), {"payload": {"content_sha256": "aa"}, "id": "2"})()
        c = type("P", (), {"payload": {"content_sha256": "bb"}, "id": "3"})()
        got = collapse_copies([a, b, c])
        assert [p.id for p in got] == ["1", "3"]

    def test_missing_hash_is_kept(self):
        a = type("P", (), {"payload": {}, "id": "1"})()
        b = type("P", (), {"payload": {}, "id": "2"})()
        assert [p.id for p in collapse_copies([a, b])] == ["1", "2"]
