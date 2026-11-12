"""The ``grr_browse`` command: list the resources of the default GRR."""
import argparse
import sys

import yaml

from gain import __version__
from gain.genomic_resources.cli_list import run_list_command
from gain.genomic_resources.repository_factory import (
    DEFAULT_DEFINITION,
    build_genomic_resource_repository,
    get_default_grr_definition_path,
    load_definition_file,
    redact_definition,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration


def _build_browse_argument_parser() -> argparse.ArgumentParser:
    """Build the argument parser of ``grr_browse``."""
    desc = "Genomic Resource Repository Browse Tool"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "--version", action="store_true", default=False,
        help="Prints the GAIn version and exits.")
    VerbosityConfiguration.set_arguments(parser)

    group = parser.add_argument_group(title="Repository/Resource")
    group.add_argument(
        "-g", "--grr", type=str,
        default=None,
        help="path to GRR definition file.")
    group.add_argument(
        "-s", "--search", type=str, default=None,
        help="FTS search term to filter resources. A term may name one "
             "index column, e.g. 'assay_term_name: \"ATAC-seq\"', but the "
             "columns are per-repository -- across a group, a repository "
             "that does not index the column is skipped with a warning "
             "rather than searched. Use -q to select on labels "
             "independently of any index.")
    group.add_argument(
        "-t", "--type", type=str, default=None,
        help="Filter resources by type.")
    group.add_argument(
        "-q", "--query", type=str, default=None,
        help="Filter resources by a wildcard query over the resource id "
             "and labels, e.g. 'hg38/scores/*[phenotype=\"autism\"]'. "
             "A label a resource does not carry reads as empty, so "
             '[key="*"] holds for every resource rather than '
             "selecting the ones that have the label.")
    group.add_argument(
        "--summary", default=False, action="store_true",
        help="Print a summary for each resource below its listing line.")

    parser.add_argument(
        "--bytes",
        default=False,
        action="store_true",
        help="Print the resource size in bytes",
    )
    return parser


def cli_browse(cli_args: list[str] | None = None) -> None:
    """Provide CLI for repository browsing."""
    parser = _build_browse_argument_parser()
    if cli_args is None:
        cli_args = sys.argv[1:]
    args = parser.parse_args(cli_args)
    VerbosityConfiguration.set(args)

    if args.version:
        print(f"GAIn version: {__version__}")
        sys.exit(0)

    definition_path = args.grr if args.grr is not None \
        else get_default_grr_definition_path()
    definition = load_definition_file(definition_path) \
        if definition_path is not None \
        else DEFAULT_DEFINITION

    if definition_path is not None:
        print("Working with GRR definition:", definition_path)
    else:
        print("No GRR definition found, using the DEFAULT_DEFINITION")
    print(yaml.safe_dump(redact_definition(definition), sort_keys=False))

    repo = build_genomic_resource_repository(definition=definition)
    run_list_command(repo, args)
