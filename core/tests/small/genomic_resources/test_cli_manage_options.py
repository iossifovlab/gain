# pylint: disable=W0621,C0114,C0116,W0212
"""How grr_manage's parsed options reach its command functions (gain#1657).

The command functions take the options they use as keyword-only parameters,
never as ``kwargs.get(key, fallback)`` reads -- a fallback that disagreed
with its flag (``region_size``: 3 Mb against a 3 Gb flag) chunked the
statistics graph a thousandfold finer, silently.
"""
import argparse
import inspect
import pathlib
from typing import Any

import pytest
from gain.genomic_resources import cli, register_implementation
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.resource_implementation import (
    DEFAULT_STATISTICS_REGION_SIZE,
)
from gain.genomic_resources.testing import setup_directories
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskDesc, TaskGraph

from .test_cli_stats import SomeTestImplementation

# The region sizes the statistics build was asked for, in call order.
_REQUESTED_REGION_SIZES: list[int] = []


class RegionSizeRecorder(SomeTestImplementation):
    def create_statistics_build_tasks(
        self, *,
        region_size: int = DEFAULT_STATISTICS_REGION_SIZE,
        grr: GenomicResourceRepo | None = None,
    ) -> list[TaskDesc]:
        _REQUESTED_REGION_SIZES.append(region_size)
        return super().create_statistics_build_tasks(
            region_size=region_size, grr=grr)


@pytest.fixture
def region_size_recorder_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    def build(resource: GenomicResource) -> RegionSizeRecorder:
        return RegionSizeRecorder(resource)

    register_implementation("region_size_recorder", build)
    _REQUESTED_REGION_SIZES.clear()
    setup_directories(tmp_path, {
        "one": {
            GR_CONF_FILE_NAME: "type: region_size_recorder\n",
        },
    })
    return tmp_path


@pytest.mark.parametrize("command", ["repo-info", "repo-repair"])
def test_the_info_and_repair_commands_build_with_the_default_region_size(
    region_size_recorder_repo: pathlib.Path, command: str,
) -> None:
    cli.cli_manage([command, "-R", str(region_size_recorder_repo), "-j", "1"])

    assert _REQUESTED_REGION_SIZES == [DEFAULT_STATISTICS_REGION_SIZE]


@pytest.mark.parametrize("command", ["repo-info", "resource-info"])
def test_the_info_commands_take_a_region_size(
    region_size_recorder_repo: pathlib.Path, command: str,
) -> None:
    cli.cli_manage([
        command, "-R", str(region_size_recorder_repo), "-r", "one",
        "-j", "1", "--region-size", "5"])

    assert _REQUESTED_REGION_SIZES == [5]


def test_the_task_graph_options_reach_the_task_graph_and_grr_manages_do_not(
    region_size_recorder_repo: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_log_dir = str(tmp_path_factory.mktemp("task-logs"))
    received: list[dict[str, Any]] = []
    process_graph = TaskGraphCli.process_graph

    def spy(graph: TaskGraph, **kwargs: Any) -> bool:
        received.append(kwargs)
        return process_graph(graph, **kwargs)

    monkeypatch.setattr(TaskGraphCli, "process_graph", spy)

    cli.cli_manage([
        "repo-stats", "-R", str(region_size_recorder_repo),
        "-j", "1", "--task-log-dir", task_log_dir])

    [kwargs] = received
    assert kwargs["jobs"] == 1
    assert kwargs["task_log_dir"] == task_log_dir
    # Nothing but what TaskGraphCli registers, plus what the whole stack
    # shares (verbose) and what the caller sets itself -- so no grr_manage
    # option leaks, today's (extra_args can carry a remote repository's
    # credentials) or a future one.
    task_graph_parser = argparse.ArgumentParser()
    TaskGraphCli.add_arguments(
        task_graph_parser, use_commands=False, task_progress_mode=False)
    task_graph_keys = vars(task_graph_parser.parse_args([])).keys()
    assert kwargs.keys() - task_graph_keys == {
        "command", "task_progress_mode", "verbose"}
    assert "extra_args" not in kwargs


def test_the_stats_core_has_no_default_region_size() -> None:
    signature = inspect.signature(cli._run_stats_core)

    with pytest.raises(TypeError, match="region_size"):
        signature.bind(
            None, None, [],
            dry_run=True, force=False, use_dvc=True, task_graph_args={})
