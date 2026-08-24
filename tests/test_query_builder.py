"""Zusammengeklickte Ausdrücke -> Qdrant-Filter und Klartext.

Beides entsteht aus demselben Baum: Was die Oberfläche anzeigt, muss das sein,
was tatsächlich gesucht wird.
"""
from api.query import QueryNode, count_conditions, describe, to_filter


def cond(field, value, label=None):
    return QueryNode(field=field, value=value, label=label)


def group(op, *children):
    return QueryNode(op=op, children=list(children))


class TestDescribe:
    def test_single_condition(self):
        assert describe(cond("person", "lennart-behr", "Jonas Meyer")) == "zeigt Jonas Meyer"

    def test_and_chain(self):
        q = group("and", cond("person", "a", "Jonas"), cond("person", "b", "Marco"))
        assert describe(q) == "zeigt Jonas UND zeigt Marco"

    def test_nested_group_gets_brackets(self):
        """Genau der Fall aus der Anforderung: A UND B UND (2015 ODER 2016)."""
        q = group(
            "and",
            cond("person", "a", "Jonas"),
            cond("person", "b", "Marco"),
            group("or", cond("year", "2015"), cond("year", "2016")),
        )
        assert describe(q) == (
            "zeigt Jonas UND zeigt Marco UND "
            "(aus dem Jahr 2015 ODER aus dem Jahr 2016)"
        )

    def test_top_level_needs_no_brackets(self):
        q = group("or", cond("year", "2015"), cond("year", "2016"))
        assert describe(q) == "aus dem Jahr 2015 ODER aus dem Jahr 2016"

    def test_single_child_group_drops_brackets(self):
        q = group("and", group("or", cond("year", "2015")))
        assert describe(q) == "aus dem Jahr 2015"

    def test_empty_values_are_ignored(self):
        q = group("and", cond("person", "a", "Jonas"), cond("year", ""))
        assert describe(q) == "zeigt Jonas"

    def test_empty_tree(self):
        assert describe(group("and")) == ""

    def test_unknown_field_is_ignored(self):
        assert describe(cond("nonsense", "x")) == ""


class TestFilter:
    def test_and_becomes_must(self):
        f = to_filter(group("and", cond("tag", "strand"), cond("tag", "nacht")))
        assert f.must is not None and len(f.must) == 2
        assert f.should is None

    def test_or_becomes_should(self):
        f = to_filter(group("or", cond("year", "2015"), cond("year", "2016")))
        assert f.should is not None and len(f.should) == 2

    def test_nesting_is_preserved(self):
        f = to_filter(group(
            "and",
            cond("tag", "strand"),
            group("or", cond("year", "2015"), cond("year", "2016")),
        ))
        assert len(f.must) == 2
        inner = [x for x in f.must if getattr(x, "should", None)]
        assert len(inner) == 1 and len(inner[0].should) == 2

    def test_year_becomes_a_range_over_the_whole_year(self):
        f = to_filter(cond("year", "2015"))
        assert f.key == "taken_at"
        # Pydantic parst die Grenzen zu datetime.
        assert (f.range.gte.year, f.range.gte.month, f.range.gte.day) == (2015, 1, 1)
        assert (f.range.lte.year, f.range.lte.month, f.range.lte.day) == (2015, 12, 31)

    def test_ambiguous_person_expands_to_should(self):
        f = to_filter(cond("person", "Sven"),
                      resolver=lambda v: ["robert-haupt", "robert-neuser"])
        assert len(f.should) == 2

    def test_unique_person_stays_a_single_condition(self):
        f = to_filter(cond("person", "Jonas"), resolver=lambda v: ["lennart-behr"])
        assert f.match.value == "lennart-behr"

    def test_empty_tree_means_no_filter(self):
        assert to_filter(group("and")) is None
        assert to_filter(cond("person", "")) is None

    def test_single_condition_needs_no_wrapper(self):
        f = to_filter(group("and", cond("tag", "strand")))
        assert f.key == "scene_tags"

    def test_lowercase_for_keyword_fields(self):
        assert to_filter(cond("tag", "Strand")).match.value == "strand"
        assert to_filter(cond("location", "Griechenland")).match.value == "griechenland"

    def test_annotation_keeps_its_case(self):
        """Notizen schreibt der Mensch - die werden nicht kleingeschrieben."""
        assert to_filter(cond("annotation", "Stripclub")).match.value == "Stripclub"


class TestCount:
    def test_counts_only_filled_conditions(self):
        q = group("and", cond("person", "a"), cond("year", ""), group("or", cond("tag", "x")))
        assert count_conditions(q) == 2
