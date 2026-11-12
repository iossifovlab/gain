"""The info page commands of ``grr_manage``.

The ``repo-info`` and ``resource-info`` commands, which build the
statistics first and then write the ``index.html`` of each resource.
"""
import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from gain import logging
from gain.genomic_resources.cli_common import (
    CommandResult,
    _note_stale_repository_index,
)
from gain.genomic_resources.cli_errors import (
    report_resource_failure,
)
from gain.genomic_resources.cli_stats import (
    _run_repo_stats_command,
    _run_stats_core,
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
    build_resource_implementation,
)
from gain.genomic_resources.repository_ids import (
    GR_INDEX_FILE_NAME,
    GR_STATISTICS_INDEX_FILE_NAME,
)

logger = logging.getLogger("grr_manage")


def _regenerate_resource_pages(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        result: CommandResult) -> CommandResult:
    """Regenerate the selected resources' own info pages."""
    failed = set(result.failed)
    wrote = result.wrote
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
            wrote = _do_resource_info_command(repo, proto, res) or wrote
        except Exception as err:  # ruff: ignore[blind-except]
            report_resource_failure(
                err, "skipping info page for", res.resource_id)
            failed.add(res.resource_id)
    return dataclasses.replace(
        result, failed=frozenset(failed), wrote=wrote)


def _run_repo_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any]) -> CommandResult:
    result = _run_repo_stats_command(
        repo, proto, resources,
        dry_run=dry_run, force=force, use_dvc=use_dvc,
        region_size=region_size, task_graph_args=task_graph_args)

    if dry_run:
        return result

    assert isinstance(proto, FsspecReadWriteProtocol)
    proto.build_index_info(failed=result.failed)
    return _regenerate_resource_pages(repo, proto, resources, result)


def _run_resource_info_command(
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

    return _note_stale_repository_index(
        proto, _regenerate_resource_pages(repo, proto, resources, result))


def _write_resource_file_if_changed(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource,
        filename: str,
        content: str) -> bool:
    """Write ``content`` to a resource file only if it differs.

    The generated info pages are deterministic, so regenerating them on
    an unchanged repo produces identical bytes. Skipping the write in
    that case keeps the file's mtime stable, so re-running repo-repair
    is idempotent (and mtime-based consumers don't see spurious churn).
    Returns whether the file was written.
    """
    if proto.file_exists(res, filename):
        with proto.open_raw_file(res, filename, "rt") as infile:
            if infile.read() == content:
                return False
    with proto.open_raw_file(res, filename, mode="wt") as outfile:
        outfile.write(content)
    return True


def _do_resource_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> bool:
    """Regenerate one resource's info pages; return whether any changed."""
    implementation = build_resource_implementation(res)

    # Both pages are rendered BEFORE either is written: writing index.html
    # first and only then rendering the statistics page left a rewritten
    # page behind whenever the second render raised, while the run
    # reported the page had been protected (gain#364).
    info = implementation.get_info(repo=repo)
    statistics_info = implementation.get_statistics_info(repo=repo)

    wrote = _write_resource_file_if_changed(
        proto, res, GR_INDEX_FILE_NAME, info)
    return _write_resource_file_if_changed(
        proto, res, GR_STATISTICS_INDEX_FILE_NAME, statistics_info) or wrote
