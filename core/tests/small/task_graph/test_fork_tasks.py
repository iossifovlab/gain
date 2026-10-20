# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613,too-many-lines
import argparse
import pathlib
import pickle  # ruff: ignore[suspicious-pickle-import]

import fsspec
import pytest
from gain.task_graph.base_executor import TaskGraphExecutorBase
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import Task, TaskDesc, TaskGraph


def add_to_list(what: int, where: list[int]) -> list[int]:
    where.append(what)
    return where


def test_executor_fork_tasks(
    tmp_path: pathlib.Path,
) -> None:
    parser = argparse.ArgumentParser(description="test_basic")
    TaskGraphCli.add_arguments(parser, task_progress_mode=True)
    args = parser.parse_args(
        ["--fork-tasks", "-j", "1", "-f", "-d", str(tmp_path)])
    executor = TaskGraphCli.create_executor(**vars(args))
    assert executor

    graph = TaskGraph()

    graph.create_task("0", add_to_list, args=[0, []], deps=[])

    TaskGraphCli.process_graph(graph, **vars(args))

    assert (tmp_path / "0.result").exists()


def test_fork_tasks_without_task_status_dir_uses_current_dir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A parser made with ``default_task_status_dir=None`` and no ``-d``
    # parses ``task_status_dir=None``; the forked run must write its
    # ``.result`` files to the current directory (gain#2030).
    monkeypatch.chdir(tmp_path)
    parser = argparse.ArgumentParser(description="test_no_status_dir")
    TaskGraphCli.add_arguments(parser, default_task_status_dir=None)
    args = parser.parse_args(["--fork-tasks", "-j", "1", "-f"])
    assert args.task_status_dir is None
    graph = TaskGraph()
    first = graph.create_task("first", add_to_list, args=[1, []], deps=[])
    graph.create_task(
        "second", add_to_list, args=[2, first], deps=[first])

    result = TaskGraphCli.process_graph(graph, **vars(args))

    assert result is True
    assert (tmp_path / "first.result").exists()
    assert (tmp_path / "second.result").exists()
    with (tmp_path / "second.result").open("rb") as infile:
        second_result = pickle.load(infile)
    assert second_result == [1, 2]


def sample_func(value: int) -> int:
    return value * 2


def fake_internal(
    task: TaskDesc,
    params: dict[str, object],
) -> str:
    task_func = task.func
    args = task.args
    params["call"] = (task_func, args, params)
    return "sentinel"


def test_exec_without_fork_invokes_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        TaskGraphExecutorBase,
        "_exec_internal",
        staticmethod(fake_internal),
    )

    params = {"fork_tasks": False, "task_id": "plain"}

    simple_task = TaskDesc(
        Task("test_task"),
        sample_func,
        [21],
        {},
        [],
        [],
        [],
        [],
    )
    result = TaskGraphExecutorBase._exec(
        simple_task, params,
    )

    assert result == "sentinel"
    assert params["call"] == (sample_func, [21], params)


def test_exec_forked_simple(
    tmp_path: pathlib.Path,
) -> None:

    task = TaskDesc(
        Task("test_task"),
        add_to_list,
        [1, []],
        {},
        [],
        [],
        [],
        [],
    )

    TaskGraphExecutorBase._exec_forked(
        task, params={"task_id": "0", "task_status_dir": str(tmp_path)},
    )

    result_fn = TaskGraphExecutorBase._result_fn(
        "test_task", {"task_status_dir": str(tmp_path)})
    assert pathlib.Path(result_fn).exists()
    with fsspec.open(result_fn, "rb") as infile:
        result = pickle.load(infile)  # pyright: ignore
    assert result == [1]


def test_exec_forked_round_trips_a_long_task_id(
    tmp_path: pathlib.Path,
) -> None:
    # The child writes ``<safe id>.result`` and the parent reads it back;
    # if ``safe_task_id`` is not a pure function of the task id the parent
    # opens a path that does not exist and silently returns None (gain#573).
    # The task is built directly -- ``TaskGraph.make_task`` truncates raw
    # ids to 200 chars, which would keep this off the affected branch.
    task = TaskDesc(
        Task("x" * 210),
        add_to_list,
        [1, []],
        {},
        [],
        [],
        [],
        [],
    )

    result = TaskGraphExecutorBase._exec(
        task,
        {"fork_tasks": True, "task_status_dir": str(tmp_path)},
    )

    assert result == [1]


def raise_exception() -> None:
    raise ValueError("Test exception")


def test_exec_forked_exception(
    tmp_path: pathlib.Path,
) -> None:
    task = TaskDesc(
        Task("test_task"),
        raise_exception,
        [],
        {},
        [],
        [],
        [],
        [],
    )

    TaskGraphExecutorBase._exec_forked(
        task,
        params={"task_status_dir": str(tmp_path)},
    )

    result_fn = TaskGraphExecutorBase._result_fn(
        "test_task", {"task_status_dir": str(tmp_path)})
    assert pathlib.Path(result_fn).exists()
    with fsspec.open(result_fn, "rb") as infile:
        result = pickle.load(infile)  # pyright: ignore
    assert isinstance(result, ValueError)
    assert str(result) == "Test exception"
