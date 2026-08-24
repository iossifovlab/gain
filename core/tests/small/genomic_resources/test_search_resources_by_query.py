# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``search_resources(resource_query=...)`` across the repository layers.

The repository fixtures below are module-scoped. Each builds a *constant*
repository and every consuming test only queries it -- nothing writes to a
repository directory after it is built. ``search_resources`` reads
``.CONTENTS.json.gz``, and the indexed route deserializes
``.CONTENTS.sqlite3.gz`` into an ``:memory:`` connection per search
(``FsspecReadOnlyProtocol.open_repository_metadata``), so no two tests share a
connection and no query can leave a trace another test could read.

Read-only-ness is decided per fixture, not wholesale. The two fixtures that
rebuild a repository over its own published index do that rebuild *inside the
fixture body*, once, before any test using them runs, and each still owns its
own directory -- the mutation is fixture setup rather than something a test
does. ``index_predating_a_non_mapping_labels`` has a single consumer and stays
function-scoped, since sharing would save nothing.

The scope matters because this module is setup-bound: 286 items that share
~0.15s of query time between them were rebuilding a repository and its FTS
index apiece (gain#863). Under ``pytest -n`` each worker builds its own copy
of what it needs, so this is once per worker, not once per run.
"""
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


@pytest.fixture(scope="module")
def unindexed_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    """A repository with resources and labels but no FTS index.

    The builders write ``.CONTENTS.json.gz`` and no ``.CONTENTS.sqlite3.gz``,
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
        .build_repo(tmp_path_factory.mktemp("unindexed_grr"))
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


@pytest.fixture(scope="module")
def labelled_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    """An indexed repository whose index describes the resources it has."""
    root = tmp_path_factory.mktemp("labelled_grr")
    repo = _labelled_repository(_RES_A_LABELS).build_repo(root)
    _create_contents_db(build_filesystem_test_protocol(root))
    return repo


@pytest.fixture(scope="module")
def index_predating_a_label(
    tmp_path_factory: pytest.TempPathFactory,
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
    root = tmp_path_factory.mktemp("index_predating_a_label")
    _labelled_repository(_RES_A_LABELS).build_repo(root)
    _create_contents_db(build_filesystem_test_protocol(root))
    return _labelled_repository(
        {**_RES_A_LABELS, "newlabel": "fresh"}).build_repo(root)


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


@pytest.fixture(scope="module")
def index_predating_a_label_edit(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    """A repository whose published index predates a label *value*.

    Built the same way as ``index_predating_a_label``, except that the
    curator's edit changes the value of a label the index already has a
    column for rather than adding a key it has never heard of. That is the
    case ``index_predating_a_label`` cannot express: a key with no column
    is the ``gain#634`` shape, and a clause on it is the only one that was
    ever handed back to the caller. Here the column exists and holds
    ``alpha`` while the resource says ``gamma`` (gain#646).

    Same resources as ``labelled_grr`` otherwise, so the query corpus
    means the same thing against all three fixtures.
    """
    root = tmp_path_factory.mktemp("index_predating_a_label_edit")
    _labelled_repository(_RES_A_LABELS).build_repo(root)
    _create_contents_db(build_filesystem_test_protocol(root))
    return _labelled_repository(
        {**_RES_A_LABELS, "domain": "gamma"}).build_repo(root)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # The value the resource carries now, which the index does not
        # know. Answering the clause out of the recorded value drops the
        # resource from the indexed route while the Python route returns
        # it -- a false negative, and a row no post-filter is reached to
        # restore.
        ('scores/*[domain="gamma"]', {"scores/res_a"}),
        # The value the index recorded, which the resource no longer
        # carries. Answering the clause out of the column returns it
        # anyway -- a resource that does not satisfy the query. Unlike
        # gain#634 this direction is a false *positive*.
        ('scores/*[domain="alpha"]', set()),
    ],
)
def test_a_label_edited_after_the_index_is_matched_on_its_live_value(
    index_predating_a_label_edit: GenomicResourceProtocolRepo,
    query: str, expected: set[str],
) -> None:
    """A clause is answered out of the resource, not out of the column.

    ``scores/res_a`` says ``domain: gamma`` now; the published index still
    records ``alpha``. Both routes must read the live value, and they
    diverge in both directions if either reads the column (gain#646).
    """
    without_index = {
        r.resource_id
        for r in index_predating_a_label_edit.search_resources(
            resource_query=query)
    }
    through_index = {
        r.resource_id
        for r in index_predating_a_label_edit.search_resources(
            resource_query=query, resource_type="position_score")
    }

    # Pinned as well as compared: the sets say what the live value is, so
    # this cannot pass on two routes that agree and are both empty.
    assert without_index == expected
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


# The clauses that only an index predating a label *edit* can be asked.
# ``domain`` is a key the published index does have a column for, and the
# resource carries a different value now than the one it recorded. These
# name the value the resource carries now, which the index does not know;
# the recorded value is asked about by ``QUERY_CORPUS``, which the test
# below splats in alongside these, so a clause on ``alpha`` belongs there
# rather than here.
_EDITED_LABEL_QUERIES = [
    '*[domain="gamma"]',
    '*[domain="gam*"]',
    '*["gamma" in domain]',
    'scores/*[domain="gamma"]',
    '*[domain="gamma" and note="*"]',
    '*[domain="alpha" and target="TF1"]',
]


@pytest.mark.parametrize("resource_type", ["position_score", "genome"])
@pytest.mark.parametrize("query", [*QUERY_CORPUS, *_EDITED_LABEL_QUERIES])
def test_an_edited_label_does_not_change_what_a_query_means(
    index_predating_a_label_edit: GenomicResourceProtocolRepo,
    query: str, resource_type: str,
) -> None:
    """The same differential, over an index that recorded another value.

    ``index_predating_a_label`` can only fall behind by a key the index
    has no column for at all, which is the gain#634 shape. A column that
    exists and disagrees with the resource is the other half, and it
    diverges in both directions: the live value is a row the indexed route
    never yields, and the recorded value is a row it yields that does not
    satisfy the query (gain#646).
    """
    accepted = equivalent_resource_types(resource_type)
    without_index = {
        r.resource_id
        for r in index_predating_a_label_edit.search_resources(
            resource_query=query)
        if r.get_type() in accepted
    }
    through_index = {
        r.resource_id
        for r in index_predating_a_label_edit.search_resources(
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

    Since gain#646 no label key reaches the statement at all, so such a
    key is simply the name of a label no resource carries; this pins that
    it stays that way.
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
    first = proto.open_repository_metadata()
    second = proto.open_repository_metadata()

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


def _repository_labelling_its_middle_resource(labels: Any) -> GRRBuilder:
    """A repository whose middle resource declares ``labels`` verbatim.

    Two well-formed resources around that one: the resources are
    walked in sorted order, so an isolation claim tested with the survivor
    only ever before the offender passes without proving much.
    """
    return (
        a_grr()
        .with_resource(
            "scores/aaa", a_position_score().with_labels(domain="alpha"))
        .with_resource(
            "scores/broken", a_position_score().with_raw_labels(labels))
        .with_resource(
            "scores/zzz", a_position_score().with_labels(domain="alpha"))
    )


@pytest.mark.parametrize("broken_labels", ["some text", ["a", "b"], 2019])
def test_a_query_only_label_search_survives_a_non_mapping_labels(
    tmp_path: pathlib.Path,
    broken_labels: Any,
) -> None:
    """The route that opens no index reads the labels of every resource.

    A ``meta.labels`` that is not a mapping used to reach
    ``LabelClause.matches_in`` as whatever the curator wrote and take the
    whole listing down with a bare ``AttributeError`` naming neither the
    resource nor the misconfiguration (gain#654). The malformed resource
    matches no label clause; the rest of the repository still answers.
    """
    repo = _repository_labelling_its_middle_resource(
        broken_labels).build_repo(tmp_path)

    resources = list(repo.search_resources(resource_query='*[domain="alpha"]'))

    assert {r.resource_id for r in resources} == {"scores/aaa", "scores/zzz"}


@pytest.fixture
def index_predating_a_non_mapping_labels(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """A repository whose index predates one resource's labels breaking.

    The index build refuses a resource whose ``meta.labels`` is not a
    mapping -- the position-score implementation runs the base schema --
    so an index built after the edit simply does not name it, and the
    indexed route never reads its labels. Indexing first and breaking the
    labels afterwards is the shape a GRR really has between a curator's
    edit and the next ``grr_manage`` run, and the only one that puts the
    malformed resource in front of the deferred label clauses (gain#634).
    """
    _repository_labelling_its_middle_resource(
        {"domain": "alpha"}).build_repo(tmp_path)
    _create_contents_db(build_filesystem_test_protocol(tmp_path))
    return _repository_labelling_its_middle_resource("some text").build_repo(
        tmp_path)


def test_the_indexed_route_survives_a_non_mapping_labels(
    index_predating_a_non_mapping_labels: GenomicResourceProtocolRepo,
) -> None:
    """Every label clause is deferred to the resource's live labels.

    Since gain#634 the indexed route asks the resource rather than the
    index column, so it reaches the same read the query-only route does
    and must survive the same malformed value.
    """
    resources = list(
        index_predating_a_non_mapping_labels.search_resources(
            resource_query='*[domain="alpha"]',
            resource_type="position_score"),
    )

    assert {r.resource_id for r in resources} == {"scores/aaa", "scores/zzz"}


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
