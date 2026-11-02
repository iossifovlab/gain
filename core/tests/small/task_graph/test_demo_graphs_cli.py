# pylint: disable=W0621,C0114,C0115,C0116,W0212,W0613
import pytest
from gain.task_graph.cli_tools import (
    task_graph_run,
    task_graph_run_with_results,
)
from gain.task_graph.demo_graphs_cli import build_demo_graph
from gain.task_graph.graph import TaskGraph

PART_SLEEP = "0.01"
SUMMARY_SLEEP = "0.02"

SHORT_PARAMS = {
    "A": ["2", PART_SLEEP, SUMMARY_SLEEP],
    "B": ["2", PART_SLEEP, SUMMARY_SLEEP],
    "C": ["2", PART_SLEEP, SUMMARY_SLEEP],
    "D": ["2", "2", PART_SLEEP, SUMMARY_SLEEP],
}


def _graph_shape(task_graph: TaskGraph) -> dict[str, set[str]]:
    return {
        task.task_id: {
            dep.task_id for dep in task_graph.get_task_desc(task).deps}
        for task in task_graph.tasks
    }


@pytest.mark.parametrize("graph_type", ["A", "B"])
def test_graph_a_and_b_have_parts_then_one_summary(graph_type: str) -> None:
    task_graph = build_demo_graph(graph_type, SHORT_PARAMS[graph_type])

    assert _graph_shape(task_graph) == {
        "part 0": set(),
        "part 1": set(),
        "summary": {"part 0", "part 1"},
    }


def test_graph_c_has_two_fan_outs() -> None:
    task_graph = build_demo_graph("C", SHORT_PARAMS["C"])

    assert _graph_shape(task_graph) == {
        "part 0": set(),
        "part 1": set(),
        "summary": {"part 0", "part 1"},
        "part2 0": {"summary"},
        "part2 1": {"summary"},
        "summary2": {"part2 0", "part2 1"},
    }


def test_graph_d_has_one_fan_out_per_click() -> None:
    task_graph = build_demo_graph("D", SHORT_PARAMS["D"])

    assert _graph_shape(task_graph) == {
        "part 0:0": set(),
        "part 0:1": set(),
        "summary 0": {"part 0:0", "part 0:1"},
        "part 1:0": set(),
        "part 1:1": set(),
        "summary 1": {"part 1:0", "part 1:1"},
    }


@pytest.mark.parametrize("graph_type", ["A", "B", "C", "D"])
def test_builder_prints_its_own_graph_letter(
    graph_type: str, capsys: pytest.CaptureFixture[str],
) -> None:
    build_demo_graph(graph_type, SHORT_PARAMS[graph_type])

    output = capsys.readouterr().out
    assert f"Building graph {graph_type} with" in output
    for typo in ("Bulding", "secoconds", "each parts"):
        assert typo not in output


@pytest.mark.parametrize("graph_type", ["A", "B", "C", "D"])
def test_graph_runs_to_the_end(graph_type: str) -> None:
    task_graph = build_demo_graph(graph_type, SHORT_PARAMS[graph_type])

    assert task_graph_run(task_graph) is True


@pytest.mark.parametrize("graph_type", ["A", "B", "C", "D"])
def test_wrong_parameter_count_names_the_graph(graph_type: str) -> None:
    with pytest.raises(ValueError, match=f"The graph {graph_type} needs"):
        build_demo_graph(graph_type, ["1"])


@pytest.mark.parametrize("graph_type", ["A", "B", "C", "D"])
@pytest.mark.parametrize("graph_params", [None, []])
def test_no_parameters_selects_the_defaults(
    graph_type: str, graph_params: list[str] | None,
) -> None:
    task_graph = build_demo_graph(graph_type, graph_params)

    assert len(task_graph.tasks) == {"A": 11, "B": 3, "C": 6, "D": 55}[
        graph_type]


@pytest.mark.parametrize("graph_type", ["A", "B", "C", "D"])
def test_parts_get_the_part_sleep_and_summaries_the_summary_sleep(
    graph_type: str,
) -> None:
    task_graph = build_demo_graph(graph_type, SHORT_PARAMS[graph_type])

    sleeps = {
        task.task_id: task_graph.get_task_desc(task).args
        for task in task_graph.tasks
    }
    assert sleeps
    for task_id, args in sleeps.items():
        expected = PART_SLEEP if task_id.startswith("part") else SUMMARY_SLEEP
        assert args == [expected], task_id


@pytest.mark.parametrize(("graph_type", "printout"), [
    ("A", (
        "Building graph A with 10 parts, 2 seconds for each part, and "
        "2 seconds for the summary")),
    ("B", (
        "Building graph B with 2 parts, 5 seconds for each part, and "
        "10 seconds for the summary")),
    ("C", (
        "Building graph C with 2 parts, 5 seconds sleep for each part, "
        "and 10 seconds sleep for the summary")),
    ("D", (
        "Building graph D with 5 clicks, with 10 parts per click and one "
        "summary task per click, 2 seconds for each part, and 2 seconds "
        "for the summary")),
])
def test_builder_prints_its_default_parameters(
    graph_type: str, printout: str, capsys: pytest.CaptureFixture[str],
) -> None:
    build_demo_graph(graph_type, None)

    assert capsys.readouterr().out.strip() == printout


def test_unknown_graph_type_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown graph <E>"):
        build_demo_graph("E", None)


@pytest.mark.parametrize(("graph_type", "part_letter"), [
    ("A", "P"),
    ("B", "B"),
])
def test_graph_a_and_b_keep_their_part_functions(
    graph_type: str, part_letter: str,
) -> None:
    task_graph = build_demo_graph(graph_type, SHORT_PARAMS[graph_type])

    results = list(task_graph_run_with_results(task_graph))

    part_results = [result for result in results if result is not None]
    assert part_results == [2_000 * part_letter, 2_000 * part_letter]
    assert len(results) == 3
