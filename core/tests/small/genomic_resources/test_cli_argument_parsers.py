# pylint: disable=W0621,C0114,C0116,W0212
"""The module-level parser builders of the GRR tools (gain#1865).

The ``argparse`` directive of ``sphinx-argparse`` renders the option
reference of each tool page from these functions, so each one must build
the full parser of its tool without running the tool.
"""
import argparse

from gain.genomic_resources import cli, cli_cache_repo


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
    parser = cli._build_browse_argument_parser()

    args = parser.parse_args(
        ["-g", "grr.yaml", "-t", "position_score", "--bytes"])

    assert (args.grr, args.type, args.bytes) == (
        "grr.yaml", "position_score", True)


def test_cache_repo_parser_reads_the_worker_count() -> None:
    parser = cli_cache_repo._build_argument_parser()

    args = parser.parse_args(["pipeline.yaml", "-j", "8", "--no-progress"])

    assert (args.pipeline, args.jobs, args.progress) == (
        "pipeline.yaml", 8, False)
