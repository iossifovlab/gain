import argparse
import random
import sys
import time
from collections.abc import Callable

from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.utils.verbosity_configuration import VerbosityConfiguration


def timeout(seconds: str) -> float:
    t = float(seconds)
    return max(random.gauss(t, 0.7 * t), 0.1)


def task_part(seconds: str) -> str:
    time.sleep(timeout(seconds))
    return 2_000 * "P"


def task_summary(seconds: str) -> None:
    time.sleep(timeout(seconds))


def task_part_b(seconds: str) -> str:
    time.sleep(timeout(seconds))
    return 2_000 * "B"


def task_part_c(seconds: str, *_args: str) -> str:
    time.sleep(timeout(seconds))
    return 2_000 * "C"


def _build_fan_in_graph(
    graph_letter: str,
    defaults: tuple[str, str, str],
    part_func: Callable[[str], str],
    graph_params: list[str] | None,
) -> TaskGraph:
    task_graph = TaskGraph()

    num_of_parts, parts_sleep, summary_sleep = defaults

    if graph_params:
        if len(graph_params) != 3:
            raise ValueError(
                f"The graph {graph_letter} needs three parameters: "
                "<number of parts>, <seconds for parts>, "
                "<seconds for summary>")
        num_of_parts, parts_sleep, summary_sleep = graph_params
    print(
        f"Building graph {graph_letter} with {num_of_parts} parts, "
        f"{parts_sleep} seconds for each part, and "
        f"{summary_sleep} seconds for the summary")

    parts = [task_graph.create_task(
        f"part {p}", part_func, args=[parts_sleep], deps=[])
        for p in range(int(num_of_parts))]
    task_graph.create_task(
        "summary", task_summary, args=[summary_sleep], deps=parts)
    return task_graph


def _build_graph_d(graph_params: list[str] | None) -> TaskGraph:
    task_graph = TaskGraph()

    num_of_clicks, num_of_parts, parts_sleep, summary_sleep = \
        "5", "10", "2", "2"

    if graph_params:
        if len(graph_params) != 4:
            raise ValueError(
                "The graph D needs four parameters: "
                "<number of clicks>, <number of parts>, "
                "<seconds for parts>, <seconds for summary>")
        num_of_clicks, num_of_parts, parts_sleep, summary_sleep = graph_params
    print(
        f"Building graph D with {num_of_clicks} clicks, "
        f"with {num_of_parts} parts per click and one summary task per click, "
        f"{parts_sleep} seconds for each part, and "
        f"{summary_sleep} seconds for the summary")

    for click in range(int(num_of_clicks)):
        parts = [task_graph.create_task(
            f"part {click}:{p}", task_part, args=[parts_sleep], deps=[])
            for p in range(int(num_of_parts))]
        task_graph.create_task(
            f"summary {click}", task_summary, args=[summary_sleep], deps=parts)
    return task_graph


def _build_graph_c(graph_params: list[str] | None) -> TaskGraph:
    task_graph = TaskGraph()

    num_of_parts, parts_sleep, summary_sleep = "2", "5", "10"

    if graph_params:
        if len(graph_params) != 3:
            raise ValueError(
                "The graph C needs three parameters: "
                "<number of parts>, <sleep for parts>, "
                "<sleep for summary>")
        num_of_parts, parts_sleep, summary_sleep = graph_params
    print(
        f"Building graph C with {num_of_parts} parts, "
        f"{parts_sleep} seconds sleep for each part, and "
        f"{summary_sleep} seconds sleep for the summary")

    parts = [
        task_graph.create_task(
            f"part {p}", task_part_c, args=[parts_sleep], deps=[])
        for p in range(int(num_of_parts))
    ]
    summary = task_graph.create_task(
        "summary", task_summary, args=[summary_sleep], deps=parts)
    parts2 = [
        task_graph.create_task(
            f"part2 {p}", task_part, args=[parts_sleep], deps=[summary])
        for p in range(int(num_of_parts))
    ]
    task_graph.create_task(
        "summary2", task_summary, args=[summary_sleep], deps=parts2)
    return task_graph


def build_demo_graph(graph_type: str,
                     graph_params: list[str] | None) -> TaskGraph:
    """Build a demo graph."""
    if graph_type == "A":
        return _build_fan_in_graph(
            "A", ("10", "2", "2"), task_part, graph_params)

    if graph_type == "B":
        return _build_fan_in_graph(
            "B", ("2", "5", "10"), task_part_b, graph_params)

    if graph_type == "C":
        return _build_graph_c(graph_params)

    if graph_type == "D":
        return _build_graph_d(graph_params)

    raise ValueError(f"Unknown graph <{graph_type}>")


def main(argv: list[str] | None = None) -> None:
    """Entry point for the demo script."""
    random.seed(42)

    parser = argparse.ArgumentParser(description="test_basic")

    parser.add_argument("graph", type=str, help="Demo graph",
                        default="A", nargs="?")
    VerbosityConfiguration.set_arguments(parser)

    parser.add_argument("--graph-params", "-gp", type=str, nargs="+")

    TaskGraphCli.add_arguments(parser)

    if argv is None:
        argv = sys.argv[1:]
    args = parser.parse_args(argv)
    VerbosityConfiguration.set(args)
    graph = build_demo_graph(args.graph, args.graph_params)
    TaskGraphCli.process_graph(graph, **vars(args))
