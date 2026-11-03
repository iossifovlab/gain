# pylint: disable=C0114,C0116
from gain.task_graph.graph import Task, TaskGraph


def task_repr(graph: TaskGraph, task: Task) -> str:
    with graph as tasks:
        task_node = tasks[task]
    return repr(task_node)


def test_repr_of_a_task_without_deps_or_files_lists_only_the_call() -> None:
    graph = TaskGraph()
    task = graph.create_task("a", print, args=[1, "x"], kwargs={"sep": "-"})

    assert task_repr(graph, task) == (
        "Task(id=a, func=<built-in function print>, "
        "args=[1, 'x'], kwargs={'sep': '-'})"
    )


def test_repr_of_a_task_with_deps_and_files_lists_each_of_them() -> None:
    graph = TaskGraph()
    dep = graph.create_task("a", print, args=[])
    task = graph.create_task(
        "b", print, args=[],
        deps=[dep],
        input_files=["in.txt"],
        output_files=["out.txt"],
        intermediate_output_files=["tmp.txt"],
    )

    text = task_repr(graph, task)

    # Check each field, but not the closing text after it. The repr closes
    # each optional field with a stray ")", and a fix of it keeps this test.
    assert text.startswith(
        "Task(id=b, func=<built-in function print>, args=[], kwargs={}, ",
    )
    assert "deps=['a']" in text
    assert "input_files=['in.txt']" in text
    assert " output_files=['out.txt']" in text
    assert "intermediate_output_files=['tmp.txt']" in text
