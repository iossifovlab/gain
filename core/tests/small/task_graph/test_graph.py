# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import pytest
from gain.task_graph.executor import TaskGraphExecutor
from gain.task_graph.graph import Task, TaskDesc, TaskGraph, chain_tasks
from gain.task_graph.sequential_executor import SequentialExecutor


def test_creating_two_tasks_with_the_same_id() -> None:
    graph = TaskGraph()
    graph.create_task("ID", lambda: None, args=[], deps=[])
    with pytest.raises(ValueError):
        graph.create_task("ID", lambda: None, args=[], deps=[])


def test_add_tasks_with_an_id_already_in_graph_leaves_graph_unchanged(
) -> None:
    graph = TaskGraph()
    graph.create_task("a", print, args=[], deps=[])
    descs = [
        graph.make_task("b", print, args=[]),
        graph.make_task("a", print, args=[]),
    ]

    with pytest.raises(ValueError, match="already in graph"):
        graph.add_tasks(descs)

    assert [t.task_id for t in graph.tasks] == ["a"]


def test_add_tasks_with_a_duplicate_id_in_the_list_raises() -> None:
    graph = TaskGraph()
    desc = graph.make_task("a", print, args=[])

    with pytest.raises(ValueError, match="already in graph"):
        graph.add_tasks([desc, desc])

    assert [t.task_id for t in graph.tasks] == ["a"]


def task_a_fn() -> str:
    return "result A"


def task_b_fn(a: str) -> str:
    return f"result B with ({a})"


def task_c_fn(b: str) -> str:
    return f"result C with ({b})"


def task_d_fn() -> str:
    return "result D"


task_a = TaskGraph.make_task(
    "A", task_a_fn, args=[], deps=[])
task_b = TaskGraph.make_task(
    "B", task_b_fn, args=[task_a.task], deps=[task_a.task])
task_c = TaskGraph.make_task(
    "C", task_c_fn, args=[task_b.task], deps=[task_b.task])
task_d = TaskGraph.make_task(
    "D", task_d_fn, args=[], deps=[])


@pytest.mark.parametrize(
    "tasks, expected", [
        ([task_a, task_d], "result D"),
        ([task_a, task_b], "result B with (result A)"),
        ([task_a, task_b, task_c], "result C with (result B with (result A))"),
        ([task_a, task_b, task_c, task_d], "result D"),
        ([task_a, task_b, task_d], "result D"),
        ([task_d, task_a, task_b, task_c],
         "result C with (result B with (result A))"),
    ],
)
def test_chain_tasks(
    tasks: list[TaskDesc],
    expected: str,
) -> None:

    task = chain_tasks(*tasks)
    assert task is not None

    res = task.func(*task.args, **task.kwargs)
    assert res == expected


@pytest.mark.parametrize(
    "tasks, task_id, expected", [
        ([task_a, task_d], "D", "result D"),
        ([task_a, task_b],
         "B", "result B with (result A)"),
        ([task_a, task_b, task_c],
         "C", "result C with (result B with (result A))"),
        ([task_a, task_b, task_c, task_d],
         "D", "result D"),
        ([task_d, task_a, task_b, task_c],
         "C", "result C with (result B with (result A))"),
    ],
)
def test_executor_chain_tasks(
    executor: TaskGraphExecutor,
    tasks: list[TaskDesc],
    task_id: str,
    expected: str,
) -> None:
    graph = TaskGraph()

    task = chain_tasks(*tasks)
    graph.add_task(task)

    executed_tasks = list(executor.execute(graph))
    assert len(executed_tasks) == 1
    ex_task, ex_res = executed_tasks[0]
    assert ex_task.task_id == task_id
    assert ex_res == expected


def test_chain_first_task_gets_the_result_of_a_task_in_its_args() -> None:
    graph = TaskGraph()
    task_a = graph.create_task("a", lambda: 5, args=[])
    first = graph.make_task("b", lambda x: ("got", x), args=[task_a])
    second = graph.make_task("c", lambda y: y, args=[first.task])
    graph.add_task(chain_tasks(first, second))

    results = dict(SequentialExecutor().execute(graph))

    assert results[Task("c")] == ("got", 5)


def test_chain_first_task_gets_the_result_of_a_task_in_its_kwargs() -> None:
    graph = TaskGraph()
    task_a = graph.create_task("a", lambda: 5, args=[])
    first = graph.make_task(
        "b", lambda *, x: ("got", x), args=[], kwargs={"x": task_a})
    second = graph.make_task("c", lambda y: y, args=[first.task])
    graph.add_task(chain_tasks(first, second))

    results = dict(SequentialExecutor().execute(graph))

    assert results[Task("c")] == ("got", 5)


def five_fn() -> int:
    return 5


def got_fn(x: int) -> tuple[str, int]:
    return ("got", x)


def got_kwarg_fn(*, x: int) -> tuple[str, int]:
    return ("got", x)


def identity_fn(y: object) -> object:
    return y


@pytest.mark.parametrize("dependency_in", ["args", "kwargs"])
def test_executor_chain_first_task_gets_the_result_of_a_task(
    executor: TaskGraphExecutor,
    dependency_in: str,
) -> None:
    graph = TaskGraph()
    task_a = graph.create_task("a", five_fn, args=[])
    if dependency_in == "args":
        first = graph.make_task("b", got_fn, args=[task_a])
    else:
        first = graph.make_task(
            "b", got_kwarg_fn, args=[], kwargs={"x": task_a})
    second = graph.make_task("c", identity_fn, args=[first.task])
    third = graph.make_task("d", identity_fn, args=[second.task])
    graph.add_task(chain_tasks(first, second, third))

    results = dict(executor.execute(graph))

    assert results[Task("d")] == ("got", 5)
