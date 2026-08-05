# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
import textwrap
from itertools import starmap

import pytest
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
    SearchIndexUnavailableError,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    INDEX_COLUMN_PATTERN,
    MAX_INDEX_COLUMNS,
    merge_index_columns,
    validate_index_columns,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_inmemory_test_resource,
    convert_to_tab_separated,
    setup_directories,
)

INJECTION_KEY = "a); DROP TABLE contents_metadata; --"


# A str subclass is the point here: the marker has to survive being used
# as a dict key next to plain string keys.
class RawKey(str):  # noqa: FURB189
    """A label key to write into the YAML verbatim, unquoted.

    YAML mapping keys need not be strings -- `2024:` is an int key, `true:`
    a bool -- and quoting is exactly what makes the difference.
    """


def _label_line(key: str, value: str) -> str:
    spelling = key if isinstance(key, RawKey) else f'"{key}"'
    return f'            {spelling}: "{value}"'


def _resource_content(labels: dict[str, str]) -> dict[str, str]:
    labels_yaml = "\n".join(
        starmap(_label_line, labels.items()),
    )
    return {
        "genomic_resource.yaml": textwrap.dedent("""
            type: position_score
            meta:
                description: Example position score
                summary: Example summary
                labels:
        """) + labels_yaml + textwrap.dedent("""
            table:
                filename: data.txt
            scores:
                - id: score
                  type: float
                  name: score
        """),
        "data.txt": convert_to_tab_separated("""
            chrom  pos_begin  score
            chr1   100        1.5
        """),
    }


def _non_score_resource_content(labels: dict[str, str]) -> dict[str, str]:
    """A resource whose implementation contributes no score fields.

    The score fields are the ones a *score* implementation adds on top of
    the base header, so a resource that is not a score is the only one that
    can reach the index carrying a label spelled like one (gain#542).
    """
    labels_yaml = "\n".join(starmap(_label_line, labels.items()))
    return {
        "genomic_resource.yaml": textwrap.dedent("""
            type: genome
            filename: chr.fa
            meta:
                description: Example genome
                labels:
        """) + labels_yaml,
        "chr.fa": convert_to_tab_separated("""
            >chr1
            NNACCCAAAC
        """),
        "chr.fa.fai": "chr1\t10\t7\t10\t11\n",
    }


def build_grr(
    tmp_path: pathlib.Path,
    resources: dict[str, dict[str, str]],
) -> FsspecReadWriteProtocol:
    """Build a small filesystem GRR with the given resource id -> labels."""
    setup_directories(
        tmp_path,
        {
            resource_id: _resource_content(labels)
            for resource_id, labels in resources.items()
        },
    )
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    return build_filesystem_test_protocol(tmp_path, repair=False)


def build_mixed_grr(
    tmp_path: pathlib.Path,
    scores: dict[str, dict[str, str]],
    non_scores: dict[str, dict[str, str]],
) -> FsspecReadWriteProtocol:
    """Build a GRR holding both score and non-score resources.

    The score fields are only in the index because a score implementation
    put them there, so telling the two families apart is what makes the
    shared-column case reachable (gain#542).
    """
    # The two families share one id space; letting a collision through
    # would silently drop a resource the caller thinks it built.
    assert not (scores.keys() & non_scores.keys())
    setup_directories(
        tmp_path,
        {
            **{
                resource_id: _resource_content(labels)
                for resource_id, labels in scores.items()
            },
            **{
                resource_id: _non_score_resource_content(labels)
                for resource_id, labels in non_scores.items()
            },
        },
    )
    cli_manage(["repo-manifest", "-R", str(tmp_path)])
    return build_filesystem_test_protocol(tmp_path, repair=False)


def test_label_key_cannot_inject_sql_into_the_index(
    tmp_path: pathlib.Path,
) -> None:
    proto = build_grr(tmp_path, {
        "benign": {"ref_genome": "hg38"},
        "evil": {INJECTION_KEY: "boom"},
    })

    failed = _create_contents_db(proto)

    assert failed == {"evil"}

    # The injected statement must not have run.
    conn = proto.open_repository_sqlite3_metadata_db()
    with conn:
        row = conn.execute(
            "SELECT value FROM contents_metadata "
            "WHERE key = 'contents_md5'",
        ).fetchone()
    assert row is not None

    # ... and the rest of the repository is still indexed.
    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="hg38")
    ] == ["benign"]


@pytest.mark.parametrize("label_key", [
    "full_id", "id", "type", "description", "summary",  # the fixed fields
    "score_ids",  # a field this resource type adds itself
    "ID",  # SQLite column names are case-insensitive, so this collides too
])
def test_label_key_colliding_with_a_fixed_field_fails_its_resource(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    label_key: str,
) -> None:
    # A label key that repeats a field of the index would replace that
    # field's value for the resource -- a resource could label itself
    # `id: something_else` and answer to a search for a name that is not
    # its own.  Rejected, and only that resource is lost.
    proto = build_grr(tmp_path, {
        "benign": {"ref_genome": "hg38"},
        "evil": {label_key: "boom"},
    })

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"evil"}
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(messages) == 1
    assert "<evil>" in messages[0]
    assert f"<{label_key}>" in messages[0]

    repo = GenomicResourceProtocolRepo(proto)
    # The benign resource keeps its own fixed fields.
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="benign")
    ] == ["benign"]


def test_repo_repair_reports_the_bad_label_key_and_indexes_the_rest(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # End to end: the offending resource must not cost the repository its
    # index, and the run must still fail, naming it.
    build_grr(tmp_path, {
        "benign": {"ref_genome": "hg38"},
        "evil": {"cell-type": "liver"},
    })

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])

    assert excinfo.value.code != 0
    assert any(
        "<evil>" in record.getMessage() and "<cell-type>" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    )

    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="hg38")
    ] == ["benign"]


def test_valid_label_keys_are_searchable_index_fields(
    tmp_path: pathlib.Path,
) -> None:
    proto = build_grr(tmp_path, {
        "one": {"ref_genome": "hg38", "assay": "chip"},
        "two": {"ref_genome": "hg19", "assay": "chip"},
    })

    assert _create_contents_db(proto) == frozenset()

    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="ref_genome : hg38")
    ] == ["one"]
    assert sorted(
        res.resource_id
        for res in repo.search_resources(search_term="assay : chip")
    ) == ["one", "two"]


@pytest.mark.parametrize("label_key", [
    "ref-genome",       # hyphen
    "reference genome",  # space
    "order",            # bare SQL keyword
    # `key` is a non-reserved keyword -- SQLite would take it as a column
    # name.  Refused all the same: the whole keyword list is refused, on
    # purpose, since which keywords are reserved is a property of the
    # SQLite build rather than of this rule.
    "key",
    "rank",             # reserved by FTS5
    "rowid",            # reserved by FTS5
    "contents",         # the index table's own hidden column
    INJECTION_KEY,
])
def test_unusable_label_key_fails_only_its_own_resource(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    label_key: str,
) -> None:
    proto = build_grr(tmp_path, {
        "benign": {"ref_genome": "hg38"},
        "evil": {label_key: "boom"},
    })

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"evil"}

    # The failure names the resource AND the offending key, and points at
    # the rule.
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(messages) == 1
    assert "<evil>" in messages[0]
    assert f"<{label_key}>" in messages[0]
    assert "[A-Za-z_][A-Za-z0-9_]*" in messages[0]

    # The repository index is still built, and the benign resource is in it.
    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="hg38")
    ] == ["benign"]


def test_label_keys_differing_only_in_case_across_resources(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Each resource's own labels are fine; it is the pair that SQLite
    # cannot accept, since it compares column names case-insensitively.
    proto = build_grr(tmp_path, {
        "alpha": {"assay": "chip"},
        "zeta": {"Assay": "rna"},
    })

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"zeta"}

    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(messages) == 1
    # The report names the rejected resource, its key, and the counterpart
    # that already owns the name.
    assert "<zeta>" in messages[0]
    assert "<Assay>" in messages[0]
    assert "<alpha>" in messages[0]
    assert "<assay>" in messages[0]

    # The repository still has an index, and the rest of it is in there.
    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="assay : chip")
    ] == ["alpha"]


def test_which_of_two_case_colliding_resources_is_kept_is_by_resource_id(
    tmp_path: pathlib.Path,
) -> None:
    # The same pair of spellings, swapped between the two resources: the
    # resource that keeps the field is the first by resource id either way,
    # never whichever one the repository happened to list first.
    proto = build_grr(tmp_path, {
        "alpha": {"Assay": "chip"},
        "zeta": {"assay": "rna"},
    })

    assert _create_contents_db(proto) == {"zeta"}

    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="Assay : chip")
    ] == ["alpha"]


def test_repo_repair_survives_a_cross_resource_case_collision(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # End to end: a collision must cost the repository the offending
    # resource, not its whole index -- _create_contents_db removes the
    # published index before rebuilding it, so an escaping error would
    # leave the repository with no index at all.
    build_grr(tmp_path, {
        "alpha": {"assay": "chip"},
        "zeta": {"Assay": "rna"},
    })

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])

    assert excinfo.value.code != 0
    assert any(
        "<zeta>" in record.getMessage() and "<alpha>" in record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    )

    assert (tmp_path / ".CONTENTS.sqlite3.gz").exists()
    # ... and not the half-built uncompressed one an escaping error leaves.
    assert not (tmp_path / ".CONTENTS.sqlite3").exists()

    proto = build_filesystem_test_protocol(tmp_path, repair=False)
    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="chip")
    ] == ["alpha"]


def test_repository_with_too_many_distinct_label_keys(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An FTS5 table has a hard ceiling on its columns, and the index's
    # columns are the union over the whole repository -- so enough label
    # keys, spread over resources that are each perfectly fine, would make
    # the index unbuildable.  The resources past the ceiling are dropped,
    # by resource id, instead of taking the index down.
    proto = build_grr(tmp_path, {
        "aaa": {f"k{i:04d}": "v" for i in range(1000)},
        "zzz": {f"m{i:04d}": "v" for i in range(1000)},
    })

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"zzz"}
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(messages) == 1
    assert "<zzz>" in messages[0]

    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="k0500 : v")
    ] == ["aaa"]


def test_repository_with_the_most_label_keys_fts5_allows(
    tmp_path: pathlib.Path,
) -> None:
    # The other side of the ceiling: a repository exactly at it must still
    # be indexed.  This pins MAX_INDEX_COLUMNS against what SQLite really
    # accepts -- 5 fixed fields plus the two a position_score contributes.
    labels = {
        f"k{i:04d}": "v"
        for i in range(MAX_INDEX_COLUMNS - 7)
    }
    proto = build_grr(tmp_path, {"aaa": labels})

    assert _create_contents_db(proto) == frozenset()

    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="k0500 : v")
    ] == ["aaa"]


@pytest.mark.parametrize("raw_key", [
    "2024",   # an int key -- `2024: release` is a plausible thing to write
    "true",   # a bool key
    "1.5",    # a float key
    "null",   # a None key
])
def test_non_string_label_key_is_reported_as_a_bad_key(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    raw_key: str,
) -> None:
    # A YAML key that is not a string cannot name a field either -- and the
    # curator must be told the rule, not handed the internal error of a
    # regex refusing a non-string (gain#364).
    proto = build_grr(tmp_path, {
        "benign": {"ref_genome": "hg38"},
        "evil": {RawKey(raw_key): "boom"},
    })

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"evil"}

    errors = [
        record for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "<evil>" in message
    assert "unexpected internal error" not in message
    assert "[A-Za-z_][A-Za-z0-9_]*" in message
    # A named cause is reported, not a stack trace.
    assert errors[0].exc_info is None

    repo = GenomicResourceProtocolRepo(proto)
    assert [
        res.resource_id
        for res in repo.search_resources(search_term="hg38")
    ] == ["benign"]


def _build_resource(labels: dict[str, str]) -> GenomicResource:
    return build_inmemory_test_resource(_resource_content(labels))


def _build_non_score_resource(labels: dict[str, str]) -> GenomicResource:
    return build_inmemory_test_resource(_non_score_resource_content(labels))


def test_collect_index_info_refuses_a_score_field_as_a_label_key() -> None:
    # A non-score resource's own header has no score fields, so nothing in
    # it repeats `score_ids` -- but the index table's columns are the union
    # across the repository, so the label would land in the very column
    # score resources fill with their score-id list (gain#542).
    impl = build_resource_implementation(
        _build_non_score_resource({"score_ids": "mylabel"}))

    with pytest.raises(ValueError, match="cannot index resource") as excinfo:
        impl.collect_index_info()

    assert "<score_ids>" in str(excinfo.value)


def test_a_refused_score_field_label_names_the_reserved_fields() -> None:
    # The curator's resource has no field of its own called `score_ids`, so
    # "repeats a field the index already has" does not on its own tell them
    # why their genome was dropped.  The message has to enumerate the names
    # the index reserves, the way it already enumerates the FTS5 ones.
    impl = build_resource_implementation(
        _build_non_score_resource({"score_ids": "mylabel"}))

    with pytest.raises(ValueError) as excinfo:
        impl.collect_index_info()

    message = str(excinfo.value)
    for reserved in ("full_id", "score_ids", "score_descriptions"):
        assert reserved in message


def test_collect_index_info_returns_the_label_keys_as_fields() -> None:
    impl = build_resource_implementation(
        _build_resource({"ref_genome": "hg38"}))

    header, row = impl.collect_index_info()

    assert header[:5] == ("full_id", "id", "type", "description", "summary")
    assert "ref_genome" in header
    assert row[header.index("ref_genome")] == "hg38"


@pytest.mark.parametrize("label_key", [
    "ref-genome",
    "order",
    "rank",
    "contents",
    "summary",
    INJECTION_KEY,
])
def test_collect_index_info_refuses_a_label_key_it_cannot_index(
    label_key: str,
) -> None:
    # collect_index_info() is where every implementation reaches the index,
    # and so where the rule is enforced -- the index build's own re-check is
    # a second line of defence, not the only one.
    impl = build_resource_implementation(_build_resource({label_key: "boom"}))

    with pytest.raises(ValueError, match="cannot index resource") as excinfo:
        impl.collect_index_info()

    assert f"<{label_key}>" in str(excinfo.value)
    assert INDEX_COLUMN_PATTERN in str(excinfo.value)


@pytest.mark.parametrize("label_key", ["score_ids", "score_descriptions"])
def test_a_non_score_resource_cannot_label_itself_with_a_score_field(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    label_key: str,
) -> None:
    # The index has one column per name for the whole repository, so this
    # label would land in the column the score resource fills with its
    # score-id list -- one column meaning a label for one resource and a
    # resource field for another.  Rejected, and only that resource is
    # lost (gain#542).
    # A good resource on either side of the bad one: resources are walked
    # in filesystem order, so an isolation claim tested with the survivor
    # only ever before the offender passes without proving much.
    proto = build_mixed_grr(
        tmp_path,
        scores={
            "aaa_benign": {"ref_genome": "hg38"},
            "zzz_benign": {"ref_genome": "hg19"},
        },
        non_scores={"evil": {label_key: "boom"}},
    )

    with caplog.at_level(logging.ERROR):
        failed = _create_contents_db(proto)

    assert failed == {"evil"}
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert len(messages) == 1
    assert "<evil>" in messages[0]
    assert f"<{label_key}>" in messages[0]

    # Losing the offender must not cost the repository anything else.
    repo = GenomicResourceProtocolRepo(proto)
    assert {
        res.resource_id
        for res in repo.search_resources(search_term="benign")
    } == {"aaa_benign", "zzz_benign"}


def test_a_non_score_resource_keeps_labels_that_collide_with_nothing(
    tmp_path: pathlib.Path,
) -> None:
    # The refusal must not spread to ordinary labels.  `reference_genome`
    # is carried by real resources in the published repositories, so it is
    # the spelling that would hurt most to lose.
    proto = build_mixed_grr(
        tmp_path,
        scores={"benign": {"ref_genome": "hg38"}},
        non_scores={"genome": {"reference_genome": "hg38"}},
    )

    assert _create_contents_db(proto) == frozenset()

    repo = GenomicResourceProtocolRepo(proto)
    assert {
        res.resource_id
        for res in repo.search_resources(
            resource_query='*[reference_genome="hg38"]',
            resource_type="genome")
    } == {"genome"}


def test_collect_index_info_refuses_a_label_key_that_is_not_a_string() -> None:
    impl = build_resource_implementation(
        _build_resource({RawKey("2024"): "release"}))

    with pytest.raises(ValueError, match="is not a string but int"):
        impl.collect_index_info()


def test_validate_index_columns_accepts_the_fields_the_live_grr_uses() -> None:
    # The keys the production repositories carry today, plus the fields the
    # implementations add themselves -- none of them may become unindexable.
    validate_index_columns("res", [
        "full_id", "id", "type", "description", "summary",
        "score_ids", "score_descriptions",
        "reference_genome", "accession", "status", "assay_term_name",
        "simple_biosample_summary", "biosample_summary", "replication_type",
        "biosample_ontology", "perturbed", "doi", "date_created",
        "date_released", "submitter_comment", "target", "gene_models",
        "source_genome", "target_genome",
    ])


@pytest.mark.parametrize(("columns", "expected"), [
    (["id", "cell-type"], "is not a valid SQL identifier"),
    (["id", "order"], "is an SQL keyword"),
    (["id", "rank"], "is a name FTS5 reserves"),
    # A repeat of a name the index keeps for a resource field is reported
    # as such -- the curator may have no field of that name to look at.
    (["id", "ID"], "is a name the index reserves for a resource field"),
    # A repeat of anything else is a plain collision between two labels.
    (["assay", "Assay"], "repeats a field the index already has"),
    (["id", 2024], "is not a string but int"),
])
def test_validate_index_columns_names_the_resource_and_the_column(
    columns: list[str], expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected) as excinfo:
        validate_index_columns("res", columns)

    assert "<res>" in str(excinfo.value)
    assert f"<{columns[1]}>" in str(excinfo.value)


def test_merge_index_columns_shares_a_field_two_resources_spell_alike(
) -> None:
    claimed = merge_index_columns("alpha", ["id", "assay"], {})

    merged = merge_index_columns("zeta", ["id", "assay", "doi"], claimed)

    assert [spelling for spelling, _ in merged.values()] == [
        "id", "assay", "doi"]
    assert merged["assay"] == ("assay", "alpha")


def test_merge_index_columns_refuses_a_field_spelled_two_ways() -> None:
    claimed = merge_index_columns("alpha", ["id", "assay"], {})

    with pytest.raises(ValueError, match="differs only in case") as excinfo:
        merge_index_columns("zeta", ["id", "Assay"], claimed)

    assert "<zeta>" in str(excinfo.value)
    assert "<Assay>" in str(excinfo.value)
    assert "<alpha>" in str(excinfo.value)
    assert "<assay>" in str(excinfo.value)
    # The rejected resource claimed nothing.
    assert list(claimed) == ["id", "assay"]


def test_searching_a_repository_whose_index_holds_nothing(
    tmp_path: pathlib.Path,
) -> None:
    # Every resource rejected leaves an index with no searchable fields at
    # all -- newly reachable from ordinary bad YAML.  Searching it must say
    # why, rather than leak SQLite's "no such table".
    #
    # It must also not answer with an empty result: this repository has not
    # applied the filter, and coming back empty is indistinguishable from
    # having applied it and matched nothing.  A group repository needs that
    # distinction to tell a child that matched nothing from one that was
    # never searched (ADR 0012).
    proto = build_grr(tmp_path, {
        "evil": {"cell-type": "liver"},
    })
    assert _create_contents_db(proto) == {"evil"}

    repo = GenomicResourceProtocolRepo(proto)

    # Both filters route through the index, so both have to say so.
    with pytest.raises(SearchIndexUnavailableError) as excinfo:
        list(repo.search_resources(resource_type="position_score"))
    assert "no resource could be indexed" in str(excinfo.value)

    with pytest.raises(SearchIndexUnavailableError) as excinfo:
        list(repo.search_resources(search_term="liver"))
    assert "no resource could be indexed" in str(excinfo.value)
