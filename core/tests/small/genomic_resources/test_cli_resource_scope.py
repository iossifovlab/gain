# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_MANIFEST_FILE_NAME,
)
from gain.genomic_resources.score_implementation import ScoreImplementationBase

from .conftest import GLOBAL_ARTIFACTS


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
    caplog: pytest.LogCaptureFixture,
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

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage([
            "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
            "-j", "1"])

    assert not any(
        "<sub/two>" in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR
    )


def test_a_resource_repair_that_wrote_notes_the_stale_repository_index(
    settled_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    touch_resource_config(settled_repo, "sub/one")

    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage([
            "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
            "-j", "1"])

    assert "repo-index" in caplog.text


def test_a_resource_repair_with_nothing_to_do_does_not_note_the_index(
    settled_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="grr_manage"):
        cli_manage([
            "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
            "-j", "1"])

    assert "repo-index" not in caplog.text


def test_a_dry_run_resource_repair_does_not_note_the_index(
    settled_repo: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    touch_resource_config(settled_repo, "sub/one")

    with caplog.at_level(logging.INFO, logger="grr_manage"), \
            pytest.raises(SystemExit):
        cli_manage([
            "resource-repair", "-R", str(settled_repo), "-r", "sub/one",
            "-n", "-j", "1"])

    assert "repo-index" not in caplog.text


def test_resource_stats_leaves_the_repository_globals_untouched(
    settled_repo: pathlib.Path,
) -> None:
    touch_resource_config(settled_repo, "sub/one")
    globals_before = snapshot_globals(settled_repo)

    cli_manage([
        "resource-stats", "-R", str(settled_repo), "-r", "sub/one",
        "-j", "1"])

    assert snapshot_globals(settled_repo) == globals_before


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
