# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pytest
from gain.genomic_resources.resource_query import (
    MAX_RESOURCE_QUERY_LENGTH,
    ResourceQuery,
    ResourceQueryParseError,
)


def test_bare_id_matches_only_itself() -> None:
    query = ResourceQuery.parse("hg38/scores/phyloP100way")

    assert query.match_id("hg38/scores/phyloP100way")
    assert not query.match_id("hg19/scores/phyloP100way")


def test_star_is_path_anchored_not_token_prefixed() -> None:
    """The glob anchors at the start of the id, unlike an FTS token match.

    ``MATCH 'id: phyloP*'`` finds ``hg19/scores/phyloP100way`` because FTS
    prefixes a *token*; the glob must not.
    """
    query = ResourceQuery.parse("hg38/scores/*")

    assert query.match_id("hg38/scores/phyloP100way")
    assert not query.match_id("hg19/scores/phyloP100way")


def test_infix_star_matches() -> None:
    """``*phyloP*`` is a plain fts5 syntax error; the glob supports it."""
    query = ResourceQuery.parse("*phyloP*")

    assert query.match_id("hg38/scores/phyloP100way")
    assert not query.match_id("hg38/scores/phastCons100way")


def test_an_unparsable_query_is_rejected() -> None:
    with pytest.raises(ResourceQueryParseError):
        ResourceQuery.parse('hg38/scores/*[unclosed="x"')


def test_an_overlong_query_is_refused_before_the_grammar_runs() -> None:
    """The grammar is ambiguous, so Earley costs ~O(n^3) in the query length.

    A caller handing untrusted text to the parser must not be able to buy
    minutes of CPU with a few kilobytes, so the length bound lives here --
    on the parser -- rather than on any one of its callers.

    One character over the bound, not the kilobytes an attacker would
    actually send: a query long enough to be interesting is also long
    enough that, if this bound were removed, the test would hang for
    minutes instead of failing.
    """
    query = "a" * (MAX_RESOURCE_QUERY_LENGTH + 1)

    with pytest.raises(ResourceQueryParseError) as excinfo:
        ResourceQuery.parse(query)

    message = str(excinfo.value)
    assert str(len(query)) in message
    assert str(MAX_RESOURCE_QUERY_LENGTH) in message


def test_a_query_exactly_at_the_bound_is_accepted() -> None:
    """The bound refuses what is *over* it, so a maximal query still parses."""
    query = "a" * MAX_RESOURCE_QUERY_LENGTH

    assert ResourceQuery.parse(query).match_id(query)


@pytest.mark.parametrize(
    "resource_id",
    [
        "hg38/scores/CADD_v1.7",
        "hg19/variant_frequencies/gnomAD_v2.1.1/exomes",
        "gene_properties/gene_sets/MSigDB_curated/7.5",
        "hg38/cnv_collections/gnomAD.v4.1_Exome_CNV",
        "sub/two(1.0)",
    ],
)
def test_a_real_resource_id_is_a_valid_query(resource_id: str) -> None:
    """Ids carrying a dot or a version suffix must be expressible.

    Roughly a sixth of the ids in the public GRRs carry one; a query
    language that cannot name them is a language a user cannot paste a
    listing line into.
    """
    assert ResourceQuery.parse(resource_id).match_id(resource_id)


def test_a_dotted_id_is_globbable() -> None:
    query = ResourceQuery.parse("hg38/scores/CADD_v1.*")

    assert query.match_id("hg38/scores/CADD_v1.7")
    assert not query.match_id("hg38/scores/CADD_v2.0")


def test_a_dotted_label_value_is_queryable() -> None:
    query = ResourceQuery.parse('*[version="1.0"]')

    assert query.match_labels({"version": "1.0"})
    assert not query.match_labels({"version": "2.0"})


def test_an_empty_query_is_rejected_rather_than_matching_everything() -> None:
    with pytest.raises(ResourceQueryParseError):
        ResourceQuery.parse("")


def test_two_parses_of_the_same_query_are_equal_and_hash_alike() -> None:
    """A parsed query is a value.

    Its clauses are data rather than closures, so equality compares what
    was asked instead of which parse asked it.
    """
    query = ResourceQuery.parse('*[a="1"]')

    assert query == ResourceQuery.parse('*[a="1"]')
    assert query != ResourceQuery.parse('*[a="2"]')
    assert query != ResourceQuery.parse('other/*[a="1"]')
    assert hash(query) == hash(ResourceQuery.parse('*[a="1"]'))


def test_equals_is_an_fnmatch_over_the_label_value() -> None:
    query = ResourceQuery.parse('*[phenotype="aut*"]')

    assert query.match_labels({"phenotype": "autism"})
    assert query.match_labels({"phenotype": "aut"})
    assert not query.match_labels({"phenotype": "epilepsy"})


def test_an_absent_label_fails_every_clause_that_rejects_an_empty_value(
) -> None:
    """Absence is matched as ``""``, so it fails whatever ``""`` fails.

    This is the whole blast radius of matching absence as the empty value:
    a literal value rejects ``""``, and so does a containment test, because
    the grammar requires at least one character in a value and no non-empty
    string is contained in ``""``. That leaves a wildcard as the only clause
    an absent label satisfies.
    """
    equals = ResourceQuery.parse('*[phenotype="autism"]')
    assert not equals.match_labels({})
    assert not equals.match_labels({"provenance": "UCSC"})

    contains = ResourceQuery.parse('*["tism" in phenotype]')
    assert not contains.match_labels({})
    assert not contains.match_labels({"provenance": "UCSC"})


def test_an_absent_label_is_matched_as_the_empty_value() -> None:
    """A resource that does not carry a label has it as ``""``.

    The FTS index cannot represent the difference: building it fills ``""``
    for every label column a resource does not carry, and both spellings
    then read back identically. A matcher that refused an absent key
    outright could therefore never agree with the same query evaluated
    against the index, whichever way the SQL was written -- so absence and
    emptiness are one case here too.
    """
    query = ResourceQuery.parse('*[phenotype="*"]')

    assert query.match_labels({"phenotype": "autism"})
    assert query.match_labels({"phenotype": ""})
    assert query.match_labels({})


def test_in_is_a_substring_test() -> None:
    query = ResourceQuery.parse('*["tism" in phenotype]')

    assert query.match_labels({"phenotype": "autism"})
    assert not query.match_labels({"phenotype": "epilepsy"})


def test_and_conjoins_two_labels() -> None:
    query = ResourceQuery.parse(
        '*[phenotype="autism" and "UCSC" in provenance]')

    assert query.match_labels({"phenotype": "autism", "provenance": "UCSC"})
    assert not query.match_labels({"phenotype": "autism"})
    assert not query.match_labels(
        {"phenotype": "epilepsy", "provenance": "UCSC"})


def test_two_conditions_on_the_same_label_both_hold() -> None:
    query = ResourceQuery.parse('*["a" in pheno and "b" in pheno]')

    assert query.match_labels({"pheno": "abc"})
    assert not query.match_labels({"pheno": "ac"})


@pytest.mark.parametrize(
    ("query_text", "labels"),
    [
        ('*["Fal" in perturbed]', {"perturbed": False}),
        ('*["19" in year]', {"year": 2019}),
        ('*[year="2024"]', {"year": 2024}),
        ('*["UCSC" in provenance]', {"provenance": {"source": "UCSC"}}),
    ],
)
def test_a_non_string_label_is_compared_in_its_rendered_form(
    query_text: str, labels: dict[str, object],
) -> None:
    """``meta.labels`` is free-form YAML, so values are bools, ints, maps.

    Comparing them raw raises a bare ``TypeError`` out of the predicate.
    """
    assert ResourceQuery.parse(query_text).match_labels(labels)
