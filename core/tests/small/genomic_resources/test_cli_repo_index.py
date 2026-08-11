# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
import shutil
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
)
from gain.genomic_resources.score_implementation import ScoreImplementationBase
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)

GLOBAL_ARTIFACTS = (
    GR_CONTENTS_FILE_NAME,        # .CONTENTS.json.gz
    GR_CONTENTS_FILE_NAME[:-3],   # .CONTENTS.json
    GR_SQLITE_META_FILE_NAME,     # .CONTENTS.sqlite3.gz
    GR_INDEX_FILE_NAME,           # index.html
)


@pytest.fixture
def repaired_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A two-score repository with manifests, statistics and globals built."""
    repo = a_grr()
    for rid in ("sub/one", "sub/two"):
        repo = repo.with_resource(
            rid,
            a_position_score()
            .with_score("phastCons", "float")
            .with_data("""
                chrom  pos_begin  phastCons
                1      10         0.1
                1      11         0.2
            """),
        )
    repo.build_repo(tmp_path)
    cli_manage(["repo-repair", "-R", str(tmp_path), "-j", "1"])
    return tmp_path


def test_repo_index_rebuilds_the_repository_globals(
    repaired_repo: pathlib.Path,
) -> None:
    for artifact in GLOBAL_ARTIFACTS:
        (repaired_repo / artifact).unlink()

    cli_manage(["repo-index", "-R", str(repaired_repo)])

    for artifact in GLOBAL_ARTIFACTS:
        assert (repaired_repo / artifact).exists(), artifact
    contents = (repaired_repo / GR_CONTENTS_FILE_NAME[:-3]).read_text()
    assert "sub/one" in contents


def test_repo_index_skips_a_manifestless_resource_without_writing_it(
    repaired_repo: pathlib.Path,
) -> None:
    naked_dir = repaired_repo / "sub" / "naked"
    naked_dir.mkdir()
    (naked_dir / "genomic_resource.yaml").write_text("{}\n")

    with pytest.raises(SystemExit) as exit_info:
        cli_manage(["repo-index", "-R", str(repaired_repo)])

    assert exit_info.value.code == 1
    assert not (naked_dir / ".MANIFEST").exists()
    contents = (repaired_repo / GR_CONTENTS_FILE_NAME[:-3]).read_text()
    assert "sub/one" in contents
    assert "sub/naked" not in contents


def test_repo_index_republishes_after_the_last_resource_is_deleted(
    repaired_repo: pathlib.Path,
) -> None:
    shutil.rmtree(repaired_repo / "sub")
    assert "sub/one" in (
        repaired_repo / GR_CONTENTS_FILE_NAME[:-3]).read_text()

    cli_manage(["repo-index", "-R", str(repaired_repo)])

    contents = (repaired_repo / GR_CONTENTS_FILE_NAME[:-3]).read_text()
    assert "sub/one" not in contents


def test_repo_index_blames_the_resource_the_fts_walk_failed_on(
    repaired_repo: pathlib.Path,
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
    (repaired_repo / GR_SQLITE_META_FILE_NAME).unlink()

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit) as excinfo:
        cli_manage(["repo-index", "-R", str(repaired_repo)])

    assert excinfo.value.code != 0
    failures = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert any("<sub/two>" in message for message in failures)
    assert not any("<sub/one>" in message for message in failures)
