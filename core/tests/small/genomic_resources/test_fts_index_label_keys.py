# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
import textwrap

import pytest
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.fsspec_protocol import FsspecReadWriteProtocol
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    convert_to_tab_separated,
    setup_directories,
)

INJECTION_KEY = "a); DROP TABLE contents_metadata; --"


def _resource_content(labels: dict[str, str]) -> dict[str, str]:
    labels_yaml = "\n".join(
        f'            "{key}": "{value}"' for key, value in labels.items()
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
