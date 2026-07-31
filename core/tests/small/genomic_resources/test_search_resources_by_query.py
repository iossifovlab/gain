# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(resource_query=...)`` across the repository layers."""
import pathlib

import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import a_grr, a_position_score


@pytest.fixture
def unindexed_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """A repository with resources and labels but no FTS index.

    The builders write ``.CONTENTS.json`` and no ``.CONTENTS.sqlite3.gz``,
    which is the shape a plain checked-out GRR has before ``grr_manage``
    builds an index into it.
    """
    return (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(domain="domain_a"),
        )
        .with_resource(
            "scores/res_b",
            a_position_score().with_labels(domain="domain_b"),
        )
        .with_resource(
            "other/res_c",
            a_position_score().with_labels(domain="domain_a"),
        )
        .build_repo(tmp_path)
    )


@pytest.fixture
def labelled_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """An indexed repository carrying the labels that tell paths apart.

    ``note`` is genuinely empty on one resource and absent from another;
    ``target`` is absent from two. The index stores ``""`` for both
    spellings, so these are the resources on which a faithful translation
    and a guessed one disagree.
    """
    repo = (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(
                domain="alpha", note="", target="TF1"),
        )
        .with_resource(
            "scores/res_b",
            a_position_score().with_labels(domain="beta", note="noted"),
        )
        .with_resource(
            "other/res_c",
            a_position_score().with_labels(domain="alpha"),
        )
        .build_repo(tmp_path)
    )
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return repo


# Every shape the query language can take, over labels that are present,
# empty, absent, and unknown to the repository altogether.
QUERY_CORPUS = [
    "*",
    "scores/*",
    "*/res_a",
    "scores/res_*",
    '*[domain="alpha"]',
    '*[domain="al*"]',
    '*[domain="*"]',
    '*[note="*"]',
    '*[note="noted"]',
    '*[note="not*"]',
    '*[target="*"]',
    '*[target="TF1"]',
    '*["ote" in note]',
    '*["alpha" in domain]',
    '*["TF" in target]',
    'scores/*[domain="alpha"]',
    '*[domain="alpha" and note="*"]',
    '*[domain="alpha" and "TF" in target]',
    '*[nosuchlabel="*"]',
    '*[nosuchlabel="value"]',
    '*["x" in nosuchlabel]',
]


@pytest.mark.parametrize("query", QUERY_CORPUS)
def test_the_query_means_the_same_with_and_without_the_index(
    labelled_grr: GenomicResourceProtocolRepo, query: str,
) -> None:
    """One query, two evaluation paths, one answer.

    ``resource_query`` on its own never opens the index. Adding a
    ``resource_type`` every resource satisfies cannot change which
    resources should come back -- it only routes the search through the
    index. Any difference between the two sets is the query meaning two
    different things depending on how it was asked.
    """
    without_index = {
        r.resource_id
        for r in labelled_grr.search_resources(resource_query=query)
    }
    through_index = {
        r.resource_id
        for r in labelled_grr.search_resources(
            resource_query=query, resource_type="position_score")
    }

    assert without_index == through_index


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # An absent label reads as empty, so a wildcard accepts it -- on
        # both paths. These are the sets the differential above is pinned
        # to; without them it could pass on two paths that agree and are
        # both wrong.
        ('*[target="*"]', {"scores/res_a", "scores/res_b", "other/res_c"}),
        ('*[note="*"]', {"scores/res_a", "scores/res_b", "other/res_c"}),
        ('*[nosuchlabel="*"]',
         {"scores/res_a", "scores/res_b", "other/res_c"}),
        # ... and rejects it for anything an empty value fails.
        ('*[target="TF1"]', {"scores/res_a"}),
        ('*["TF" in target]', {"scores/res_a"}),
        ('*[nosuchlabel="value"]', set()),
        ('*["x" in nosuchlabel]', set()),
    ],
)
def test_the_indexed_path_returns_the_expected_resources(
    labelled_grr: GenomicResourceProtocolRepo,
    query: str, expected: set[str],
) -> None:
    found = {
        r.resource_id
        for r in labelled_grr.search_resources(
            resource_query=query, resource_type="position_score")
    }

    assert found == expected


def test_a_label_key_no_resource_carries_is_not_an_error(
    labelled_grr: GenomicResourceProtocolRepo,
) -> None:
    """The index has no column for a label nothing carries.

    Every resource reads as ``""`` for it, which a wildcard accepts and a
    literal rejects -- the same answer the unindexed path gives. What must
    not happen is the search failing because the column is missing.
    """
    assert len(list(labelled_grr.search_resources(
        resource_query='*[nosuchlabel="*"]',
        resource_type="position_score"))) == 3
    assert list(labelled_grr.search_resources(
        resource_query='*[nosuchlabel="value"]',
        resource_type="position_score")) == []


def test_the_query_conjoins_with_a_search_term(
    labelled_grr: GenomicResourceProtocolRepo,
) -> None:
    """All three filters narrow together, in one statement."""
    found = {
        r.resource_id
        for r in labelled_grr.search_resources(
            search_term="res_a",
            resource_type="position_score",
            resource_query='scores/*[domain="alpha"]',
        )
    }

    assert found == {"scores/res_a"}


def test_a_search_term_needs_an_index(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """Establishes why the query path must not go through the index."""
    with pytest.raises(ValueError, match="SQLite metadata DB not found"):
        list(unindexed_grr.search_resources(search_term="domain_a"))


def test_a_query_only_search_needs_no_index(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    resources = list(unindexed_grr.search_resources(resource_query="scores/*"))

    assert {r.resource_id for r in resources} == {
        "scores/res_a", "scores/res_b"}


def test_a_query_only_label_search_needs_no_index(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    resources = list(
        unindexed_grr.search_resources(
            resource_query='*[domain="domain_a"]'),
    )

    assert {r.resource_id for r in resources} == {
        "scores/res_a", "other/res_c"}


def test_the_wildcard_limit_is_annotation_policy_not_repository_policy(
    unindexed_grr: GenomicResourceProtocolRepo,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap refuses an oversized pipeline; it must not truncate a listing.

    Same repository, same query: the annotation layer refuses it, the
    repository hands back every match.
    """
    monkeypatch.setattr(AnnotationConfigParser, "WILDCARD_LIMIT", 1)

    with pytest.raises(AnnotationConfigurationError, match="Too many"):
        AnnotationConfigParser.query_resources(
            "position_score", "scores/*", unindexed_grr)

    resources = list(unindexed_grr.search_resources(resource_query="scores/*"))
    assert len(resources) == 2


def test_an_empty_query_result_is_an_error_only_for_annotation(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    with pytest.raises(AnnotationConfigurationError, match="No resources"):
        AnnotationConfigParser.query_resources(
            "position_score", "nothing/*", unindexed_grr)

    assert list(unindexed_grr.search_resources(
        resource_query="nothing/*")) == []


def test_a_group_repository_applies_the_query_to_every_child(
    tmp_path: pathlib.Path,
) -> None:
    left = (
        a_grr()
        .with_resource("scores/left", a_position_score())
        .with_resource("other/left", a_position_score())
        .build_repo(tmp_path / "left")
    )
    right = (
        a_grr()
        .with_resource("scores/right", a_position_score())
        .with_resource("other/right", a_position_score())
        .build_repo(tmp_path / "right")
    )
    group = GenomicResourceGroupRepo([left, right], "group")

    resources = list(group.search_resources(resource_query="scores/*"))

    assert {r.resource_id for r in resources} == {
        "scores/left", "scores/right"}


def test_a_dotted_and_versioned_id_is_selectable(
    tmp_path: pathlib.Path,
) -> None:
    """Ids as they actually appear in the public GRRs.

    Roughly a sixth of them carry a dot or a version suffix.
    """
    grr = (
        a_grr()
        .with_resource("hg38/scores/CADD_v1.7", a_position_score())
        .with_resource("hg38/scores/CADD_v2.0", a_position_score())
        .with_resource("hg38/scores/phyloP", a_position_score())
        .build_repo(tmp_path)
    )

    assert [r.resource_id for r in grr.search_resources(
        resource_query="hg38/scores/CADD_v1.7")] == ["hg38/scores/CADD_v1.7"]
    assert {r.resource_id for r in grr.search_resources(
        resource_query="hg38/scores/CADD_v*")} == {
        "hg38/scores/CADD_v1.7", "hg38/scores/CADD_v2.0"}


def test_a_group_does_not_dedupe_an_id_two_children_both_carry(
    tmp_path: pathlib.Path,
) -> None:
    """The query filters the fan-out; it does not change its shape.

    ``GenomicResourceGroupRepo.get_all_resources`` yields a shadowed id
    once per child, and a filtered search stays consistent with it. The
    annotation layer dedupes on top, because two annotators built from one
    id would be wrong -- that is pipeline policy, not repository policy.
    """
    left = (
        a_grr()
        .with_resource("scores/dup", a_position_score())
        .build_repo(tmp_path / "left")
    )
    right = (
        a_grr()
        .with_resource("scores/dup", a_position_score())
        .build_repo(tmp_path / "right")
    )
    group = GenomicResourceGroupRepo([left, right], "group")

    found = [r.resource_id for r in group.search_resources(
        resource_query="scores/*")]
    assert found == ["scores/dup", "scores/dup"]

    assert AnnotationConfigParser.query_resources(
        "position_score", "scores/*", group) == ["scores/dup"]


def test_an_empty_query_filters_nothing(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """``-q ""`` is an unset filter, not a query that matches nothing.

    An empty string is what a shell hands over for an unset variable, and
    the friendlier reading of ``-q "$SELECTOR"`` with no selector is the
    one that behaves like omitting the flag.
    """
    resources = list(unindexed_grr.search_resources(resource_query=""))

    assert len(resources) == 3


def test_a_malformed_query_is_rejected_before_any_resource_is_read(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    """The parse happens when the call is made, not on first ``next()``.

    ``search_resources`` returns a generator, so a parse inside its body
    would defer the error to the first iteration -- past the point a caller
    can still report it against the argument that caused it.
    """
    with pytest.raises(ResourceQueryParseError):
        unindexed_grr.search_resources(resource_query='scores/*[bad="x"')


def test_a_malformed_query_error_names_the_query_and_the_cause(
    unindexed_grr: GenomicResourceProtocolRepo,
) -> None:
    with pytest.raises(ResourceQueryParseError) as err:
        unindexed_grr.search_resources(resource_query='scores/*[bad="x"')

    assert 'scores/*[bad="x"' in str(err.value)
    assert str(err.value) != f'Unparsable resource query: \'{"x"}\''
