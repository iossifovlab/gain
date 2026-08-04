# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(resource_query=...)`` across the repository layers."""
import gzip
import pathlib
from typing import Any

import apsw
import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GR_SQLITE_META_FILE_NAME,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.genomic_resources.resource_types import equivalent_resource_types
from gain.genomic_resources.testing import build_filesystem_test_protocol
from gain.genomic_resources.testing.builders import (
    GRRBuilder,
    a_grr,
    a_position_score,
    a_reference_genome,
)


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


# The labels of ``scores/res_a``, named so a fixture can restate them with
# one added. ``with_labels`` replaces rather than accumulates, so a variant
# has to pass the whole mapping.
_RES_A_LABELS: dict[str, Any] = {
    "domain": "alpha", "note": "", "target": "TF1",
    # Free-form YAML: not every label value is a string.
    "perturbed": False, "year": 2019,
}


def _labelled_repository(res_a_labels: dict[str, Any]) -> GRRBuilder:
    """The resource set the indexed fixtures below are built from.

    ``note`` is genuinely empty on one resource and absent from another;
    ``target`` is absent from two. The index stores ``""`` for both
    spellings, so these are the resources on which a faithful translation
    and a guessed one disagree.

    One resource is deliberately **not** a score. The score fields are in
    the index only because a score implementation put them there, so a
    repository of scores alone never exercises what a clause naming one
    means for a resource that contributes none (gain#542).
    """
    return (
        a_grr()
        .with_resource(
            "scores/res_a",
            a_position_score().with_labels(**res_a_labels),
        )
        .with_resource(
            "scores/res_b",
            a_position_score().with_labels(domain="beta", note="noted"),
        )
        .with_resource(
            "other/res_c",
            a_position_score().with_labels(domain="alpha"),
        )
        .with_resource(
            "genomes/res_g",
            a_reference_genome().with_labels(domain="alpha", note="noted"),
        )
    )


@pytest.fixture
def labelled_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """An indexed repository whose index describes the resources it has."""
    repo = _labelled_repository(_RES_A_LABELS).build_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return repo


@pytest.fixture
def index_predating_a_label(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """A repository whose published index predates one of its labels.

    The index is built first and ``newlabel`` added afterwards with no
    rebuild -- the shape a GRR has between a curator's edit and the next
    ``grr_manage`` run, and the one case ``labelled_grr`` cannot express:
    an index built from the very resources it is compared against always
    agrees with them.

    The repository is rebuilt rather than edited in place so the label is
    added in the same vocabulary the rest of the file builds resources in;
    only ``.CONTENTS.sqlite3.gz`` is left behind, which is the point.

    Same resources as ``labelled_grr`` otherwise, so the query corpus
    means the same thing against both.
    """
    _labelled_repository(_RES_A_LABELS).build_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return _labelled_repository(
        {**_RES_A_LABELS, "newlabel": "fresh"}).build_repo(tmp_path)


def test_a_label_added_after_the_index_means_the_same_on_both_routes(
    index_predating_a_label: GenomicResourceProtocolRepo,
) -> None:
    """A clause the index cannot answer must not settle the search.

    The index has no column for ``newlabel``, but the resource serves the
    label from its ``meta.labels`` either way. Settling the clause for
    every resource at once reads "no column" as "no resource carries the
    key", which a published index older than the label makes false --
    and the narrower search then returns strictly fewer resources than
    the broader one, silently (gain#634).
    """
    without_index = {
        r.resource_id
        for r in index_predating_a_label.search_resources(
            resource_query='scores/*[newlabel="fresh"]')
    }
    through_index = {
        r.resource_id
        for r in index_predating_a_label.search_resources(
            resource_query='scores/*[newlabel="fresh"]',
            resource_type="position_score")
    }

    # Pinned as well as compared: the resource really does carry the label
    # now, so this cannot pass on two routes that agree and are both empty.
    assert without_index == {"scores/res_a"}
    assert through_index == without_index


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
    # The index also has columns that are fields of the resource rather
    # than entries of its labels. A label query naming one is asking about
    # a label, and no resource can carry these as labels -- the index build
    # refuses a label key that repeats a field it already has.
    '*[type="position_score"]',
    '*[type="*"]',
    '*[id="scores/res_a"]',
    '*[full_id="scores/res_a"]',
    '*[summary="*"]',
    '*[description="*"]',
    '*[score_ids="*"]',
    '*["phastCons" in score_ids]',
    # A key that differs from a real label only in case. SQLite resolves a
    # column name case-insensitively; the Python path looks the key up in
    # a dict, which does not.
    '*[Domain="alpha"]',
    '*[DOMAIN="*"]',
    # A label value that YAML made a bool or an int. Both sides render it
    # the same way or they disagree.
    '*[perturbed="False"]',
    '*["Fal" in perturbed]',
    '*[year="2019"]',
    '*["19" in year]',
]


@pytest.mark.parametrize("resource_type", ["position_score", "genome"])
@pytest.mark.parametrize("query", QUERY_CORPUS)
def test_the_query_means_the_same_with_and_without_the_index(
    labelled_grr: GenomicResourceProtocolRepo,
    query: str, resource_type: str,
) -> None:
    """One query, two evaluation paths, one answer.

    ``resource_query`` on its own never opens the index; adding a
    ``resource_type`` routes the search through it. Holding the type fixed
    on both sides leaves the query evaluation as the only thing that can
    differ, so any difference between the two sets is the query meaning two
    different things depending on how it was asked.

    Run for both families in the repository: a clause naming a score field
    has to mean the same thing for a resource that contributes one and for
    a resource that does not (gain#542).
    """
    # Expanded the way the indexed side expands it -- a fragment score
    # answers to two spellings, so an exact comparison here would fail for
    # a reason belonging to the test rather than to the query.
    accepted = equivalent_resource_types(resource_type)
    without_index = {
        r.resource_id
        for r in labelled_grr.search_resources(resource_query=query)
        if r.get_type() in accepted
    }
    through_index = {
        r.resource_id
        for r in labelled_grr.search_resources(
            resource_query=query, resource_type=resource_type)
    }

    assert without_index == through_index


# The clauses that only a stale index can be asked. ``newlabel`` is a key
# the published index has no column for, in every shape that decides a
# clause: rejecting the empty string, accepting it, and containment.
_STALE_INDEX_QUERIES = [
    '*[newlabel="fresh"]',
    '*[newlabel="fre*"]',
    '*["res" in newlabel]',
    '*[newlabel="*"]',
    '*[newlabel="stale"]',
    'scores/*[newlabel="fresh"]',
    '*[domain="alpha" and newlabel="fresh"]',
]


@pytest.mark.parametrize("resource_type", ["position_score", "genome"])
@pytest.mark.parametrize("query", [*QUERY_CORPUS, *_STALE_INDEX_QUERIES])
def test_a_stale_index_does_not_change_what_a_query_means(
    index_predating_a_label: GenomicResourceProtocolRepo,
    query: str, resource_type: str,
) -> None:
    """The same differential, over an index that has fallen behind.

    ``labelled_grr`` builds its index from the very resources it is
    compared against, so index and resources agree by construction and a
    whole class of divergence is invisible to it (gain#634). Here the
    index is one label out of date, which is the ordinary state of a GRR
    between a curator's edit and the next ``grr_manage`` run.
    """
    accepted = equivalent_resource_types(resource_type)
    without_index = {
        r.resource_id
        for r in index_predating_a_label.search_resources(
            resource_query=query)
        if r.get_type() in accepted
    }
    through_index = {
        r.resource_id
        for r in index_predating_a_label.search_resources(
            resource_query=query, resource_type=resource_type)
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
        # A resource field is not a label. `type` names a column of the
        # index, but no resource carries a *label* called `type`, so the
        # clause reads as empty for every one of them -- it must not be
        # answered out of the column that happens to share the name.
        ('*[type="position_score"]', set()),
        ('*[id="scores/res_a"]', set()),
        ('*[full_id="scores/res_a"]', set()),
        ('*["phastCons" in score_ids]', set()),
        ('*[type="*"]',
         {"scores/res_a", "scores/res_b", "other/res_c"}),
        ('*[score_ids="*"]',
         {"scores/res_a", "scores/res_b", "other/res_c"}),
        # ... and neither is a case variant of a label that does exist.
        ('*[Domain="alpha"]', set()),
        # A bool and an int label, compared in their rendered form.
        ('*[perturbed="False"]', {"scores/res_a"}),
        ('*[year="2019"]', {"scores/res_a"}),
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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # The non-score resource contributes no score fields, so a clause
        # naming one reads as empty for it -- a wildcard accepts that, and
        # anything an empty value fails rejects it. Without these the
        # differential above could pass on a genome that matches nothing
        # either way (gain#542).
        ('*[score_ids="*"]', {"genomes/res_g"}),
        ('*[score_descriptions="*"]', {"genomes/res_g"}),
        ('*["phastCons" in score_ids]', set()),
        ('*[score_ids="score"]', set()),
        # Its own labels still answer normally.
        ('*[domain="alpha"]', {"genomes/res_g"}),
        ('*[note="noted"]', {"genomes/res_g"}),
        ('*[domain="beta"]', set()),
    ],
)
def test_the_indexed_path_answers_a_score_clause_for_a_non_score_resource(
    labelled_grr: GenomicResourceProtocolRepo,
    query: str, expected: set[str],
) -> None:
    found = {
        r.resource_id
        for r in labelled_grr.search_resources(
            resource_query=query, resource_type="genome")
    }

    assert found == expected


def _publish_index_with_column(
    root: pathlib.Path, column: str, rows: list[tuple[str, str, str]],
) -> None:
    """Overwrite the repository's FTS index with a hand-built one.

    Stands in for a published `.CONTENTS.sqlite3.gz` that gain did not
    build -- the read path deserializes whatever the repository serves,
    without revetting the column names the index build would have refused.
    """
    db_path = root / "hostile.sqlite3"
    with apsw.Connection(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE contents_metadata "
            "(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "CREATE VIRTUAL TABLE contents USING "
            f'fts5(full_id, id, type, "{column}")')
        for row in rows:
            # S608: splicing the column name is the whole point here --
            # this builds the artefact a hostile repository would publish,
            # which is what the code under test has to survive.
            conn.execute(
                "INSERT INTO contents (full_id, id, type, "  # noqa: S608
                f'"{column}") VALUES (?, ?, ?, ?)', (*row, ""))
    (root / GR_SQLITE_META_FILE_NAME).write_bytes(
        gzip.compress(db_path.read_bytes(), mtime=0))
    db_path.unlink()


def test_a_crafted_index_column_name_cannot_break_out_of_the_query(
    tmp_path: pathlib.Path,
) -> None:
    """A published index is an untrusted artifact.

    ``grr_manage`` vets every column name it creates, but the read path
    deserializes whatever `.CONTENTS.sqlite3.gz` the repository serves --
    and the query language admits parentheses in a label key. A key naming
    a column crafted to close one call and open its own expression must
    not widen the search past the filters that were asked for.
    """
    repo = (
        a_grr()
        .with_resource("scores/res_a", a_position_score())
        .with_resource("secret/res_b", a_position_score())
        .build_repo(tmp_path)
    )
    _publish_index_with_column(
        tmp_path, "id)or(1", [
            ("scores/res_a", "scores/res_a", "position_score"),
            ("secret/res_b", "secret/res_b", "position_score"),
        ])

    found = list(repo.search_resources(
        resource_query='scores/*[id)or(1="never-matches-anything"]',
        resource_type="position_score"))

    assert found == []


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


@pytest.mark.parametrize("query", [
    "scores/*",
    '*[domain="alpha"]',
    '*[note="*"]',
    '*[type="position_score"]',
    '*[nosuchlabel="*"]',
])
def test_a_search_term_routes_the_query_the_same_way_a_type_does(
    labelled_grr: GenomicResourceProtocolRepo, query: str,
) -> None:
    """The other filter that opens the index must not change the answer.

    ``search_term`` narrows through FTS ``MATCH`` rather than a column
    comparison, so it reaches the push-down by a different route than
    ``resource_type`` does.
    """
    everything = {
        r.resource_id
        for r in labelled_grr.search_resources(search_term="position")
    }
    assert everything == {"scores/res_a", "scores/res_b", "other/res_c"}

    # The term reaches the scores and not the genome, so the comparison is
    # made inside the universe it selects -- otherwise this would measure
    # what the term matches rather than what the query means.
    without_index = {
        r.resource_id
        for r in labelled_grr.search_resources(resource_query=query)
        if r.resource_id in everything
    }
    through_index = {
        r.resource_id
        for r in labelled_grr.search_resources(
            resource_query=query, search_term="position")
    }

    assert without_index == through_index


def test_each_search_opens_its_own_metadata_connection(
    labelled_grr: GenomicResourceProtocolRepo,
) -> None:
    """The push-down registers functions on the connection it is handed.

    It reuses one set of function names per search, which is only sound
    because no two searches share a connection.
    """
    proto = labelled_grr.proto
    first = proto.open_repository_sqlite3_metadata_db()
    second = proto.open_repository_sqlite3_metadata_db()

    assert first is not second


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
