"""Provides CLI for management of genomic resources repositories."""
import argparse
import copy
import dataclasses
import fnmatch
import gzip
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, NamedTuple, cast
from urllib.parse import urlparse

import apsw
import yaml
from cerberus.schema import SchemaError

from gain import __version__, logging
from gain.genomic_resources.cached_repository import GenomicResourceCachedRepo
from gain.genomic_resources.dvc import (
    DvcContentDriftError,
    is_dvc_directory_out,
    parse_dvc_pointer_out,
)
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GenomicResource,
    GenomicResourceRepo,
    ManifestEntry,
    ReadOnlyRepositoryProtocol,
    ReadWriteRepositoryProtocol,
    parse_gr_id_version_token,
    version_tuple_to_string,
)
from gain.genomic_resources.repository_factory import (
    DEFAULT_DEFINITION,
    build_genomic_resource_repository,
    build_resource_implementation,
    get_default_grr_definition,
    get_default_grr_definition_path,
    load_definition_file,
    redact_definition,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    ResourceStatistics,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import Task, TaskGraph, chain_tasks
from gain.utils import fs_utils
from gain.utils.fs_utils import (
    find_directory_with_a_file,
    find_subdirectories_with_a_file,
)
from gain.utils.helpers import convert_size
from gain.utils.verbosity_configuration import VerbosityConfiguration

logger = logging.getLogger("grr_manage")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """What a repository-management command found and what it could not do.

    Three outcomes, deliberately kept apart (gain#364):

    * ``needs_update`` -- how many resources are OUT OF DATE.  Only a
      ``--dry-run`` reports this; a real run repairs them instead of
      counting them.  This is the meaning the plain ``int`` these commands
      used to return carried.
    * ``failed`` -- the ids of the resources that are BROKEN: whatever
      GAIn was asked to do to them raised, or silently did not happen.  A
      run collects these rather than aborting on the first one, so a
      single broken resource cannot stop the healthy ones from being
      repaired.
    * ``repo_failed`` -- something failed that no single resource can be
      blamed for: the repository's own configuration, or a statistics task
      graph whose failure the per-resource check could not pin on any
      resource.  Inventing a resource id for it would be a lie, but it
      still has to make the run exit non-zero.

    An ``int`` could express only the first, which is why non-dry-run
    repair was structurally incapable of reporting failure.
    """

    needs_update: int = 0
    failed: frozenset[str] = frozenset()
    repo_failed: bool = False

    @property
    def has_failures(self) -> bool:
        """Whether anything failed at all, attributable or not."""
        return bool(self.failed) or self.repo_failed


# The exceptions that mean the RESOURCE (or its configuration) is at fault:
# a malformed config, a schema violation, a file that is not there.  They
# are reported as one line carrying the cause, with the traceback demoted to
# DEBUG.  Anything else is a defect in GAIn and keeps its traceback at ERROR.
_RESOURCE_ERRORS = (ValueError, SchemaError, FileNotFoundError)


def _report_resource_failure(
    err: Exception, action: str, resource_id: str,
) -> None:
    """Report a failed operation on one resource, at the right tier.

    ``action`` names what could not be done -- never the phase the failure
    happened in.  A handler that wraps several operations cannot know which
    one raised, and naming the wrong one sends the reader looking in the
    wrong place (gain#364); the cause, which is always carried, says it.
    """
    # LOG014 is suppressed rather than obeyed: every caller is an exception
    # handler, which is exactly what makes `exc_info` meaningful here -- the
    # linter cannot see that through the call.
    if isinstance(err, _RESOURCE_ERRORS):
        # `str(err)` is empty for an exception raised without a message --
        # `raise ValueError()`, or a bare `assert` under `python -O`. The
        # class name is then the only thing left that says anything about
        # the cause, and this issue is exactly about losing it (gain#364).
        logger.error(
            "%s <%s>: %s", action, resource_id,
            str(err) or type(err).__name__)
        logger.debug(
            "%s <%s> failed", action, resource_id,
            exc_info=True)  # noqa: LOG014
        return
    logger.error(
        "%s <%s>: unexpected internal error", action, resource_id,
        exc_info=True)  # noqa: LOG014


def _add_repository_resource_parameters_group(
    parser: argparse.ArgumentParser, *, use_resource: bool = True,
) -> None:

    group = parser.add_argument_group(title="Repository/Resource")
    group.add_argument(
        "-R", "--repository", type=str,
        default=None,
        help="URL to the genomic resources repository. If not specified "
        "the tool assumes a local file system repository and starts looking "
        "for .CONTENTS.json file from the current working directory up to the "
        "root directory. If found the directory is assumed for root "
        "repository directory; otherwise error is reported.")
    group.add_argument(
        "--grr", "--definition", "-g", type=str,
        default=None,
        help="Path to an extra GRR definition file. This GRR will be loaded"
        "in a group alongside the local one.")

    group.add_argument(
        "--extra-args", type=str, default=None,
        help="comma separated list of `key=value` pairs arguments needed for "
        "connection to the specific repository protocol. "
        "Ex: if you want to connect to an S3 repository it is often "
        "neccessary to pass additional `endpoint-url` argument.",
    )
    if use_resource:
        group.add_argument(
            "-r", "--resource", type=str,
            help="Specifies the resource whose manifest we want to rebuild. "
            "If not specified the tool assumes local filesystem repository "
            "and starts looking for 'genomic_resource.yaml' file from "
            "current working directory up to the root directory. If found "
            "the directory is assumed for a resource directory; otherwise "
            "error is reported.")


def _add_dry_run_and_force_parameters_group(
        parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="Force/Dry run")
    group.add_argument(
        "-n", "--dry-run", default=False, action="store_true",
        help="only checks if the manifest update is needed whithout "
        "actually updating it")
    group.add_argument(
        "-f", "--force", default=False,
        action="store_true",
        help="ignore resource state and rebuild manifest")


def _add_dvc_parameters_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="DVC params")
    group.add_argument(
        "--with-dvc", default=True,
        action="store_true", dest="use_dvc",
        help="trust a '.dvc' sidecar as the md5 sum and size of the file it "
        "describes, and never hash a DVC-managed file (default). A file GAIn "
        "has already hashed keeps its recorded md5 sum while its size and "
        "timestamp are unchanged; a file with no sidecar and no usable state "
        "is hashed")
    group.add_argument(
        "-D", "--without-dvc",
        action="store_false", dest="use_dvc",
        help="verify mode: ignore every recorded state, compute from its "
        "content the md5 sum of every resource file that is on disk, and "
        "check it against the file's '.dvc' sidecar. The run reports every "
        "file that disagrees and exits non-zero, writing no manifest for the "
        "resources they belong to; fix the drift with 'dvc add' / "
        "'dvc commit'. A file that is NOT on disk still takes its md5 sum "
        "and size from its sidecar - there is no content to hash, and its "
        "manifest entry is never dropped")


def _add_hist_parameters_group(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(title="Statistics")
    group.add_argument(
        "--region-size", type=int, default=3_000_000_000,
        help="Region size to use for splitting statistics calculation into "
        "tasks")


def _configure_list_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("list", help="List a GR Repo")
    parser.add_argument(
        "--hr", default=False, action="store_true",
        help="Projects the size in human-readable format.")
    _add_repository_resource_parameters_group(parser, use_resource=False)
    VerbosityConfiguration.set_arguments(parser)


def _run_list_command(
        proto: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
        args: argparse.Namespace) -> None:
    search_term = getattr(args, "search", None)
    resource_type = getattr(args, "type", None)
    long_format = getattr(args, "summary", False)
    repos: list = [proto]
    if isinstance(proto, GenomicResourceGroupRepo):
        repos = proto.children
    for repo in repos:
        for res in repo.search_resources(search_term, resource_type):
            res_size = sum(fs for _, fs in res.get_manifest().get_files())

            files_msg = f"{len(list(res.get_manifest().get_files())):2d}"
            if isinstance(repo, GenomicResourceCachedRepo):
                cached_files = repo.get_resource_cached_files(res.get_id())
                files_msg = f"{len(cached_files):2d}/{files_msg}"

            res_size_msg = res_size \
                if hasattr(args, "bytes") and args.bytes is True \
                else convert_size(res_size)
            repo_id = repo.repo_id if isinstance(repo, GenomicResourceRepo) \
                else repo.get_id()
            print(
                f"{res.get_type():20} {res.get_version_str():7s} "
                f"{files_msg} {res_size_msg:12} "
                f"{repo_id} "
                f"{res.get_id()}")
            if long_format:
                summary = res.get_summary()
                if summary:
                    print(f"  {summary.strip()}")


def _configure_repo_init_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-init", help="Initialize a directory to turn it into a GRR")

    _add_repository_resource_parameters_group(parser, use_resource=False)
    _add_dry_run_and_force_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)


def _run_repo_init_command(**kwargs: str) -> None:
    repository: str | None = kwargs.get("repository")
    if repository is None:
        repo_url = find_directory_with_a_file(GR_CONTENTS_FILE_NAME)
        if repo_url is None:
            repo_url = find_directory_with_a_file(GR_CONTENTS_FILE_NAME[:-3])
    else:
        assert repository is not None
        repo_url = find_directory_with_a_file(
            GR_CONTENTS_FILE_NAME, repository)
        if repo_url is None:
            repo_url = find_directory_with_a_file(
                GR_CONTENTS_FILE_NAME[:-3], repository)

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


def _configure_repo_manifest_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-manifest", help="Create/update manifests for whole GRR")

    _add_repository_resource_parameters_group(parser, use_resource=False)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)


def _configure_resource_manifest_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resource-manifest", help="Create/update manifests for a resource")

    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)


def _configure_repo_stats_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-stats",
        help="Build the statistics for a resource")

    _add_repository_resource_parameters_group(parser, use_resource=False)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    _add_hist_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


def _configure_resource_stats_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resource-stats",
        help="Build the statistics for a resource")

    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    _add_hist_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


def _configure_repo_repair_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-repair",
        help="Update/rebuild manifest and histograms whole GRR")
    _add_repository_resource_parameters_group(parser, use_resource=False)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    _add_hist_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


def _configure_resource_repair_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resource-repair",
        help="Update/rebuild manifest and histograms for a resource")
    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    _add_hist_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


def _configure_repo_info_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repo-info", help="Build the index.html for the whole GRR",
    )
    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


def _configure_resource_info_subparser(
        subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resource-info", help="Build the index.html for the specific resource",
    )
    _add_repository_resource_parameters_group(parser)
    _add_dry_run_and_force_parameters_group(parser)
    _add_dvc_parameters_group(parser)
    VerbosityConfiguration.set_arguments(parser)

    TaskGraphCli.add_arguments(
        parser, use_commands=False, task_progress_mode=False,
    )


class UnsupportedDvcDirectoryOutputError(Exception):
    """A resource declares a ``dvc add <dir>`` output, which GAIn refuses.

    Raised by :func:`collect_dvc_entries` and turned into a non-zero exit by
    :func:`cli_manage` (#255).
    """


def collect_dvc_entries(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> dict[str, ManifestEntry]:
    """Collect manifest entries defined by .dvc files.

    A ``.dvc`` file that cannot be read, does not parse as a pointer for the
    data file it sits next to, or declares no usable md5 sum and size is
    skipped with a warning - never propagated into the manifest, and never
    allowed to abort the command. `.dvc` sidecars are read on every
    ``grr_manage`` run, and the repository scan that produced this entry has
    already tolerated the very same content (see
    :meth:`FsspecReadWriteProtocol._is_dvc_managed_leaf`); the two classify
    identically because both delegate to
    :func:`dvc.parse_dvc_pointer_out`.

    A *well-formed* sidecar for a ``dvc add <dir>`` output is a different
    matter: it is not ignored, it is REFUSED. GAIn cannot verify a ``.dir``
    md5 sum - it hashes a DVC cache object, not any file GAIn can read - so
    writing it into the manifest would be a false clean bill of health, and
    quietly skipping the directory would leave its data unmanifested and
    unverified. Either way the resource would be certified without its
    content ever being checked, so the command fails instead (#255). This is
    the gate every ``grr_manage`` subcommand that builds or checks a manifest
    passes through, and it applies whether or not the directory is
    materialised.

    An entry is produced for every readable sidecar. Every materialised
    file's entry is consulted by
    :meth:`ReadWriteRepositoryProtocol._update_manifest_entry_and_state` -
    the sidecar IS the md5 sum of the file it describes - and the entries
    for files the scan did not yield are merged by
    :meth:`ReadWriteRepositoryProtocol._merge_unscanned_dvc_entries` (#373).

    Raises:
        UnsupportedDvcDirectoryOutputError: the resource has a ``dvc add
            <dir>`` output.
    """
    result = {}
    manifest = proto.collect_resource_entries(res)
    for entry in manifest:
        if not entry.name.endswith(".dvc"):
            continue
        filename = entry.name[:-4]
        basename = os.path.basename(filename)

        try:
            with proto.open_raw_file(res, entry.name, "rb") as infile:
                content = cast(bytes, infile.read())
        except (OSError, ValueError):
            logger.warning(
                "unable to read the '.dvc' file <%s> of <%s>; ignoring it",
                entry.name, res.resource_id)
            continue

        out = parse_dvc_pointer_out(content, basename)
        if out is None:
            logger.warning(
                "the '.dvc' file <%s> of <%s> is not a dvc pointer for <%s>; "
                "ignoring it",
                entry.name, res.resource_id, filename)
            continue

        if is_dvc_directory_out(out):
            message = (
                f"resource <{res.resource_id}> has a 'dvc add <dir>' output: "
                f"the '.dvc' file <{entry.name}> describes the directory "
                f"<{filename}>. 'dvc add <dir>' outputs are not supported by "
                f"GAIn: the '.dir' md5 sum such a sidecar declares is the "
                f"hash of a DVC cache object, not of any file in the "
                f"resource, so GAIn can never verify it against the bytes it "
                f"serves. DVC-manage the individual files instead: run 'dvc "
                f"add <file>' on each file of <{filename}> (and remove "
                f"<{entry.name}>)."
            )
            raise UnsupportedDvcDirectoryOutputError(message)

        md5 = out.get("md5")
        size = out.get("size")
        if not isinstance(md5, str) or not isinstance(size, int):
            logger.warning(
                "the '.dvc' file <%s> of <%s> declares no usable md5 sum and "
                "size for <%s>; ignoring it",
                entry.name, res.resource_id, filename)
            continue

        if filename not in manifest:
            logger.info(
                "filling manifest of <%s> with entry for <%s> based on "
                "dvc data only",
                res.resource_id, filename)

        result[filename] = ManifestEntry(filename, size, md5)

    return result


def _do_resource_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    res: GenomicResource,
    dry_run: bool,  # noqa: FBT001
    force: bool,  # noqa: FBT001
    use_dvc: bool,  # noqa: FBT001
) -> bool:
    # '.dvc' entries are always collected, in EVERY mode: they are the md5
    # sum of the file they describe by default, and the thing `--without-dvc`
    # verifies the bytes against. Gating this on `use_dvc` also deleted every
    # entry a sidecar is the only possible source for - a file that is not
    # materialised - from every manifest under `-D` (#251).
    prebuild_entries = collect_dvc_entries(proto, res)
    verify_content = not use_dvc

    manifest_update = proto.check_update_manifest(
        res, prebuild_entries, verify_content=verify_content)
    if not bool(manifest_update):
        logger.debug(
            "manifest of <%s> is up to date",
            res.get_genomic_resource_id_version())
    else:
        msg = (
            f"manifest of "
            f"<{res.get_genomic_resource_id_version()}> "
            f"should be updated; "
            f"entries to update in manifest "
            f"{sorted(manifest_update.entries_to_update)}"
        )
        if manifest_update.entries_to_delete:
            msg = (
                f"{msg}; "  # noqa: S608
                f"entries to delete from manifest "
                f"{sorted(manifest_update.entries_to_delete)}"
            )
        logger.warning(msg)

    if dry_run:
        return bool(manifest_update)

    if force or bool(manifest_update):
        # The manifest `check_update_manifest` returned IS the updated one:
        # every entry already carries its md5 sum, and the entries the scan
        # did not yield are already merged in. Deriving it again through
        # `build_manifest`/`update_manifest` would read every materialised
        # file a SECOND time under `--without-dvc`, which deliberately
        # bypasses the size and timestamp fast path (#251).
        logger.info(
            "updating manifest for resource <%s>...", res.resource_id)
        proto.save_manifest(res, manifest_update.manifest)

    return False


class ManifestOutcome(NamedTuple):
    """What a manifest pass over a set of resources found.

    ``updates_needed`` is keyed by the resources the pass got through, and
    valued by whether that resource's manifest is stale. ``failed`` names
    the resources whose manifest could not be built at all - today, only a
    resource whose content drifted from its ``.dvc`` sidecars under
    ``--without-dvc``; it has NO entry in ``updates_needed`` (#373).
    """

    updates_needed: dict[str, bool]
    failed: frozenset[str]


def _run_repo_manifest_command_internal(
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: bool | int | str) -> ManifestOutcome:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    use_dvc = cast(bool, kwargs.get("use_dvc", True))

    updates_needed = {}
    failed: set[str] = set()
    for res in resources:
        try:
            updates_needed[res.resource_id] = _do_resource_manifest_command(
                proto, res,
                dry_run=dry_run,
                force=force,
                use_dvc=use_dvc,
            )
        except DvcContentDriftError as err:
            # Collected, not raised: every drifted resource of the
            # repository is reported by one run, and the resources that
            # agree with their sidecars are still repaired (#373).
            _report_resource_failure(
                err, "could not verify", res.resource_id)
            failed.add(res.resource_id)

    return ManifestOutcome(updates_needed, frozenset(failed))


def _build_content_file(proto: FsspecReadWriteProtocol) -> None:
    """Build CONTENTS.json."""
    proto.build_content_file()


def _create_contents_db(
    proto: FsspecReadWriteProtocol,
) -> frozenset[str]:
    """Build the FTS SQLite index for the repository.

    Calls collect_index_info() on each resource's implementation to get
    field names and values.  Returns the ids of the resources that could
    not be indexed -- the index is repository-wide, so this walks EVERY
    resource, not only the ones the command selected; the ids returned are
    always the offending ones, never the selected one (gain#364).
    """
    sqlite_filepath = os.path.join(proto.root_path, ".CONTENTS.sqlite3")
    gzip_sqlite_filepath = os.path.join(
        proto.root_path, GR_SQLITE_META_FILE_NAME)

    current_md5 = proto.md5_contents()
    if os.path.exists(gzip_sqlite_filepath):
        try:
            raw = gzip.decompress(
                pathlib.Path(gzip_sqlite_filepath).read_bytes())
            conn = apsw.Connection(":memory:")
            conn.deserialize("main", raw)
            row = conn.execute(
                "SELECT value FROM contents_metadata "
                "WHERE key = 'contents_md5'",
            ).fetchone()
            if row and row[0] == current_md5:
                return frozenset()
        except Exception:  # noqa: BLE001
            logger.debug(
                "Could not read existing contents db; rebuilding",
                exc_info=True,
            )

    if os.path.exists(sqlite_filepath):
        os.remove(sqlite_filepath)
    if os.path.exists(gzip_sqlite_filepath):
        os.remove(gzip_sqlite_filepath)

    index_infos: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    failed: set[str] = set()
    for res in proto.get_all_resources():
        try:
            impl = build_resource_implementation(res)
            index_infos.append(impl.collect_index_info())
        except Exception as err:  # noqa: BLE001
            _report_resource_failure(
                err, "skipping FTS index for", res.resource_id)
            failed.add(res.resource_id)

    all_columns: dict[str, None] = {}
    for header, _ in index_infos:
        for col in header:
            all_columns[col] = None
    columns = list(all_columns.keys())

    with apsw.Connection(sqlite_filepath) as conn:
        conn.execute(
            "CREATE TABLE contents_metadata (key TEXT PRIMARY KEY, value TEXT)",
        )
        conn.execute(
            "INSERT INTO contents_metadata (key, value) VALUES (?, ?)",
            ("contents_md5", current_md5),
        )

        if columns:
            cols_str = ", ".join(columns)
            conn.execute(
                f"CREATE VIRTUAL TABLE contents USING fts5({cols_str})",
            )
            for header, row in index_infos:
                header_idx = {col: i for i, col in enumerate(header)}
                full_row = tuple(
                    row[header_idx[col]] if col in header_idx else ""
                    for col in columns
                )
                conn.execute(
                    f"INSERT INTO contents ({', '.join(columns)}) "  # noqa: S608
                    f"VALUES ({', '.join(['?'] * len(columns))})",
                    full_row,
                )

    # mtime=0 strips the current-time stamp from the gzip header
    # so re-running this on an unchanged repo produces identical
    # bytes (gzip.open's default writes the wall-clock time, which
    # changes every run). The OS byte at offset 9 is normalised to
    # 0xff for cross-Python-distribution determinism — see the
    # matching note in fsspec_protocol.build_content_file.
    raw_data = pathlib.Path(sqlite_filepath).read_bytes()
    gz = gzip.compress(raw_data, mtime=0)
    gz = gz[:9] + b"\xff" + gz[10:]
    pathlib.Path(gzip_sqlite_filepath).write_bytes(gz)
    os.remove(sqlite_filepath)
    return frozenset(failed)


def _run_repo_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
    **kwargs: bool | int | str,
) -> CommandResult:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    if dry_run and force:
        # A usage error, not a count of anything.
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return CommandResult(repo_failed=True)
    outcome = _run_repo_manifest_command_internal(
        proto, resources, **kwargs)
    if dry_run:
        # `updates_needed` is keyed by EVERY resource and valued by whether
        # that resource's manifest is stale, so its LENGTH is the size of
        # the repository -- which made `--dry-run` report a fully settled
        # repository as inconsistent, with a status equal to its resource
        # count.  Count the stale ones (gain#364).  A resource that could
        # not even be checked is certainly not up to date, so it counts too.
        return CommandResult(
            needs_update=sum(
                1 for stale in outcome.updates_needed.values() if stale
            ) + len(outcome.failed),
            failed=outcome.failed)
    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto)
    return CommandResult(failed=outcome.failed)


def _find_resources(
    proto: ReadOnlyRepositoryProtocol,
    repo_url: str,
    **kwargs: str | bool | int,
) -> Sequence[GenomicResource]:
    resource_pattern = cast(str, kwargs.get("resource"))

    if resource_pattern is not None:
        return [
            res for res in proto.get_all_resources()
            if fnmatch.fnmatch(res.resource_id, resource_pattern)
        ]

    if urlparse(repo_url).scheme not in {"file", ""}:
        logger.error(
            "resource not specified but the repository URL %s "
            "is not local filesystem repository", repo_url)
        return []

    cwd = os.getcwd()
    resource_dir = find_directory_with_a_file(GR_CONF_FILE_NAME, cwd)
    if resource_dir is not None:

        rid_ver = os.path.relpath(resource_dir, repo_url)
        resource_id, version = parse_gr_id_version_token(rid_ver)

        res = proto.get_resource(
            resource_id,
            version_constraint=f"={version_tuple_to_string(version)}")
        return [res]

    result = []
    for res_dir in find_subdirectories_with_a_file(GR_CONF_FILE_NAME, cwd):
        rid_ver = os.path.relpath(res_dir, repo_url)
        resource_id, version = parse_gr_id_version_token(rid_ver)

        res = proto.get_resource(
            resource_id,
            version_constraint=f"={version_tuple_to_string(version)}")
        result.append(res)
    if result:
        return result

    logger.error("Can't find resource starting from %s", cwd)
    return []


def _read_stats_hash(
        proto: ReadWriteRepositoryProtocol,
        implementation: GenomicResourceImplementation) -> bytes | None:
    res = implementation.resource
    stats_dir = ResourceStatistics.get_statistics_folder()
    if not proto.file_exists(res, f"{stats_dir}/stats_hash"):
        return None
    with proto.open_raw_file(
        res, f"{stats_dir}/stats_hash", mode="rb",
    ) as infile:
        return cast(bytes, infile.read())


def _store_stats_hash(
    proto: ReadWriteRepositoryProtocol,
    resource: GenomicResource,
) -> bool:

    try:
        impl = build_resource_implementation(resource)
        stats_dir = ResourceStatistics.get_statistics_folder()
        if stats_dir is None:
            logger.warning(
                "Couldn't store stats hash for %s; unable to get stats dir",
                resource.resource_id)
            return False
        with proto.open_raw_file(
            resource, f"{stats_dir}/stats_hash", mode="wb",
        ) as outfile:
            stats_hash = impl.calc_statistics_hash()
            outfile.write(stats_hash)
    except Exception as err:  # noqa: BLE001
        _report_resource_failure(
            err, "couldn't store statistics hash for", resource.resource_id)
        return False
    return True


def _collect_impl_stats_tasks(
    graph: TaskGraph,
    proto: ReadWriteRepositoryProtocol,
    impl: GenomicResourceImplementation,
    grr: GenomicResourceRepo,
    *,
    region_size: int,
) -> None:

    tasks = impl.create_statistics_build_tasks(
        region_size=region_size, grr=grr)

    last_task: list[Task] = [tasks[-1].task] if len(tasks) > 0 else []
    hash_task = graph.make_task(
        f"{impl.resource.get_full_id()}_stats_hash_rebuild",
        _store_stats_hash,
        args=[proto, impl.resource],
        deps=last_task,
    )
    if len(tasks) == 1:
        merged_task = chain_tasks(tasks[0], hash_task)
        graph.add_task(merged_task)
    else:
        graph.add_tasks(tasks)
        graph.add_task(hash_task)


def _stats_need_rebuild(
        proto: ReadWriteRepositoryProtocol,
        impl: GenomicResourceImplementation) -> bool:
    """Check if an implementation's stats need rebuilding."""
    current_hash = impl.calc_statistics_hash()
    stored_hash = _read_stats_hash(proto, impl)

    if stored_hash is None:
        logger.info(
            "No hash stored for <%s>; needs update",
            impl.resource.get_full_id(),
        )
        return True

    if stored_hash != current_hash:
        logger.info(
            "Stored hash for <%s> is outdated; needs update",
            impl.resource.get_full_id(),
        )
        return True

    logger.debug(
        "<%s> statistics hash is up to date", impl.resource.get_full_id(),
    )
    return False


def _statistics_not_built(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
) -> frozenset[str]:
    """Return the ids of resources whose statistics did not get built.

    ``TaskGraphCli.process_graph`` reports only whether SOME task failed,
    and ``_store_stats_hash`` reports its own failure by returning
    ``False`` from inside a worker -- neither can name a resource.  So the
    repository itself is asked instead: the last task of every resource
    writes its ``stats_hash``, so a resource whose stored hash is still
    missing or stale after the graph has run is a resource whose
    statistics were not built (gain#364).

    This is the ONLY per-resource attribution available for an execution
    failure, and it is exact: it observes the outcome rather than
    guessing at the cause.
    """
    not_built: set[str] = set()
    for res in resources:
        try:
            impl = build_resource_implementation(res)
            if _read_stats_hash(proto, impl) != impl.calc_statistics_hash():
                logger.error(
                    "statistics of <%s> were not built", res.resource_id)
                not_built.add(res.resource_id)
        except Exception as err:  # noqa: BLE001
            _report_resource_failure(
                err, "could not check the statistics of", res.resource_id)
            not_built.add(res.resource_id)
    return frozenset(not_built)


def _run_repo_stats_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: bool | int | str) -> CommandResult:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    region_size = cast(int, kwargs.get("region_size", 3_000_000))

    if dry_run and force:
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return CommandResult(repo_failed=True)

    outcome = _run_repo_manifest_command_internal(
        proto, resources, **kwargs)
    updates_needed = outcome.updates_needed

    graph = TaskGraph()

    needs_update = 0
    failed: set[str] = set(outcome.failed)
    stats_resources: list[GenomicResource] = []
    for res in resources:
        if res.resource_id in failed:
            # Its manifest could not be built, so there is nothing to
            # certify the statistics against -- and rebuilding them would
            # write new files into a resource the run is already failing on.
            logger.warning(
                "not building the statistics of <%s>: "
                "it already failed in this run", res.resource_id)
            continue
        # Four operations under one `try` -- building the implementation,
        # looking up the manifest update, comparing the statistics hash and
        # collecting the statistics tasks -- so the message names none of
        # them and carries the cause instead (gain#364).
        try:
            impl = build_resource_implementation(res)
            manifest_updated = updates_needed[res.resource_id]
            needs_rebuild = manifest_updated or _stats_need_rebuild(proto, impl)
            if dry_run:
                if needs_rebuild:
                    logger.info(
                        "Statistics of <%s> needs update", res.resource_id)
                    needs_update += 1
            elif force or needs_rebuild:
                _collect_impl_stats_tasks(
                    graph, proto, impl, repo,
                    region_size=region_size)
                stats_resources.append(res)
        except Exception as err:  # noqa: BLE001
            # Collected, not raised: the resources after this one in the
            # repository are still repaired.
            _report_resource_failure(
                err, "skipping statistics for", res.resource_id)
            failed.add(res.resource_id)

    if dry_run:
        # A resource that could not even be checked is certainly not up to
        # date, so it counts towards the "how many need an update" status a
        # dry run exits with -- that keeps the status a COUNT rather than
        # collapsing it to a bare 1 (gain#364).
        return CommandResult(
            needs_update=needs_update + len(failed),
            failed=frozenset(failed))

    repo_failed = False
    if len(graph.tasks) > 0:
        modified_kwargs = copy.copy(kwargs)
        modified_kwargs["command"] = "run"
        modified_kwargs["keep_going"] = True
        if modified_kwargs.get("task_log_dir") is None:
            repo_url = proto.get_url()
            modified_kwargs["task_log_dir"] = \
                fs_utils.join(repo_url, ".task-log")

        # `keep_going=True` means a failing task does not raise -- the
        # only report of it is this return value, and discarding it was
        # the whole of gain#364 for an execution failure.
        if not TaskGraphCli.process_graph(
                graph, task_progress_mode=False, **modified_kwargs):
            logger.error("building the statistics of GRR <%s> failed",
                         proto.get_url())
            repo_failed = True

    if stats_resources:
        # Run unconditionally, not only when the graph reported failure: a
        # task can also fail SILENTLY (`_store_stats_hash` catches its own
        # exception and returns False), and the outcome is what matters.
        not_built = _statistics_not_built(proto, stats_resources)
        failed |= not_built
        if not_built:
            # Attributed, so the repository-level flag would say nothing
            # the per-resource list does not say better.
            repo_failed = False

        # Rebuilding the statistics wrote new files into these resources, so
        # their manifests have to be rebuilt. `use_dvc=True` (the size and
        # timestamp fast path) is deliberate even under `--without-dvc`: the
        # manifest pass above has just verified the content of every
        # materialised file of this very repository, in this very command,
        # and persisted the resulting states - so re-verifying them here
        # would be a second full read of the repository, not a second
        # opinion (#251). The freshly written statistics files have no state
        # and are hashed here. It cannot fail: the default mode never
        # verifies a sidecar, so it has no drift to report.
        _run_repo_manifest_command_internal(
            proto, stats_resources,
            dry_run=False, force=True, use_dvc=True)

    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto)
    # The FTS index walks the whole repository, so the ids it returns may
    # name resources this command did not select -- they are reported
    # under their own ids, and the run fails, but the selected resource is
    # not blamed for them.
    failed |= _create_contents_db(proto)
    return CommandResult(
        failed=frozenset(failed), repo_failed=repo_failed)


def _run_repo_repair_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: str | bool | int) -> CommandResult:
    return _run_repo_info_command(repo, proto, resources, **kwargs)


def _run_repo_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: str | bool | int) -> CommandResult:
    result = _run_repo_stats_command(repo, proto, resources, **kwargs)

    dry_run = cast(bool, kwargs.get("dry_run", False))
    if dry_run:
        return result

    assert isinstance(proto, FsspecReadWriteProtocol)
    proto.build_index_info()
    failed = set(result.failed)
    for res in resources:
        if res.resource_id in failed:
            # Something GAIn was asked to do to it already failed -- most
            # often building its statistics, which would make its info
            # page render from placeholder histograms, overwriting
            # whatever good page is already there (gain#364).
            logger.warning(
                "not regenerating the info page of <%s>: "
                "it already failed in this run", res.resource_id)
            continue
        try:
            _do_resource_info_command(repo, proto, res)
        except Exception as err:  # noqa: BLE001
            _report_resource_failure(
                err, "skipping info page for", res.resource_id)
            failed.add(res.resource_id)
    return dataclasses.replace(result, failed=frozenset(failed))


def _write_resource_file_if_changed(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource,
        filename: str,
        content: str) -> None:
    """Write ``content`` to a resource file only if it differs.

    The generated info pages are deterministic, so regenerating them on
    an unchanged repo produces identical bytes. Skipping the write in
    that case keeps the file's mtime stable, so re-running repo-repair
    is idempotent (and mtime-based consumers don't see spurious churn).
    """
    if proto.file_exists(res, filename):
        with proto.open_raw_file(res, filename, "rt") as infile:
            if infile.read() == content:
                return
    with proto.open_raw_file(res, filename, mode="wt") as outfile:
        outfile.write(content)


def _do_resource_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> None:
    implementation = build_resource_implementation(res)

    # Both pages are rendered BEFORE either is written: writing index.html
    # first and only then rendering the statistics page left a rewritten
    # page behind whenever the second render raised, while the run
    # reported the page had been protected (gain#364).
    info = implementation.get_info(repo=repo)
    statistics_info = implementation.get_statistics_info(repo=repo)

    _write_resource_file_if_changed(proto, res, "index.html", info)
    _write_resource_file_if_changed(
        proto, res, "statistics/index.html", statistics_info)


# The repository-scoped and resource-scoped commands differ only in how they
# choose the resources they work on; everything after that -- which command
# function runs, how a failure is reported, and what the process exits with
# -- is shared, so it is written once (see _run_management_command and
# _exit_with).
_REPO_COMMANDS = frozenset({
    "repo-manifest", "repo-stats", "repo-info", "repo-repair"})
_RESOURCE_COMMANDS = frozenset({
    "resource-manifest", "resource-stats",
    "resource-info", "resource-repair"})


def cli_manage(cli_args: list[str] | None = None) -> None:
    """Provide CLI for repository management."""
    # pylint: disable=too-many-branches,too-many-statements
    if cli_args is None:
        cli_args = sys.argv[1:]
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
    _configure_repo_stats_subparser(commands_parser)
    _configure_resource_stats_subparser(commands_parser)
    _configure_repo_info_subparser(commands_parser)
    _configure_resource_info_subparser(commands_parser)
    _configure_repo_repair_subparser(commands_parser)
    _configure_resource_repair_subparser(commands_parser)
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
        _run_repo_init_command(**vars(args))
        return

    repo_url = _get_repo_url(args)
    repo = _create_grr_repo(args, repo_url)
    proto = _create_proto(repo_url, args.extra_args)
    if command == "list":
        _run_list_command(proto, args)
        return

    if not isinstance(proto, ReadWriteRepositoryProtocol):
        raise TypeError(
            f"resource management works with RW protocols; "
            f"{proto.proto_id} ({proto.scheme}) is read only")

    resources: Sequence[GenomicResource]

    if command in _REPO_COMMANDS:
        resources = list(proto.get_all_resources())
        if len(resources) == 0:
            logger.info("repository <%s> has no resources", repo_url)
            sys.exit(0)
    elif command in _RESOURCE_COMMANDS:
        resources = _find_resources(proto, repo_url, **vars(args))
        if not resources:
            logger.error("resource not found...")
            sys.exit(1)
    else:
        logger.error(
            "Unknown command %s. The known commands are index, "
            "list and histogram", command)
        sys.exit(1)

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

    The command name is read out of ``kwargs`` rather than taken as a
    parameter: the whole parsed argument namespace is forwarded to the
    command functions (the task-graph options live in it), ``command``
    included.
    """
    command = cast(str, kwargs["command"])
    try:
        if command.endswith("-manifest"):
            return _run_repo_manifest_command(proto, resources, **kwargs)
        if command.endswith("-stats"):
            return _run_repo_stats_command(
                repo, proto, resources, **kwargs)
        if command.endswith("-info"):
            return _run_repo_info_command(
                repo, proto, resources, **kwargs)
        if command.endswith("-repair"):
            return _run_repo_repair_command(
                repo, proto, resources, **kwargs)
        # Never fall through: repair is destructive, and a command name
        # added to _REPO_COMMANDS/_RESOURCE_COMMANDS but not handled here
        # would otherwise silently run a full repair.
        logger.error("Unknown command %s.", command)
        sys.exit(1)
    except UnsupportedDvcDirectoryOutputError as ex:
        # A resource GAIn cannot verify: refuse it outright, loudly.
        logger.error("%s", ex)  # noqa: TRY400
        sys.exit(1)
    except ValueError as ex:
        # The repository itself, rather than one of its resources, is
        # unusable -- there is no resource id to attribute this to.
        logger.error(  # noqa: TRY400
            "Misconfigured repository %s; %s", repo_url, ex)
        logger.debug("repository %s is misconfigured", repo_url, exc_info=True)
        logger.warning("inconsistent GRR <%s> state", repo_url)
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
    grr_definition = {
        "id": "cli_grr",
        "type": "group",
        "children": [
            {
                "id": "local",
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
            repo_url = find_directory_with_a_file(GR_CONTENTS_FILE_NAME[:-3])
        if repo_url is None:
            logger.error(
                "Can't find repository starting from: %s", os.getcwd())
            sys.exit(1)
        repo_url = str(repo_url)
        print(f"working with repository: {repo_url}")

    return cast(str, repo_url)


def _create_proto(
    repo_url: str, extra_args: str = "",
) -> ReadWriteRepositoryProtocol:
    url = urlparse(repo_url)

    if url.scheme in {"file", ""} and not os.path.isabs(repo_url):
        repo_url = os.path.abspath(repo_url)

    kwargs: dict[str, str] = {}
    if extra_args:
        parsed = [tuple(a.split("=")) for a in extra_args.split(",")]
        kwargs = {p[0]: p[1] for p in parsed}

    proto = build_fsspec_protocol(
        proto_id="manage", root_url=repo_url, **kwargs)
    if not isinstance(proto, ReadWriteRepositoryProtocol):
        raise TypeError(f"repository protocol is not writable: {repo_url}")
    return proto


def cli_browse(cli_args: list[str] | None = None) -> None:
    """Provide CLI for repository browsing."""
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
        help="FTS search term to filter resources.")
    group.add_argument(
        "-t", "--type", type=str, default=None,
        help="Filter resources by type.")
    group.add_argument(
        "--summary", default=False, action="store_true",
        help="Print a summary for each resource below its listing line.")

    parser.add_argument(
        "--bytes",
        default=False,
        action="store_true",
        help="Print the resource size in bytes",
    )

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
    _run_list_command(repo, args)
