"""UND/ODER-Verknüpfung der Suchkriterien."""
from api.routes.search import SearchRequest


class TestDefaults:
    def test_all_is_the_default(self):
        """Der Normalfall: jedes Kriterium muss zutreffen."""
        r = SearchRequest()
        assert r.match == "all"
        assert r.persons_match == "all"

    def test_caption_threshold_is_optional(self):
        assert SearchRequest().caption_min_score is None


class TestFilterShape:
    """Die Filterstruktur, die an Qdrant geht — must vs. should."""

    def _build(self, **kw):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        req = SearchRequest(**kw)
        terms = [
            FieldCondition(key="person_ids", match=MatchValue(value=p.lower()))
            for p in req.persons
        ]
        if req.persons_match == "any" and len(terms) > 1:
            conditions = [Filter(should=terms)]
        else:
            conditions = list(terms)
        if req.scene_tags:
            conditions += [
                FieldCondition(key="scene_tags", match=MatchValue(value=t))
                for t in req.scene_tags
            ]
        if not conditions:
            return None
        return Filter(should=conditions) if req.match == "any" else Filter(must=conditions)

    def test_two_persons_default_to_together(self):
        f = self._build(persons=["a", "b"])
        assert f.must is not None and len(f.must) == 2
        assert f.should is None

    def test_persons_any_becomes_one_should_group(self):
        f = self._build(persons=["a", "b"], persons_match="any")
        assert len(f.must) == 1
        assert len(f.must[0].should) == 2

    def test_match_any_turns_everything_into_should(self):
        f = self._build(persons=["a"], scene_tags=["strand"], match="any")
        assert f.must is None
        assert len(f.should) == 2

    def test_single_person_needs_no_group(self):
        f = self._build(persons=["a"], persons_match="any")
        assert len(f.must) == 1
        assert getattr(f.must[0], "should", None) is None

    def test_no_criteria_means_no_filter(self):
        assert self._build() is None
