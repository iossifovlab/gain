# pylint: disable=C0114,C0116
import pytest
from gain.task_graph.graph import TaskGraph


def noop(*_args: object, **_kwargs: object) -> None:
    return None


def build_graph() -> TaskGraph:
    """Build the graph a <- b <- c, plus the lone tasks d and e."""
    graph = TaskGraph()
    a = graph.create_task("a", noop, args=[])
    b = graph.create_task("b", noop, args=[], deps=[a])
    graph.create_task("c", noop, args=[], deps=[b])
    graph.create_task("d", noop, args=[])
    graph.create_task("e", noop, args=[])
    return graph


def task_ids(graph: TaskGraph) -> list[str]:
    return [task.task_id for task in graph.tasks]


def test_prune_keeps_the_named_task_and_its_transitive_deps() -> None:
    graph = build_graph()

    graph.prune(["c"])

    assert task_ids(graph) == ["a", "b", "c"]


def test_prune_accepts_task_handles_and_keeps_the_union_of_their_deps(
) -> None:
    graph = build_graph()
    b, d = (task for task in graph.tasks if task.task_id in {"b", "d"})

    graph.prune([b, d])

    assert task_ids(graph) == ["a", "b", "d"]


def test_prune_with_an_unknown_id_raises_and_leaves_the_graph_unchanged(
) -> None:
    graph = build_graph()

    with pytest.raises(ValueError, match="not in graph"):
        graph.prune(["c", "missing"])

    assert task_ids(graph) == ["a", "b", "c", "d", "e"]
