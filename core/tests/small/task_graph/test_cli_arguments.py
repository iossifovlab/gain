# pylint: disable=W0621,C0114,C0116,W0212,W0613,too-many-lines
import argparse

import pytest
from gain.task_graph import TaskGraphCli


@pytest.mark.parametrize("argv,jobs", [
    (["-j", "1"], 1),
    ([], None),
    (["-j", "100"], 100),
    (["--jobs", "1"], 1),
    (["--jobs", "100"], 100),
])
def test_cli_args_jobs(argv: list[str], jobs: int | None) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.jobs == jobs


@pytest.mark.parametrize("argv,command", [
    ([], "run"),
    (["run"], "run"),
    (["list"], "list"),
    (["status"], "status"),
])
def test_cli_args_command(argv: list[str], command: str) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.command == command


@pytest.mark.parametrize("argv,name", [
    ([], None),
    (["-N", "abc"], "abc"),
    (["--dcn", "abc"], "abc"),
    (["--dask-cluster-name", "abc"], "abc"),
])
def test_cli_args_dask_cluster_name(
        argv: list[str], name: str | None) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.dask_cluster_name == name


@pytest.mark.parametrize("argv,filename", [
    ([], None),
    (["-c", "abc.yaml"], "abc.yaml"),
    (["--dccf", "abc.yaml"], "abc.yaml"),
    (["--dask-cluster-config-file", "abc.yaml"], "abc.yaml"),
])
def test_cli_args_dask_cluster_config_file(
        argv: list[str], filename: str | None) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.dask_cluster_config_file == filename


@pytest.mark.parametrize("argv,task_ids", [
    ([], None),
    (["--task-ids", "a"], ["a"]),
    (["--task-ids", "a", "b"], ["a", "b"]),
    (["--task-ids", "a", "b", "c"], ["a", "b", "c"]),
    (["-t", "a"], ["a"]),
    (["-t", "a", "b"], ["a", "b"]),
    (["-t", "a", "b", "c"], ["a", "b", "c"]),
])
def test_cli_args_task_ids(
        argv: list[str], task_ids: list[str] | None) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.task_ids == task_ids


@pytest.mark.parametrize("argv,keep_going", [
    ([], False),
    (["--keep-going"], True),
])
def test_cli_args_keep_going(
    argv: list[str],
    keep_going: bool,
) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.keep_going == keep_going


@pytest.mark.parametrize("task_progress_mode,argv,force", [
    (True, [], False),
    (True, ["--force"], True),
    (False, [], None),
])
def test_cli_args_force(
    task_progress_mode: bool,
    argv: list[str],
    force: bool | None,
) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser, task_progress_mode=task_progress_mode)
    args = vars(parser.parse_args(argv))

    assert args.get("force") == force


@pytest.mark.parametrize("default_task_status_dir, argv,task_status_dir", [
    (".", [], "."),
    ("/a/b/c", [], "/a/b/c"),
    (".", ["-d", ".status"], ".status"),
    (".", ["--tsd", ".status"], ".status"),
    (".", ["--task-status-dir", ".status"], ".status"),
])
def test_cli_args_task_status_dir(
        default_task_status_dir: str, argv: list[str],
        task_status_dir: str) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(
        parser, default_task_status_dir=default_task_status_dir)
    args = parser.parse_args(argv)

    assert args.task_status_dir == task_status_dir


@pytest.mark.parametrize("argv,fork_tasks", [
    ([], False),
    (["--fork-tasks"], True),
    (["--fork-task"], True),
    (["--fork"], True),
])
def test_cli_args_fork_tasks(
    argv: list[str], fork_tasks: bool,
) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    assert args.fork_tasks == fork_tasks


@pytest.mark.parametrize("argv", [
    (["--fork-tasks"],),
    (["--force"],),
    (["-f"],),
    (["-d"],),
])
def test_cli_args_without_progress_mode(
    argv: list[str],
) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser, task_progress_mode=False)
    with pytest.raises(SystemExit):
        parser.parse_args(argv)


def _option_dests() -> dict[str, str]:
    """Map the first flag of each option of ``add_arguments`` to its key."""
    parser = argparse.ArgumentParser(add_help=False)
    TaskGraphCli.add_arguments(parser)
    return {
        action.option_strings[0]: action.dest
        for action in parser._actions
        if action.option_strings
    }


def test_add_arguments_docstring_names_the_key_of_each_option() -> None:
    doc = TaskGraphCli.add_arguments.__doc__
    assert doc is not None

    missing = [
        dest for dest in _option_dests().values()
        if f"``{dest}``" not in doc
    ]

    assert missing == []


def test_add_arguments_adds_no_verbose_option() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    TaskGraphCli.add_arguments(parser)

    assert "verbose" not in _option_dests().values()
    with pytest.raises(SystemExit):
        parser.parse_args(["status", "-v"])


def test_process_graph_docstring_says_where_verbose_comes_from() -> None:
    doc = TaskGraphCli.process_graph.__doc__
    assert doc is not None

    assert "VerbosityConfiguration.set_arguments" in doc
