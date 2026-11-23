"""The info page commands of ``grr_manage``.

The ``repo-info``, ``repo-repair``, ``resource-info`` and
``resource-repair`` commands, which build the statistics first and then
write the info pages of each resource. A repair skips the info pages of a
resource whose page fingerprint is unchanged.

The page fingerprint covers the page templates and the static files of
gain, the provider templates, the manifest, the statistics hash, the info
hash and the page dependencies of the resource. A new gain version alone
does not change it, so a repair after an upgrade renders no page again.
After a change to the Python code that builds the page data, run
``repo-repair --force`` or ``repo-info``.
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
from gain.genomic_resources.group_repository import (
    GenomicResourceGroupRepo,
)
from gain.genomic_resources.page_fingerprint import (
    PAGE_FINGERPRINT_READ_SIZE,
    insert_page_fingerprint,
    read_page_fingerprint,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceProtocolRepo,
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


def _invalidate_children_at(repo: GenomicResourceRepo, url: str) -> None:
    """Drop the memo of each repository in ``repo`` that reads ``url``."""
    if isinstance(repo, GenomicResourceGroupRepo):
        for child in repo.children:
            _invalidate_children_at(child, url)
    elif isinstance(repo, GenomicResourceProtocolRepo) \
            and repo.proto.get_url() == url:
        repo.invalidate()


def _regenerate_resource_pages(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource],
        result: CommandResult, *,
        render_all_pages: bool) -> CommandResult:
    """Regenerate the info pages of the selected resources.

    With ``render_all_pages`` every resource renders. Otherwise a resource
    whose page fingerprint is current is skipped.
    """
    failed = set(result.failed)
    wrote = result.wrote
    rendered = skipped = 0
    # The statistics step rewrote manifests through ``proto``. ``repo``
    # still holds the manifests that it loaded before, and the page
    # fingerprint reads the manifest of each page dependency from ``repo``.
    # Only the children at the url of ``proto`` hold rewritten manifests,
    # so a remote child keeps its memo.
    _invalidate_children_at(repo, proto.get_url())
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
            outcome = _do_resource_info_command(
                repo, proto, res, render_all_pages=render_all_pages)
        except Exception as err:  # ruff: ignore[blind-except]
            report_resource_failure(
                err, "skipping info page for", res.resource_id)
            failed.add(res.resource_id)
            continue
        if outcome is None:
            skipped += 1
            continue
        rendered += 1
        wrote = outcome or wrote
    logger.info("info pages: rendered %d, skipped %d", rendered, skipped)
    return dataclasses.replace(
        result, failed=frozenset(failed), wrote=wrote)


def _run_repo_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any],
        render_all_pages: bool) -> CommandResult:
    """Run ``repo-info`` or ``repo-repair``.

    ``render_all_pages`` is ``True`` for ``repo-info``: the command renders
    every info page whatever its page fingerprint.
    """
    result = _run_repo_stats_command(
        repo, proto, resources,
        dry_run=dry_run, force=force, use_dvc=use_dvc,
        region_size=region_size, task_graph_args=task_graph_args)

    if dry_run:
        return result

    assert isinstance(proto, FsspecReadWriteProtocol)
    proto.build_index_info(failed=result.failed)
    return _regenerate_resource_pages(
        repo, proto, resources, result,
        render_all_pages=render_all_pages or force)


def _run_resource_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        resources: Sequence[GenomicResource], *,
        dry_run: bool, force: bool, use_dvc: bool, region_size: int,
        task_graph_args: Mapping[str, Any],
        render_all_pages: bool) -> CommandResult:
    """Run ``resource-info`` or ``resource-repair``.

    ``render_all_pages`` is ``True`` for ``resource-info``: the command
    renders every info page whatever its page fingerprint.
    """
    result = _run_stats_core(
        repo, proto, resources,
        dry_run=dry_run, force=force, use_dvc=use_dvc,
        region_size=region_size, task_graph_args=task_graph_args)

    if dry_run:
        return result

    return _note_stale_repository_index(
        proto, _regenerate_resource_pages(
            repo, proto, resources, result,
            render_all_pages=render_all_pages or force))


def _write_resource_file_if_changed(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource,
        filename: str,
        content: str) -> bool:
    """Write ``content`` to a resource file only if it differs.

    The info pages are deterministic, so rendering them on an unchanged
    repo produces identical bytes. Skipping the write in
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


def _stored_page_fingerprint(
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource) -> str | None:
    """Return the page fingerprint stored in the info pages of ``res``.

    ``None`` means that an info page is missing or that ``index.html``
    carries no page fingerprint tag.
    """
    if not (proto.file_exists(res, GR_INDEX_FILE_NAME)
            and proto.file_exists(res, GR_STATISTICS_INDEX_FILE_NAME)):
        return None
    with proto.open_raw_file(res, GR_INDEX_FILE_NAME, "rt") as infile:
        return read_page_fingerprint(infile.read(PAGE_FINGERPRINT_READ_SIZE))


def _do_resource_info_command(
        repo: GenomicResourceRepo,
        proto: ReadWriteRepositoryProtocol,
        res: GenomicResource, *,
        render_all_pages: bool) -> bool | None:
    """Regenerate the info pages of one resource.

    Return ``None`` when the page fingerprint of the resource is current
    and the pages were skipped. Otherwise return whether a page changed.
    """
    implementation = build_resource_implementation(res)
    fingerprint = implementation.page_fingerprint(repo)
    if (not render_all_pages and fingerprint is not None
            and _stored_page_fingerprint(proto, res) == fingerprint):
        return None

    # Both pages are rendered BEFORE either is written: writing index.html
    # first and only then rendering the statistics page left a rewritten
    # page behind whenever the second render raised, while the run
    # reported the page had been protected (gain#364).
    info = implementation.get_info(repo=repo)
    statistics_info = implementation.get_statistics_info(repo=repo)
    # The tag goes in after the render: the kinds, gpf included, call
    # InfoImplementationMixin.get_info without the render arguments.
    if fingerprint is not None:
        info = insert_page_fingerprint(info, fingerprint)
        statistics_info = insert_page_fingerprint(
            statistics_info, fingerprint)

    # index.html goes last, because it holds the page fingerprint that the
    # next repair reads. A run that stops between the two writes leaves
    # the old page fingerprint, so the next repair renders the pages again.
    wrote = _write_resource_file_if_changed(
        proto, res, GR_STATISTICS_INDEX_FILE_NAME, statistics_info)
    return _write_resource_file_if_changed(
        proto, res, GR_INDEX_FILE_NAME, info) or wrote
