"""The statistics commands of ``grr_manage``.

The ``repo-stats`` and ``resource-stats`` commands: the selection of the
resources whose statistics are out of date, the task graph that builds
them, and the statistics hash that records a build.
"""
import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any, cast

from gain import logging
from gain.genomic_resources.cli_common import (
    CommandResult,
    _note_stale_repository_index,
)
from gain.genomic_resources.cli_errors import (
    report_resource_failure,
)
from gain.genomic_resources.cli_manifest import (
    ManifestOutcome,
    _publish_repository_contents,
    _run_repo_manifest_command_internal,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    DerivedFilesState,
    GenomicResourceImplementation,
    ResourceStatistics,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import Task, TaskGraph, chain_tasks
from gain.utils import fs_utils

logger = logging.getLogger("grr_manage")


def _stats_hash_path() -> str:
    """Return the resource-relative path of the stored statistics hash."""
    return f"{ResourceStatistics.get_statistics_folder()}/stats_hash"


def _read_stats_hash(
        proto: ReadWriteRepositoryProtocol,
        implementation: GenomicResourceImplementation) -> bytes | None:
    res = implementation.resource
    if not proto.file_exists(res, _stats_hash_path()):
        return None
    with proto.open_raw_file(res, _stats_hash_path(), mode="rb") as infile:
        return cast(bytes, infile.read())


def _store_stats_hash(
    proto: ReadWriteRepositoryProtocol,
    resource: GenomicResource,
) -> bool:

    try:
        impl = build_resource_implementation(resource)
        with proto.open_raw_file(
            resource, _stats_hash_path(), mode="wb",
        ) as outfile:
            stats_hash = impl.calc_statistics_hash()
            outfile.write(stats_hash)
    except Exception as err:  # ruff: ignore[blind-except]
        report_resource_failure(
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
    """Add a resource's statistics tasks to ``graph``, ending in its hash.

    The stored hash is removed first, before any task is added, so only a
    rebuild that succeeds leaves one behind and a resource whose hash
    cannot be removed fails with none of its tasks in the graph.
    """
    proto.delete_resource_file(impl.resource, _stats_hash_path())
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


def _report_schema_stale_statistics(
    impl: GenomicResourceImplementation,
) -> bool:
    """Log a hash-current resource whose statistics predate the schema.

    True when it did.  One INFO line naming each declared file that is
    missing or carries an older ``format_version`` than its writer
    stamps now, and the ``-f`` remedy.  A file that cannot be read as a
    JSON document is left to whatever reads it.  Reports only: nothing
    here feeds the task graph, the dry-run count or the failed set.
    """
    stale: list[str] = []
    for stored in impl.stored_statistics():
        try:
            with impl.resource.open_raw_file(
                    stored.file, mode="rb") as infile:
                version = stored.stored_version(infile.read())
        except FileNotFoundError:
            stale.append(
                f"{stored.file} is missing "
                f"(current is {stored.format_version})")
            continue
        if version is not None and version < stored.format_version:
            stale.append(
                f"{stored.file} is at format version {version} "
                f"(current is {stored.format_version})")
    if not stale:
        return False
    resource_id = impl.resource.resource_id
    logger.info(
        "statistics of <%s> predate the current schema: %s; "
        "rebuild with `grr_manage resource-stats -r %s -f`",
        resource_id, ", ".join(stale), resource_id)
    return True


def _warn_schema_stale(count: int) -> None:
    """One WARNING per run, last, counting the resources behind the schema.

    Nothing when the count is zero.  Last, where a repository-wide
    run's operator reads -- so ``_run_stats_core`` calls it once on
    each of its exits; the per-resource lines name the files.
    """
    if not count:
        return
    logger.warning(
        "%d resource(s) carry statistics that predate the current "
        "schema; rebuild them with "
        "`grr_manage resource-stats -r <resource_id> -f`",
        count)


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
        except Exception as err:  # ruff: ignore[blind-except]
            report_resource_failure(
                err, "could not check the statistics of", res.resource_id)
            not_built.add(res.resource_id)
    return frozenset(not_built)


def _run_stats_graph(
    graph: TaskGraph, proto: ReadWriteRepositoryProtocol,
    task_graph_args: Mapping[str, Any],
) -> bool:
    """Run the statistics tasks; whether the run failed as a whole.

    An empty graph is nothing to run.  Otherwise the graph runs to the
    end whatever fails (``keep_going``), and the only report of a
    failed task is the return value -- discarding it was the whole of
    gain#364 for an execution failure.  Task logs go under the
    repository's ``.task-log`` unless the caller named a directory.
    """
    if len(graph.tasks) == 0:
        return False
    graph_args = dict(task_graph_args)
    graph_args["command"] = "run"
    graph_args["keep_going"] = True
    # Task-graph plumbing, not a grr_manage option: an in-process caller
    # may name no task-log directory at all.
    if graph_args.get("task_log_dir") is None:
        repo_url = proto.get_url()
        graph_args["task_log_dir"] = \
            fs_utils.join(repo_url, ".task-log")
    if not TaskGraphCli.process_graph(
            graph, task_progress_mode=False, **graph_args):
        logger.error("building the statistics of GRR <%s> failed",
                     proto.get_url())
        return True
    return False


@dataclasses.dataclass
class _StatsSelection:
    """What the per-resource pass of ``_run_stats_core`` found and did."""

    failed: set[str]
    graph: TaskGraph = dataclasses.field(default_factory=TaskGraph)
    needs_update: int = 0
    schema_stale: int = 0
    # Out of date, but with a payload this checkout does not have: a
    # dry run counts them apart, a real run leaves them for a checkout
    # that has it.
    not_buildable: int = 0
    stats_resources: list[GenomicResource] = dataclasses.field(
        default_factory=list)
    # Resources whose derived files alone were rewritten: no task ran for
    # them, but their manifests have new content to record all the same.
    derived_resources: list[GenomicResource] = dataclasses.field(
        default_factory=list)


def _select_resource_stats(
        selection: _StatsSelection,
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource, updates_needed: Mapping[str, bool], *,
        dry_run: bool, force: bool, region_size: int) -> None:
    """Decide, and on a real run start, the statistics work of one resource.

    Five operations -- building the implementation, looking up the
    manifest update, comparing the statistics hash, checking the derived
    files and collecting the statistics tasks -- run here under the one
    ``try`` of the caller, so its message names none of them and
    carries the cause instead (gain#364).
    """
    impl = build_resource_implementation(res)
    manifest_updated = updates_needed[res.resource_id]
    needs_rebuild = manifest_updated or _stats_need_rebuild(proto, impl)
    # A second gate, beside the hash's and independent of it: a
    # file the kind derives at repair from inputs the hash must
    # not learn about (a score's chromosome lengths from its
    # `reference_genome` label).  Stale on its own, it is
    # rewritten on its own -- never at the price of a rebuild,
    # which rewrites it anyway -- and one whose payload is not
    # here is left alone: the kind says so, and this loop
    # decides nothing about DVC (gain#1576).
    derived = impl.derived_files_state(repo)
    if dry_run:
        _count_dry_run_stats(
            selection, res, derived, needs_rebuild=needs_rebuild)
    else:
        _start_resource_stats(
            selection, repo, proto, res, impl, derived,
            rebuild=force or needs_rebuild, region_size=region_size)
    # A third state, reported and never acted on: the hash is
    # current, so nothing rebuilds the statistics -- a derived
    # file's rewrite does not touch them -- and a file behind
    # the schema stays behind it (gain#1586).
    if not (needs_rebuild or force) \
            and _report_schema_stale_statistics(impl):
        selection.schema_stale += 1


def _count_dry_run_stats(
        selection: _StatsSelection,
        res: GenomicResource,
        derived: DerivedFilesState, *,
        needs_rebuild: bool) -> None:
    """Count the statistics work that a real run would do for ``res``."""
    if needs_rebuild or derived is not DerivedFilesState.CURRENT:
        logger.info(
            "Statistics of <%s> needs update", res.resource_id)
        selection.needs_update += 1
    if derived is DerivedFilesState.PAYLOAD_ABSENT:
        selection.not_buildable += 1


def _start_resource_stats(
        selection: _StatsSelection,
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource,
        impl: GenomicResourceImplementation,
        derived: DerivedFilesState, *,
        rebuild: bool, region_size: int) -> None:
    """Start the statistics tasks, or the derived files rewrite, of ``res``.

    ``rebuild`` is true on a forced run and when the statistics are out
    of date.
    """
    if rebuild:
        _collect_impl_stats_tasks(
            selection.graph, proto, impl, repo,
            region_size=region_size)
        selection.stats_resources.append(res)
    elif derived is DerivedFilesState.STALE:
        impl.rebuild_derived_files(repo)
        selection.derived_resources.append(res)


def _select_stats(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        outcome: ManifestOutcome, *,
        dry_run: bool, force: bool, region_size: int) -> _StatsSelection:
    """Run the per-resource pass of ``_run_stats_core``."""
    selection = _StatsSelection(failed=set(outcome.failed))
    for res in resources:
        if res.resource_id in selection.failed:
            # Its manifest could not be built, so there is nothing to
            # certify the statistics against -- and rebuilding them would
            # write new files into a resource the run is already failing on.
            logger.warning(
                "not building the statistics of <%s>: "
                "it already failed in this run", res.resource_id)
            continue
        try:
            _select_resource_stats(
                selection, repo, proto, res, outcome.updates_needed,
                dry_run=dry_run, force=force, region_size=region_size)
        except Exception as err:  # ruff: ignore[blind-except]
            # Collected, not raised: the resources after this one in the
            # repository are still repaired.
            report_resource_failure(
                err, "skipping statistics for", res.resource_id)
            selection.failed.add(res.resource_id)
    return selection


def _dry_run_stats_result(selection: _StatsSelection) -> CommandResult:
    """Report what a dry run of ``_run_stats_core`` found."""
    _warn_schema_stale(selection.schema_stale)
    # A resource that could not even be checked is certainly not up to
    # date, so it counts towards the "how many need an update" status a
    # dry run exits with -- that keeps the status a COUNT rather than
    # collapsing it to a bare 1 (gain#364).
    needs_update = selection.needs_update + len(selection.failed)
    if needs_update:
        logger.info(
            "%d resources need update, %d of them not buildable here",
            needs_update, selection.not_buildable)
    return CommandResult(
        needs_update=needs_update, failed=frozenset(selection.failed))


def _build_selected_stats(
        selection: _StatsSelection,
        proto: ReadWriteRepositoryProtocol,
        outcome: ManifestOutcome,
        task_graph_args: Mapping[str, Any]) -> CommandResult:
    """Run the collected statistics tasks and refresh the manifests."""
    failed = selection.failed
    stats_resources = selection.stats_resources
    repo_failed = _run_stats_graph(selection.graph, proto, task_graph_args)

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

    written = [*stats_resources, *selection.derived_resources]
    if written:
        # Rebuilding the statistics (or the derived files alone) wrote new
        # files into these resources, so their manifests have to be
        # rebuilt. `use_dvc=True` (the size and
        # timestamp fast path) is deliberate even under `--without-dvc`: the
        # manifest pass above has just verified the content of every
        # materialised file of this very repository, in this very command,
        # and persisted the resulting states - so re-verifying them here
        # would be a second full read of the repository, not a second
        # opinion (#251). The freshly written statistics files have no state
        # and are hashed here. Its outcome is collected: the default mode
        # has no sidecar drift to report, but a file can still turn out
        # undescribable between the two passes of one run, and such a
        # resource must not be published from the manifest this pass
        # declined to write (gain#503).
        stats_manifest_outcome = _run_repo_manifest_command_internal(
            proto, written,
            dry_run=False, force=True, use_dvc=True)
        failed |= set(stats_manifest_outcome.failed)

    _warn_schema_stale(selection.schema_stale)
    return CommandResult(
        failed=frozenset(failed), repo_failed=repo_failed,
        wrote=outcome.wrote or bool(written))


def _run_stats_core(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any]) -> CommandResult:
    """Build the selected resources' statistics and refresh their manifests.

    Writes inside the selected resources' directories (and task logs
    under the repository's ``.task-log``); publishing the
    repository-global artifacts is its callers' decision.
    """
    if dry_run and force:
        logger.warning("please choose one of 'dry_run' and 'force' options")
        return CommandResult(repo_failed=True)

    outcome = _run_repo_manifest_command_internal(
        proto, resources, dry_run=dry_run, force=force, use_dvc=use_dvc)
    selection = _select_stats(
        repo, proto, resources, outcome,
        dry_run=dry_run, force=force, region_size=region_size)
    if dry_run:
        return _dry_run_stats_result(selection)
    return _build_selected_stats(selection, proto, outcome, task_graph_args)


def _run_repo_stats_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any]) -> CommandResult:
    result = _run_stats_core(
        repo, proto, resources,
        dry_run=dry_run, force=force, use_dvc=use_dvc,
        region_size=region_size, task_graph_args=task_graph_args)
    if dry_run:
        return result
    return _publish_repository_contents(proto, result)


def _run_resource_stats_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any]) -> CommandResult:
    return _note_stale_repository_index(
        proto, _run_stats_core(
            repo, proto, resources,
            dry_run=dry_run, force=force, use_dvc=use_dvc,
            region_size=region_size, task_graph_args=task_graph_args))
