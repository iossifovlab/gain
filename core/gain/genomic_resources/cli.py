"""Provides CLI for management of genomic resources repositories."""
import argparse
import functools
import importlib
import os
import pathlib
import sys
import types
import warnings
from collections.abc import Mapping, Sequence
from typing import Any, cast

from gain import __version__, logging
from gain.genomic_resources.cli_browse import (  # pylint: disable=unused-import
    # The grr_browse console script names ``cli.cli_browse``.
    cli_browse,  # ruff: ignore[unused-import]
)
from gain.genomic_resources.cli_common import (
    CommandResult,
    _add_dry_run_and_force_parameters_group,
    _add_dvc_parameters_group,
    _add_hist_parameters_group,
    _add_repository_resource_parameters_group,
    _create_proto,
    _find_resources,
)
from gain.genomic_resources.cli_dvc import (
    refuse_dvc_directory_outputs,
)
from gain.genomic_resources.cli_info import (
    _run_repo_info_command,
    _run_resource_info_command,
)
from gain.genomic_resources.cli_list import run_list_command
from gain.genomic_resources.cli_manifest import (
    _build_content_file,
    _configure_repo_index_subparser,
    _configure_repo_manifest_subparser,
    _configure_resource_manifest_subparser,
    _run_repo_index_command,
    _run_repo_manifest_command,
    _run_resource_manifest_command,
)
from gain.genomic_resources.cli_stats import (
    _run_repo_stats_command,
    _run_resource_stats_command,
)
from gain.genomic_resources.dvc import (
    UnsupportedDvcDirectoryOutputError,
)
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
    get_default_grr_definition,
    load_definition_file,
)
from gain.genomic_resources.repository_ids import (
    GR_CONTENTS_FILE_NAME,
    GR_LEGACY_CONTENTS_FILE_NAME,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.utils.fs_utils import (
    find_directory_with_a_file,
)
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("grr_manage")


#: The public names that moved out of this module (gain#2268), each with
#: its new module. They stay importable from here for one release, with a
#: ``DeprecationWarning`` that names the new module.
_MOVED: dict[str, str] = {
    "CommandResult": "gain.genomic_resources.cli_common",
    "ManifestOutcome": "gain.genomic_resources.cli_manifest",
}


class _MovedNamesModule(types.ModuleType):
    """This module, warning when a caller reads a name that moved out.

    A module ``__getattr__`` runs only for a name missing from the module.
    This module still imports several moved names for its own code, so
    the warning sits on attribute access instead. An import statement
    and ``module.name`` both read through here. The code of this module
    reads its globals directly and never warns.
    """

    def __getattribute__(self, name: str) -> Any:
        moved_to = _MOVED.get(name)
        if moved_to is None:
            return super().__getattribute__(name)
        warnings.warn(
            f"{name} moved to {moved_to}", DeprecationWarning, stacklevel=2)
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return getattr(importlib.import_module(moved_to), name)


sys.modules[__name__].__class__ = _MovedNamesModule


def _configure_list_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("list", help="List a GR Repo")
    parser.add_argument(
        "--hr", default=False, action="store_true",
        help="Projects the size in human-readable format.")
    # The same three filters `grr_browse` offers, so that the two listings
    # of the same repository cannot be narrowed differently.
    parser.add_argument(
        "-s", "--search", type=str, default=None,
        help="FTS search term to filter resources. A term may name one "
             "index column, e.g. 'assay_term_name: \"ATAC-seq\"', but the "
             "columns are per-repository -- across a group, a repository "
             "that does not index the column is skipped with a warning "
             "rather than searched. Use -q to select on labels "
             "independently of any index.")
    parser.add_argument(
        "-t", "--type", type=str, default=None,
        help="Filter resources by type.")
    parser.add_argument(
        "-q", "--query", type=str, default=None,
        help="Filter resources by a wildcard query over the resource id "
             "and labels, e.g. 'hg38/scores/*[phenotype=\"autism\"]'. "
             "A label a resource does not carry reads as empty, so "
             '[key="*"] holds for every resource rather than '
             "selecting the ones that have the label.")
    _add_repository_resource_parameters_group(parser, use_resource=False)
    VerbosityConfiguration.set_arguments(parser)


def _configure_repo_init_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-init", help="Initialize a directory to turn it into a GRR")

    # No Force/Dry run group here: `repo-init` has no manifest-staleness
    # question to answer, and it never read either value -- so `repo-init
    # -n` initialised the repository for real, writing the content file
    # and a state for every file it hashed (#415).
    _add_repository_resource_parameters_group(parser, use_resource=False)
    VerbosityConfiguration.set_arguments(parser)


def _run_repo_init_command(*, repository: str | None) -> None:
    if repository is None:
        repo_url = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)
        if repo_url is None:
            repo_url = find_directory_with_a_file(GR_LEGACY_CONTENTS_FILE_NAME)
    else:
        assert repository is not None
        repo_url = find_directory_with_a_file(
            GR_CONTENTS_FILE_NAME, repository)
        if repo_url is None:
            repo_url = find_directory_with_a_file(
                GR_LEGACY_CONTENTS_FILE_NAME, repository)

    if repo_url is not None:
        logger.error(
            "current working directory is part of a GRR at %s", repo_url)
        sys.exit(1)

    if repository is None:
        cwd = pathlib.Path().absolute()
    else:
        cwd = pathlib.Path(repository).absolute()

    proto = _create_proto(str(cwd))
    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto)


# The subcommands that build statistics, pages or both take the same
# argument groups in the same order.  Only the name, the help text and the
# ``-r``/``--resource`` option differ: a ``repo-*`` command works on the
# whole repository, so it takes no resource.  Each row maps the name to
# the help text and the ``use_resource`` value; the row order is the order
# in which ``--help`` lists the commands.
_STATS_INFO_REPAIR_COMMANDS: dict[str, tuple[str, bool]] = {
    "repo-stats": ("Build the statistics for a resource", False),
    "resource-stats": ("Build the statistics for a resource", True),
    "repo-info": ("Build the index.html for the whole GRR", False),
    "resource-info": (
        "Build the index.html for the specific resource", True),
    "repo-repair": (
        "Update/rebuild manifest and histograms whole GRR", False),
    "resource-repair": (
        "Update/rebuild manifest and histograms for a resource", True),
}


def _configure_stats_info_repair_subparser(
    subparsers: argparse._SubParsersAction, name: str,
) -> None:
    help_text, use_resource = _STATS_INFO_REPAIR_COMMANDS[name]
    parser = subparsers.add_parser(name, help=help_text)
    _add_repository_resource_parameters_group(
        parser, use_resource=use_resource)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    _add_hist_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


# The repository-scoped and resource-scoped commands differ only in how they
# choose the resources they work on; everything after that -- which command
# function runs, how a failure is reported, and what the process exits with
# -- is shared, so it is written once (see _run_management_command and
# _exit_with).
_REPO_COMMANDS = frozenset({
    "repo-manifest", "repo-stats", "repo-info", "repo-repair",
    "repo-index"})


_RESOURCE_COMMANDS = frozenset({
    "resource-manifest", "resource-stats",
    "resource-info", "resource-repair"})


@functools.cache
def _task_graph_options() -> frozenset[str]:
    """The namespace keys forwarded to the task graph.

    Exactly what ``TaskGraphCli`` registers on the statistics subparsers,
    plus ``verbose``, which the whole stack shares.  An allowlist: the
    grr_manage options share the namespace, and a new one must not reach
    every task's parameters by default -- ``extra_args`` can carry a remote
    repository's credentials.
    """
    parser = argparse.ArgumentParser()
    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False)
    return frozenset(vars(parser.parse_args([]))) | {"verbose"}


def _build_manage_argument_parser() -> argparse.ArgumentParser:
    """Build the argument parser of ``grr_manage`` and its subcommands."""
    desc = "Genomic Resource Repository Management Tool"
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "--version", action="store_true", default=False,
        help="Prints the GAIn version and exits.")
    VerbosityConfiguration.set_arguments(parser)

    commands_parser: argparse._SubParsersAction = parser.add_subparsers(
        dest="command", help="Command to execute")

    _configure_list_subparser(commands_parser)
    _configure_repo_init_subparser(commands_parser)
    _configure_repo_manifest_subparser(commands_parser)
    _configure_resource_manifest_subparser(commands_parser)
    for name in _STATS_INFO_REPAIR_COMMANDS:
        _configure_stats_info_repair_subparser(commands_parser, name)
    _configure_repo_index_subparser(commands_parser)
    return parser


def _resolve_resources(
    command: str,
    proto: ReadWriteRepositoryProtocol,
    repo_url: str,
    args: argparse.Namespace,
) -> Sequence[GenomicResource]:
    """Find the resources that ``command`` works on, or exit."""
    resources: Sequence[GenomicResource]

    if command in _REPO_COMMANDS:
        resources = list(proto.get_all_resources())
        if not resources:
            # Noted, not acted on: an empty repository is the n=0 case of
            # the normal path, and the command still publishes the
            # globals it always publishes -- the index OF that emptiness
            # (gain#782).
            logger.info("repository <%s> has no resources", repo_url)
    elif command in _RESOURCE_COMMANDS:
        resources = _find_resources(proto, repo_url, resource=args.resource)
        if not resources:
            logger.error("resource not found...")
            sys.exit(1)
    else:
        logger.error(
            "Unknown command %s. The known commands are index, "
            "list and histogram", command)
        sys.exit(1)

    return resources


def cli_manage(cli_args: list[str] | None = None) -> None:
    """Provide CLI for repository management."""
    if cli_args is None:
        cli_args = sys.argv[1:]
    parser = _build_manage_argument_parser()
    args = parser.parse_args(cli_args)
    VerbosityConfiguration.set(args)

    if args.version:
        print(f"GAIn version: {__version__}")
        sys.exit(0)

    command = args.command
    if command is None:
        logger.error("missing grr_manage subcommand")
        parser.print_help()
        sys.exit(1)

    if command == "repo-init":
        _run_repo_init_command(repository=args.repository)
        return

    repo_url = _get_repo_url(args)
    repo = _create_grr_repo(args, repo_url)
    proto = _create_proto(repo_url, args.extra_args)
    if command == "list":
        run_list_command(proto, args)
        return

    if not isinstance(proto, ReadWriteRepositoryProtocol):
        raise TypeError(
            f"resource management works with RW protocols; "
            f"{proto.proto_id} ({proto.scheme}) is read only")

    resources = _resolve_resources(command, proto, repo_url, args)

    _exit_with(
        _run_management_command(repo, proto, resources, repo_url, **vars(args)),
        repo_url)


def _run_management_command(
    repo: GenomicResourceRepo,
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
    repo_url: str,
    **kwargs: Any,
) -> CommandResult:
    """Run one repository-management command over ``resources``.

    ``kwargs`` is the whole parsed argument namespace, and this is the one
    place it is unpacked: each command function takes the ``grr_manage``
    options it uses as required keyword-only parameters -- a missing or
    renamed option is a ``TypeError`` or ``KeyError``, never a substituted
    default (gain#1657) -- and the statistics commands get what remains,
    the task-graph options, as ``task_graph_args``.
    """
    command = cast(str, kwargs["command"])

    try:
        return _dispatch_management_command(
            command, repo, proto, resources, kwargs)
    except UnsupportedDvcDirectoryOutputError as ex:
        # A resource GAIn cannot verify: refuse it outright, loudly.
        logger.error("%s", ex)  # ruff: ignore[error-instead-of-exception]
        sys.exit(1)
    except ValueError as ex:
        # The repository itself, rather than one of its resources, is
        # unusable -- there is no resource id to attribute this to.
        logger.error(  # ruff: ignore[error-instead-of-exception]
            "Misconfigured repository %s; %s", repo_url, ex)
        logger.debug("repository %s is misconfigured", repo_url, exc_info=True)
        logger.warning("inconsistent GRR <%s> state", repo_url)
        sys.exit(1)


def _manifest_options(kwargs: Mapping[str, Any]) -> dict[str, bool]:
    """Pick the manifest options of a management command."""
    return {
        "dry_run": kwargs["dry_run"],
        "force": kwargs["force"],
        "use_dvc": kwargs["use_dvc"],
    }


def _stats_options(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the statistics options of a management command."""
    task_graph_options = _task_graph_options()
    return {
        **_manifest_options(kwargs),
        "region_size": kwargs["region_size"],
        "task_graph_args": {
            key: value for key, value in kwargs.items()
            if key in task_graph_options
        },
    }


def _dispatch_management_command(
    command: str,
    repo: GenomicResourceRepo,
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
    kwargs: Mapping[str, Any],
) -> CommandResult:
    """Run the command function that ``command`` names, or exit."""
    if command == "repo-index":
        # Ahead of the dvc-directory refusal: that guard protects
        # the writes a repair-style command makes inside resource
        # directories, and repo-index makes none. A dvc-directory
        # resource is published from its committed manifest like
        # any other, or skipped-and-reported if it has none (#373).
        return _run_repo_index_command(proto)
    # Before anything is written: a resource GAIn cannot certify fails
    # the command here, where failing it costs nothing, rather than
    # part-way through a repository it has already started rewriting
    # (#284).
    refuse_dvc_directory_outputs(proto, resources)
    # Dispatched by FULL command name, never by suffix: the repo- and
    # resource-scoped variants of one verb differ in whether the
    # repository-global artifacts are republished at the end, so a
    # suffix match would silently hand one scope the other's contract.
    if command == "repo-manifest":
        return _run_repo_manifest_command(
            proto, resources, **_manifest_options(kwargs))
    if command == "resource-manifest":
        return _run_resource_manifest_command(
            proto, resources, **_manifest_options(kwargs))
    if command == "repo-stats":
        return _run_repo_stats_command(
            repo, proto, resources, **_stats_options(kwargs))
    if command == "resource-stats":
        return _run_resource_stats_command(
            repo, proto, resources, **_stats_options(kwargs))
    # Repair is info plus nothing: the info commands already rebuild
    # manifests and statistics on the way to the pages.
    if command in ("repo-info", "repo-repair"):
        return _run_repo_info_command(
            repo, proto, resources, **_stats_options(kwargs))
    if command in ("resource-info", "resource-repair"):
        return _run_resource_info_command(
            repo, proto, resources, **_stats_options(kwargs))
    # Never fall through: repair is destructive, and a command name
    # added to _REPO_COMMANDS/_RESOURCE_COMMANDS but not handled here
    # would otherwise silently run a full repair.
    logger.error("Unknown command %s.", command)
    sys.exit(1)


def _exit_with(result: CommandResult, repo_url: str) -> None:
    """Turn a command result into a message and an exit status.

    The one place that decides what the process says and exits with, so the
    repository-scoped and resource-scoped commands cannot drift apart.
    """
    if result.has_failures:
        if result.failed:
            logger.error(
                "failed resources in GRR <%s>: %s",
                repo_url, ", ".join(sorted(result.failed)))
        else:
            logger.error(
                "GRR <%s> could not be processed; "
                "no single resource can be blamed", repo_url)
    if result.needs_update or result.has_failures:
        logger.warning("inconsistent GRR <%s> state", repo_url)
        # `needs_update` is a count and only a `--dry-run` produces one; a
        # real run reports failure with a bare 1.
        sys.exit(result.needs_update or 1)
    logger.info("GRR <%s> is consistent", repo_url)


def _create_grr_repo(
    args: argparse.Namespace,
    repo_url: str,
) -> GenomicResourceRepo:
    extra_definition_path = args.grr
    if extra_definition_path:
        if not os.path.exists(extra_definition_path):
            raise FileNotFoundError(
                f"Definition {extra_definition_path} not found!",
            )
        extra_definition = load_definition_file(extra_definition_path)
    else:
        extra_definition = get_default_grr_definition()
    local_id = "local"
    # ``load_definition_file`` is a bare ``yaml.safe_load``, so a malformed
    # definition file is whatever YAML made of it -- ``None`` for an empty
    # file, a list, a bare string. Only a mapping has an ``id`` to read or
    # rewrite; anything else is nested verbatim so that the factory's own
    # validation reports it as an invalid definition, rather than this
    # normalisation turning it into an AttributeError that hides the cause.
    if isinstance(extra_definition, dict):
        if not extra_definition.get("id"):
            # A top-level GRR definition may omit ``id``; nested here as a
            # group child it would then get a synthesised, url-derived id
            # (see ``repository_factory``). Name it instead, so the child id
            # -- and, with it, the cache directory of a cached repository --
            # stays the same predictable value no matter what the user's
            # definition points at (#445).
            extra_definition = {**extra_definition, "id": "default_grr"}
        if extra_definition["id"] == local_id:
            # A user definition may legitimately be named ``local`` -- the
            # ones shipped in this repo are -- and it is nested here as a
            # sibling of the CLI's own child, where child ids must be
            # unique. The synthetic group is the CLI's own invention,
            # invisible to the user, so the CLI renames its own child rather
            # than refusing to run (#445). The fallback cannot collide in
            # turn: it is only used when the user definition is called
            # ``local``.
            local_id = "cli_local"
    grr_definition = {
        "id": "cli_grr",
        "type": "group",
        "children": [
            {
                "id": local_id,
                "type": "dir",
                "directory": repo_url,
            },
            extra_definition,
        ],
    }

    return build_genomic_resource_repository(definition=grr_definition)


def _get_repo_url(args: argparse.Namespace) -> str:
    repo_url = args.repository
    if repo_url is None:
        repo_url = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)
        if repo_url is None:
            repo_url = find_directory_with_a_file(GR_LEGACY_CONTENTS_FILE_NAME)
        if repo_url is None:
            logger.error(
                "Can't find repository starting from: %s", os.getcwd())
            sys.exit(1)
        repo_url = str(repo_url)
        print(f"working with repository: {repo_url}")

    return cast(str, repo_url)
