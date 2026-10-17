# pylint: disable=C0116,W0212
"""The options that the page ``running_in_parallel`` names for each tool.

The page ``docs/source/running_in_parallel.rst`` lists the options that
``TaskGraphCli.add_arguments`` adds to ``annotate_tabular``,
``annotate_vcf``, ``binning_tool`` and the ``grr_manage`` subcommands. These
tests read the option strings from the page and compare them with the real
parser of each tool. An option string counts only when the parser has it
exactly, not through argparse's prefix matching.
"""
import argparse
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from gain.annotation import annotate_tabular, annotate_vcf
from gain.binning import cli as binning_cli
from gain.genomic_resources import cli as grr_cli
from gain.task_graph.cli_tools import TaskGraphCli

PAGE = (
    Path(__file__).parents[4] / "docs" / "source" / "running_in_parallel.rst"
)

# The first word of an inline literal that starts with a dash:
# ``-j N`` gives -j, ``--force`` gives --force.
_OPTION_RE = re.compile(r"``(--?[A-Za-z][A-Za-z-]*)")

TASK_STATUS_OPTIONS = frozenset({"-d", "--task-status-dir", "--tsd"})
GRR_MANAGE_SUBCOMMANDS = (
    "repo-stats", "resource-stats", "repo-info", "resource-info",
    "repo-repair", "resource-repair",
)

ParserBuilder = Callable[[], argparse.ArgumentParser]
TOOLS: dict[str, ParserBuilder] = {
    "annotate_tabular": annotate_tabular._build_argument_parser,
    "annotate_vcf": annotate_vcf._build_argument_parser,
    "binning_tool": binning_cli._build_argument_parser,
}


def _page_options() -> frozenset[str]:
    return frozenset(_OPTION_RE.findall(PAGE.read_text(encoding="utf-8")))


def _option_dests(parser: argparse.ArgumentParser) -> dict[str, str]:
    return {
        option: action.dest
        for option, action in parser._option_string_actions.items()
    }


def _task_graph_dests(*, task_progress_mode: bool) -> dict[str, str]:
    parser = argparse.ArgumentParser(add_help=False)
    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=task_progress_mode)
    return _option_dests(parser)


def _grr_manage_subcommand(name: str) -> argparse.ArgumentParser:
    parser = grr_cli._build_manage_argument_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    subparser: argparse.ArgumentParser = subparsers.choices[name]
    return subparser


def _all_parser_options() -> frozenset[str]:
    options: set[str] = set()
    for build_parser in TOOLS.values():
        options.update(build_parser()._option_string_actions)
    parser = grr_cli._build_manage_argument_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction))
    for subparser in subparsers.choices.values():
        options.update(subparser._option_string_actions)
    return frozenset(options)


def test_page_names_every_task_graph_option() -> None:
    task_graph_options = set(_task_graph_dests(task_progress_mode=True))

    missing = task_graph_options - {"-h", "--help"} - _page_options()

    assert not missing


def test_every_option_on_the_page_exists_in_some_tool() -> None:
    unknown = _page_options() - _all_parser_options()

    assert not unknown


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_takes_each_task_graph_option_on_the_page(tool: str) -> None:
    expected = {
        option: dest
        for option, dest in _task_graph_dests(task_progress_mode=True).items()
        if option in _page_options()
    }

    dests = _option_dests(TOOLS[tool]())

    assert {option: dests.get(option) for option in expected} == expected


@pytest.mark.parametrize("subcommand", GRR_MANAGE_SUBCOMMANDS)
def test_grr_manage_subcommand_takes_the_options_without_d(
    subcommand: str,
) -> None:
    expected = {
        option: dest
        for option, dest in _task_graph_dests(task_progress_mode=False).items()
        if option in _page_options()
    }

    dests = _option_dests(_grr_manage_subcommand(subcommand))

    assert {option: dests.get(option) for option in expected} == expected
    assert not TASK_STATUS_OPTIONS & set(dests)


@pytest.mark.parametrize("subcommand", GRR_MANAGE_SUBCOMMANDS)
def test_grr_manage_force_is_its_own_option(subcommand: str) -> None:
    task_graph = _task_graph_dests(task_progress_mode=False)

    dests = _option_dests(_grr_manage_subcommand(subcommand))

    assert "--force" not in task_graph
    assert dests["--force"] == dests["-f"]


def test_grr_manage_repo_index_takes_no_task_graph_option() -> None:
    task_graph = set(_task_graph_dests(task_progress_mode=False))

    options = set(_option_dests(_grr_manage_subcommand("repo-index")))

    assert not (task_graph - {"-h", "--help"}) & options


@pytest.mark.parametrize("tool", ["annotate_tabular", "annotate_vcf"])
@pytest.mark.parametrize("command", ["run", "list", "status"])
def test_annotate_tool_accepts_the_command(tool: str, command: str) -> None:
    args = TOOLS[tool]().parse_args(["input.tsv.bgz", "pipeline.yaml", command])

    assert args.command == command


def test_binning_tool_takes_no_command() -> None:
    args = binning_cli._build_argument_parser().parse_args(["run.yaml"])

    assert not hasattr(args, "command")
