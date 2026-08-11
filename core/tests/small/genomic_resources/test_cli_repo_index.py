# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
)
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
    """A one-score repository with manifests, statistics and globals built."""
    (
        a_grr()
        .with_resource(
            "sub/one",
            a_position_score()
            .with_score("phastCons", "float")
            .with_data("""
                chrom  pos_begin  phastCons
                1      10         0.1
                1      11         0.2
            """),
        )
        .build_repo(tmp_path)
    )
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
    two_dir = repaired_repo / "sub" / "two"
    two_dir.mkdir()
    (two_dir / "genomic_resource.yaml").write_text("{}\n")

    with pytest.raises(SystemExit) as exit_info:
        cli_manage(["repo-index", "-R", str(repaired_repo)])

    assert exit_info.value.code == 1
    assert not (two_dir / ".MANIFEST").exists()
    contents = (repaired_repo / GR_CONTENTS_FILE_NAME[:-3]).read_text()
    assert "sub/one" in contents
    assert "sub/two" not in contents
