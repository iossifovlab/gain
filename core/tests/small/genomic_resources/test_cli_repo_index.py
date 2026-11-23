# pylint: disable=W0621,C0114,C0116,W0212,W0613
import gzip
import json
import logging
import pathlib
from typing import Any

import apsw
import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository_ids import GR_SQLITE_META_FILE_NAME
from gain.genomic_resources.resource_query import label_alternatives
from gain.genomic_resources.score_implementation import ScoreImplementationBase
from gain.genomic_resources.testing.builders import a_basic_resource, a_grr

from .conftest import (
    GLOBAL_ARTIFACTS,
    empty_the_repository,
    read_published_contents,
)


def test_repo_index_rebuilds_the_repository_globals(
    settled_repo: pathlib.Path,
) -> None:
    for artifact in GLOBAL_ARTIFACTS:
        (settled_repo / artifact).unlink()

    cli_manage(["repo-index", "-R", str(settled_repo)])

    for artifact in GLOBAL_ARTIFACTS:
        assert (settled_repo / artifact).exists(), artifact
    contents = read_published_contents(settled_repo)
    assert "sub/one" in contents


def test_repo_index_skips_a_manifestless_resource_without_writing_it(
    settled_repo: pathlib.Path,
) -> None:
    naked_dir = settled_repo / "sub" / "naked"
    naked_dir.mkdir()
    (naked_dir / "genomic_resource.yaml").write_text("{}\n")

    with pytest.raises(SystemExit) as exit_info:
        cli_manage(["repo-index", "-R", str(settled_repo)])

    assert exit_info.value.code == 1
    assert not (naked_dir / ".MANIFEST").exists()
    contents = read_published_contents(settled_repo)
    assert "sub/one" in contents
    assert "sub/naked" not in contents


def test_repo_index_republishes_after_the_last_resource_is_deleted(
    settled_repo: pathlib.Path,
) -> None:
    empty_the_repository(settled_repo)

    cli_manage(["repo-index", "-R", str(settled_repo)])

    contents = read_published_contents(settled_repo)
    assert "sub/one" not in contents


def test_repo_index_blames_the_resource_the_fts_walk_failed_on(
    settled_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The FTS index is repository-wide, so publishing it walks every
    # resource.  A failure there is real and must fail the run, but it is
    # reported under the offending resource's own id.
    real = ScoreImplementationBase.collect_index_info

    def boom(self: Any) -> Any:
        if self.resource.resource_id == "sub/two":
            raise ValueError("cannot index this")
        return real(self)

    monkeypatch.setattr(ScoreImplementationBase, "collect_index_info", boom)
    # A settled index short-circuits on its stored contents md5 and never
    # walks; drop it so the publish actually rebuilds.
    (settled_repo / GR_SQLITE_META_FILE_NAME).unlink()

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-index", "-R", str(settled_repo)])

    assert excinfo.value.code != 0
    failures = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert any("<sub/two>" in message for message in failures)
    assert not any("<sub/one>" in message for message in failures)


def _read_index(repo_path: pathlib.Path) -> apsw.Connection:
    """The published search index of ``repo_path``, opened in memory."""
    conn = apsw.Connection(":memory:")
    conn.deserialize("main", gzip.decompress(
        (repo_path / GR_SQLITE_META_FILE_NAME).read_bytes()))
    return conn


def _a_labelled_grr(repo_path: pathlib.Path) -> None:
    a_grr().with_resource(
        "atlas",
        a_basic_resource().with_labels(
            modality=["RNA", "ATAC"], tissue="basal ganglia", donors=12),
    ).build_repo(repo_path)


def test_repo_index_keeps_the_rendered_labels_of_each_resource(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_grr(tmp_path)

    cli_manage(["repo-index", "-R", str(tmp_path)])

    index = _read_index(tmp_path)
    rows = index.execute(
        "SELECT full_id, labels FROM resource_labels").fetchall()
    assert [
        (full_id, json.loads(str(labels))) for full_id, labels in rows
    ] == [
        ("atlas", {
            "modality": ["RNA", "ATAC"],
            "tissue": "basal ganglia",
            "donors": label_alternatives(12)[0],
        }),
    ]


def test_repo_index_leaves_the_fts_contents_table_as_it_was(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_grr(tmp_path)

    cli_manage(["repo-index", "-R", str(tmp_path)])

    index = _read_index(tmp_path)
    columns = [
        row[1] for row in index.execute("PRAGMA table_info(contents)")]
    assert columns == [
        "full_id", "id", "type", "description", "summary",
        "modality", "tissue", "donors",
    ]
    assert index.execute(
        "SELECT modality FROM contents").fetchall() == [("RNA ATAC",)]


def _rewrite_index(repo_path: pathlib.Path, *statements: str) -> None:
    """Run ``statements`` on the published index and publish it again."""
    index = _read_index(repo_path)
    for statement in statements:
        index.execute(statement)
    (repo_path / GR_SQLITE_META_FILE_NAME).write_bytes(
        gzip.compress(bytes(index.serialize("main"))))


def test_repo_index_rebuilds_an_index_with_no_schema_version(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_grr(tmp_path)
    cli_manage(["repo-index", "-R", str(tmp_path)])
    # What an older release published: the same `contents_md5`, no
    # version and no labels table.
    _rewrite_index(
        tmp_path,
        "DROP TABLE resource_labels",
        "DELETE FROM contents_metadata WHERE key != 'contents_md5'",
    )

    cli_manage(["repo-index", "-R", str(tmp_path)])

    index = _read_index(tmp_path)
    assert index.execute(
        "SELECT full_id FROM resource_labels").fetchall() == [("atlas",)]


def test_repo_index_rebuilds_an_index_with_an_older_schema_version(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_grr(tmp_path)
    cli_manage(["repo-index", "-R", str(tmp_path)])
    # An index that records an older version, on the same `contents_md5`
    # and with no labels table.
    _rewrite_index(
        tmp_path,
        "DROP TABLE resource_labels",
        "UPDATE contents_metadata SET value = '1' "
        "WHERE key = 'schema_version'",
    )

    cli_manage(["repo-index", "-R", str(tmp_path)])

    index = _read_index(tmp_path)
    assert index.execute(
        "SELECT full_id FROM resource_labels").fetchall() == [("atlas",)]
    assert index.execute(
        "SELECT value FROM contents_metadata WHERE key = 'schema_version'",
    ).fetchall() != [("1",)]


def test_repo_index_keeps_an_index_with_the_current_schema_version(
    tmp_path: pathlib.Path,
) -> None:
    _a_labelled_grr(tmp_path)
    cli_manage(["repo-index", "-R", str(tmp_path)])
    # A rebuild would drop this row.
    _rewrite_index(
        tmp_path,
        "INSERT INTO contents_metadata (key, value) VALUES ('kept', 'yes')",
    )

    cli_manage(["repo-index", "-R", str(tmp_path)])

    index = _read_index(tmp_path)
    assert index.execute(
        "SELECT value FROM contents_metadata WHERE key = 'kept'",
    ).fetchall() == [("yes",)]
