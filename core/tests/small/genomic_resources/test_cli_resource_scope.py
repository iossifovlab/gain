# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
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
def settled_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A two-score repository fully repaired, globals included."""
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


def snapshot_globals(path: pathlib.Path) -> dict[str, bytes]:
    return {
        artifact: (path / artifact).read_bytes()
        for artifact in GLOBAL_ARTIFACTS
    }


def touch_resource_config(path: pathlib.Path, resource_id: str) -> None:
    """Change a resource so its manifest genuinely needs an update."""
    config_path = path / resource_id / GR_CONF_FILE_NAME
    config_path.write_text(
        config_path.read_text()
        + "meta:\n  summary: an edited summary\n")


def test_resource_repair_leaves_the_repository_globals_untouched(
    settled_repo: pathlib.Path,
) -> None:
    touch_resource_config(settled_repo, "sub/one")
    manifest_before = (
        settled_repo / "sub/one" / GR_MANIFEST_FILE_NAME).read_bytes()
    globals_before = snapshot_globals(settled_repo)

    cli_manage([
        "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
        "-j", "1"])

    assert (
        settled_repo / "sub/one" / GR_MANIFEST_FILE_NAME
    ).read_bytes() != manifest_before
    assert snapshot_globals(settled_repo) == globals_before


def test_resource_repair_ignores_an_unrelated_resource_it_cannot_index(
    settled_repo: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A resource-scoped command no longer publishes the FTS index, so a
    # broken resource it never selected cannot fail -- or even be read
    # by -- the run (gain#760).
    real = ScoreImplementationBase.collect_index_info

    def boom(self: Any) -> Any:
        if self.resource.resource_id == "sub/two":
            raise ValueError("cannot index this")
        return real(self)

    monkeypatch.setattr(ScoreImplementationBase, "collect_index_info", boom)
    touch_resource_config(settled_repo, "sub/one")

    cli_manage([
        "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
        "-j", "1"])


def test_resource_manifest_leaves_the_repository_globals_untouched(
    settled_repo: pathlib.Path,
) -> None:
    touch_resource_config(settled_repo, "sub/one")
    manifest_before = (
        settled_repo / "sub/one" / GR_MANIFEST_FILE_NAME).read_bytes()
    globals_before = snapshot_globals(settled_repo)

    cli_manage([
        "resource-manifest", "-R", str(settled_repo), "-r", "sub/one"])

    assert (
        settled_repo / "sub/one" / GR_MANIFEST_FILE_NAME
    ).read_bytes() != manifest_before
    assert snapshot_globals(settled_repo) == globals_before
