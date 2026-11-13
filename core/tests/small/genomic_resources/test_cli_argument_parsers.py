# pylint: disable=W0621,C0114,C0116,W0212
"""The module-level parser builders of the GRR tools (gain#1865).

The ``argparse`` directive of ``sphinx-argparse`` renders the option
reference of each tool page from these functions, so each one must build
the full parser of its tool without running the tool.
"""
import argparse

import pytest
from gain.genomic_resources import cli, cli_browse, cli_cache_repo


def _subcommands(parser: argparse.ArgumentParser) -> set[str]:
    actions = [
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1
    return set(actions[0].choices)


def test_manage_parser_holds_every_subcommand() -> None:
    parser = cli._build_manage_argument_parser()

    assert _subcommands(parser) == {
        "list", "repo-init", "repo-manifest", "resource-manifest",
        "repo-stats", "resource-stats", "repo-info", "resource-info",
        "repo-repair", "resource-repair", "repo-index",
    }


def test_browse_parser_reads_the_listing_filters() -> None:
    parser = cli_browse._build_browse_argument_parser()

    args = parser.parse_args(
        ["-g", "grr.yaml", "-t", "position_score", "--bytes"])

    assert (args.grr, args.type, args.bytes) == (
        "grr.yaml", "position_score", True)


def test_cache_repo_parser_reads_the_worker_count() -> None:
    parser = cli_cache_repo._build_argument_parser()

    args = parser.parse_args(["pipeline.yaml", "-j", "8", "--no-progress"])

    assert (args.pipeline, args.jobs, args.progress) == (
        "pipeline.yaml", 8, False)


def test_repo_info_rejects_a_resource(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = cli._build_manage_argument_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["repo-info", "-r", "x"])

    assert excinfo.value.code == 2
    assert "unrecognized arguments: -r x" in capsys.readouterr().err


def test_every_repo_command_rejects_a_resource(
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_commands = sorted(
        name for name in _subcommands(cli._build_manage_argument_parser())
        if name.startswith("repo-"))
    assert repo_commands

    rejected = []
    for command in repo_commands:
        with pytest.raises(SystemExit) as excinfo:
            cli._build_manage_argument_parser().parse_args(
                [command, "-r", "x"])
        err = capsys.readouterr().err
        if (excinfo.value.code == 2
                and "unrecognized arguments: -r x" in err):
            rejected.append(command)

    assert rejected == repo_commands


@pytest.mark.parametrize("command", [
    "resource-stats", "resource-repair", "resource-info",
])
def test_the_resource_commands_take_a_resource(command: str) -> None:
    parser = cli._build_manage_argument_parser()

    args = parser.parse_args([command, "-r", "x"])

    assert args.resource == "x"
