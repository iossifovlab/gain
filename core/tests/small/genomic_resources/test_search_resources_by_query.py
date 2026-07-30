# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(resource_query=...)`` across the repository layers."""
import pathlib

import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.resource_query import ResourceQueryParseError
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
