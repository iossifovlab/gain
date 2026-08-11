"""The `grr_manage list` command.

Split out of ``cli`` because listing is the one read-only command in that
module: everything else there manages a repository -- rebuilds manifests,
statistics and info pages -- while this only describes what is already
there. Keeping it apart also keeps the reporting policy it needs (a bad
resource is named and skipped, never fatal) from being read as the
management commands' policy, which is to fail the run.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Generator

from gain import logging
from gain.genomic_resources.cached_repository import GenomicResourceCachedRepo
from gain.genomic_resources.cli_errors import (
    RESOURCE_ERRORS,
    report_resource_failure,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    ReadOnlyRepositoryProtocol,
    SearchIndexUnavailableError,
    SearchTermError,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.utils.helpers import convert_size

logger = logging.getLogger("grr_manage")


def _search(
    repo: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
    search_term: str | None,
    resource_type: str | None,
    resource_query: str | None,
) -> Generator[
    tuple[ReadOnlyRepositoryProtocol | GenomicResourceRepo, GenomicResource],
    None, None,
]:
    """Run the search, turning a caller's mistake into a usage error.

    Yields each hit paired with the repository that holds it, which is what
    lets a group be listed in one pass: the pairs already name the child,
    so the listing does not have to take the group apart to label a row --
    and a child that cannot answer the filter is skipped inside the group
    rather than ending the listing (ADR 0012).

    The failures that do reach here are about the arguments or about every
    repository at once, and none deserves a traceback: a query the grammar
    cannot parse, a term FTS5 cannot read as a search expression, and a
    `-s`/`-t` filter that no repository has an index to apply. The last is
    the normal shape of a checked-out GRR -- `.CONTENTS.json.gz` and no
    `.CONTENTS.sqlite3.gz` -- and its own message names the way out.
    """
    try:
        # Iterated inside the guard, not merely started: the query is
        # parsed when the call is made, but the index is opened -- and the
        # search term handed to FTS5 -- on the first item, so only one of
        # the failures would be caught by guarding the call alone.
        if isinstance(repo, GenomicResourceRepo):
            yield from repo.search_resources_by_child(
                search_term, resource_type, resource_query)
        else:
            # A bare protocol holds its own resources and has no children.
            for res in repo.search_resources(
                    search_term, resource_type, resource_query):
                yield repo, res
    except (
        ResourceQueryParseError, SearchTermError,
        SearchIndexUnavailableError,
    ) as err:
        # `error`, not `exception`: the traceback is the thing being
        # replaced. The message already names the argument, or the
        # repositories and the repair, which is all a user can act on.
        logger.error("%s", err)  # noqa: TRY400
        sys.exit(1)


def _repo_id(
    repo: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
) -> str | None:
    """Name a repository, whichever side of the protocol/repo split it is."""
    if isinstance(repo, GenomicResourceRepo):
        return repo.repo_id
    return repo.get_id()


def run_list_command(
        proto: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
        args: argparse.Namespace) -> None:
    """List the resources of a repository."""
    # Read defensively rather than as attributes: this runs for both
    # `grr_manage list` and `grr_browse`, and is also driven directly by
    # tests that pass a bare namespace-less object.
    search_term = getattr(args, "search", None)
    resource_type = getattr(args, "type", None)
    resource_query = getattr(args, "query", None)
    long_format = getattr(args, "summary", False)
    # No group is taken apart here: the search yields the holder alongside
    # each resource, so a nested group is labelled by the leaf that serves
    # the row rather than by whichever ancestor this loop happened to hold.
    for repo, res in _search(
            proto, search_term, resource_type, resource_query):
        try:
            # A resource with no committed '.MANIFEST' has one built
            # here, on demand, so listing fails for any reason building
            # a manifest can. Reported and skipped, never raised: a
            # raise truncates the listing at whatever sorted first
            # (ADR 0010-resource-file-name-containment, the gain#464
            # shape, gain#503).
            files = list(res.get_manifest().get_files())
        except RESOURCE_ERRORS as err:
            report_resource_failure(err, "could not list", res.get_id())
            continue
        res_size = sum(fs for _, fs in files)
        files_msg = f"{len(files):2d}"
        if isinstance(repo, GenomicResourceCachedRepo):
            cached_files = repo.get_resource_cached_files(res.get_id())
            files_msg = f"{len(cached_files):2d}/{files_msg}"

        res_size_msg = res_size \
            if hasattr(args, "bytes") and args.bytes is True \
            else convert_size(res_size)
        repo_id = _repo_id(repo)
        print(
            f"{res.get_type():20} {res.get_version_str():7s} "
            f"{files_msg} {res_size_msg:12} "
            f"{repo_id} "
            f"{res.get_id()}")
        if long_format:
            summary = res.get_summary()
            if summary:
                print(f"  {summary.strip()}")
