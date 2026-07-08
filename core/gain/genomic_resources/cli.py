"""Provides CLI for management of genomic resources repositories."""
import argparse
import copy
import fnmatch
import gzip
import os
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlparse

import apsw
import yaml
from cerberus.schema import SchemaError

from gain import __version__, logging
from gain.genomic_resources.cached_repository import GenomicResourceCachedRepo
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
        help="use '.dvc' files if present to get md5 sum of resource files "
        "(default)")
    group.add_argument(
        "-D", "--without-dvc", default=True,
        action="store_false", dest="use_dvc",
        help="calculate the md5 sum if necessary of resource files; "
        "do not use '.dvc' files to get md5 sum of resource files")


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


def collect_dvc_entries(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> dict[str, ManifestEntry]:
    """Collect manifest entries defined by .dvc files."""
    result = {}
    manifest = proto.collect_resource_entries(res)
    for entry in manifest:
        if not entry.name.endswith(".dvc"):
            continue
        filename = entry.name[:-4]
        basename = os.path.basename(filename)

        if filename not in manifest:
            logger.info(
                "filling manifest of <%s> with entry for <%s> based on "
                "dvc data only",
                res.resource_id, filename)

        with proto.open_raw_file(res, entry.name, "rt") as infile:
            content = infile.read()
            dvc = yaml.safe_load(content)
            for data in dvc["outs"]:
                if data["path"] == basename:
                    result[filename] = \
                        ManifestEntry(filename, data["size"], data["md5"])

    return result


def _do_resource_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    res: GenomicResource,
    dry_run: bool,  # noqa: FBT001
    force: bool,  # noqa: FBT001
    use_dvc: bool,  # noqa: FBT001
) -> bool:
    prebuild_entries = {}
    if use_dvc:
        prebuild_entries = collect_dvc_entries(proto, res)

    manifest_update = proto.check_update_manifest(res, prebuild_entries)
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

    if force:
        logger.info(
            "building manifest for resource <%s>...", res.resource_id)
        manifest = proto.build_manifest(
            res, prebuild_entries)
        proto.save_manifest(res, manifest)
        return False

    if bool(manifest_update):
        logger.info(
            "updating manifest for resource <%s>...", res.resource_id)
        manifest = proto.update_manifest(
            res, prebuild_entries)
        proto.save_manifest(res, manifest)
        return False
    return bool(manifest_update)


def _run_repo_manifest_command_internal(
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: bool | int | str) -> dict[str, Any]:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    use_dvc = cast(bool, kwargs.get("use_dvc", True))

    updates_needed = {}
    for res in resources:
        updates_needed[res.resource_id] = _do_resource_manifest_command(
            proto, res,
            dry_run=dry_run,
            force=force,
            use_dvc=use_dvc,
        )

    return updates_needed


def _build_content_file(proto: FsspecReadWriteProtocol) -> None:
    """Build CONTENTS.json."""
    proto.build_content_file()


def _create_contents_db(
    proto: FsspecReadWriteProtocol,
) -> None:
    """Build the FTS SQLite index for the repository.

    Calls collect_index_info() on each resource's implementation to get
    field names and values. If an implementation cannot be constructed,
    falls back to the standard meta fields from the resource config.
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
                return
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
    for res in proto.get_all_resources():
        try:
            impl = build_resource_implementation(res)
            index_infos.append(impl.collect_index_info())
        except Exception:  # noqa: BLE001
            logger.warning(
                "Skipping FTS index for <%s>: could not build implementation",
                res.resource_id,
            )

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


def _run_repo_manifest_command(
    proto: ReadWriteRepositoryProtocol,
    resources: Sequence[GenomicResource],
    **kwargs: bool | int | str,
) -> int:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    if dry_run and force:
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return 1
    updates_needed = _run_repo_manifest_command_internal(
        proto, resources, **kwargs)
    if dry_run:
        return len(updates_needed)
    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto)
    return 0


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
    except Exception:  # noqa: BLE001
        logger.warning(
            "Couldn't store stats hash for %s",
            resource.resource_id)
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


def _run_repo_stats_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: bool | int | str) -> int:
    dry_run = cast(bool, kwargs.get("dry_run", False))
    force = cast(bool, kwargs.get("force", False))
    use_dvc = cast(bool, kwargs.get("use_dvc", True))
    region_size = cast(int, kwargs.get("region_size", 3_000_000))

    if dry_run and force:
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return 0

    updates_needed = _run_repo_manifest_command_internal(
        proto, resources, **kwargs)

    graph = TaskGraph()

    status = 0
    stats_resources: list[GenomicResource] = []
    for res in resources:
        try:
            impl = build_resource_implementation(res)
            manifest_updated = updates_needed[res.resource_id]
            needs_rebuild = manifest_updated or _stats_need_rebuild(proto, impl)
            if dry_run:
                if needs_rebuild:
                    logger.info(
                        "Statistics of <%s> needs update", res.resource_id)
                    status += 1
            elif force or needs_rebuild:
                _collect_impl_stats_tasks(
                    graph, proto, impl, repo,
                    region_size=region_size)
                stats_resources.append(res)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Skipping stats for <%s>: could not build implementation",
                res.resource_id,
            )

    if dry_run:
        return status

    if len(graph.tasks) > 0:
        modified_kwargs = copy.copy(kwargs)
        modified_kwargs["command"] = "run"
        modified_kwargs["keep_going"] = True
        if modified_kwargs.get("task_log_dir") is None:
            repo_url = proto.get_url()
            modified_kwargs["task_log_dir"] = \
                fs_utils.join(repo_url, ".task-log")

        TaskGraphCli.process_graph(
            graph, task_progress_mode=False, **modified_kwargs)

    if stats_resources:
        _run_repo_manifest_command_internal(
            proto, stats_resources,
            dry_run=False, force=True, use_dvc=use_dvc)

    assert isinstance(proto, FsspecReadWriteProtocol)
    _build_content_file(proto)
    _create_contents_db(proto)
    return 0


def _run_repo_repair_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: str | bool | int) -> int:
    return _run_repo_info_command(repo, proto, resources, **kwargs)


def _run_repo_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        **kwargs: str | bool | int) -> int:
    status = _run_repo_stats_command(repo, proto, resources, **kwargs)

    dry_run = cast(bool, kwargs.get("dry_run", False))
    if dry_run:
        return status

    assert isinstance(proto, FsspecReadWriteProtocol)
    proto.build_index_info()
    for res in resources:
        try:
            _do_resource_info_command(repo, proto, res)
        except ValueError:
            logger.exception(
                "Failed to generate repo index for %s",
                res.resource_id,
            )
        except SchemaError:
            logger.exception(
                "Resource %s has an invalid configuration",
                res.resource_id,
            )
        except BaseException:  # pylint: disable=broad-except
            logger.exception(
                "Failed to load %s",
                res.resource_id,
            )
    return 0


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

    _write_resource_file_if_changed(
        proto, res, "index.html",
        implementation.get_info(repo=repo))

    _write_resource_file_if_changed(
        proto, res, "statistics/index.html",
        implementation.get_statistics_info(repo=repo))


def cli_manage(cli_args: list[str] | None = None) -> None:
    """Provide CLI for repository management."""
    # flake8: noqa: C901
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

    if command in {"repo-manifest", "repo-stats", "repo-info", "repo-repair"}:
        status = 0
        resources = list(proto.get_all_resources())
        if len(resources) == 0:
            logger.info("repository <%s> has no resources", repo_url)
            sys.exit(0)

        try:
            if command == "repo-manifest":
                status = _run_repo_manifest_command(
                    proto, resources, **vars(args))
            elif command == "repo-stats":
                status = _run_repo_stats_command(
                    repo, proto, resources, **vars(args))
            elif command == "repo-info":
                status = _run_repo_info_command(
                    repo, proto, resources, **vars(args))
            elif command == "repo-repair":
                status = _run_repo_repair_command(
                    repo, proto, resources, **vars(args))
            else:
                logger.error(
                    "Unknown command %s.", command)
                sys.exit(1)
            if status == 0:
                logger.info("GRR <%s> is consistent", repo_url)
                return
        except ValueError as ex:
            logger.error(  # noqa: TRY400
                "Misconfigured repository %s; %s", repo_url, ex)
            status = 1

        logger.warning("inconsistent GRR <%s> state", repo_url)
        sys.exit(status)
    elif command in {
            "resource-manifest", "resource-stats",
            "resource-info", "resource-repair"}:
        status = 0
        resources = _find_resources(proto, repo_url, **vars(args))
        if not resources:
            logger.error("resource not found...")
            sys.exit(1)
        assert resources
        try:
            if command == "resource-manifest":
                status = _run_repo_manifest_command(
                    proto, resources, **vars(args))
            elif command == "resource-stats":
                status = _run_repo_stats_command(
                    repo, proto, resources, **vars(args))
            elif command == "resource-info":
                status = _run_repo_info_command(
                    repo, proto, resources, **vars(args))
            elif command == "resource-repair":
                status = _run_repo_repair_command(
                    repo, proto, resources, **vars(args))
            else:
                logger.error(
                    "Unknown command %s.", command)
                sys.exit(1)
            if status == 0:
                logger.info("GRR <%s> is consistent", repo_url)
                return
        except ValueError:
            logger.exception("unexpected exception")
            status = 1
        logger.warning("inconsistent GRR <%s> state", repo_url)
        sys.exit(status)
    else:
        logger.error(
            "Unknown command %s. The known commands are index, "
            "list and histogram", command)
        sys.exit(1)


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
