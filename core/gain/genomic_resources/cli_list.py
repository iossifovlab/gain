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
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    ReadOnlyRepositoryProtocol,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.utils.helpers import convert_size

logger = logging.getLogger("grr_manage")


def _search(
    repo: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
    search_term: str | None,
    resource_type: str | None,
    resource_query: str | None,
) -> Generator[GenomicResource, None, None]:
    """Run the search, turning a caller's mistake into a usage error.

    The two failures reachable from the command line are both about the
    arguments rather than about the repository, and neither deserves a
    traceback: a query the grammar cannot parse, and a `-s`/`-t` filter on
    a repository that has no search index to apply it to. The latter is the
    normal shape of a checked-out GRR -- `.CONTENTS.json` and no
    `.CONTENTS.sqlite3.gz` -- so it is worth naming the way out.
    """
    try:
        # Iterated inside the guard, not merely started: the query is
        # parsed when the call is made, but the index is opened on the
        # first item, so only one of the two failures would be caught by
        # guarding the call alone.
        yield from repo.search_resources(
            search_term, resource_type, resource_query)
    except ResourceQueryParseError as err:
        # `error`, not `exception`: the traceback is the thing being
        # replaced. The message already names the query and what the
        # grammar expected, which is all a user can act on.
        logger.error("%s", err)  # noqa: TRY400
        sys.exit(1)
    except ValueError as err:
        if "SQLite metadata DB" not in str(err):
            raise
        logger.error(  # noqa: TRY400
            "cannot filter by --search/--type: repository <%s> has no "
            "search index. Build one with `grr_manage repo-repair`, or "
            "select by id and labels with --query, which needs none.",
            _repo_id(repo))
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
    repos: list = [proto]
    if isinstance(proto, GenomicResourceGroupRepo):
        repos = proto.children
    for repo in repos:
        for res in _search(repo, search_term, resource_type, resource_query):
            try:
                # A resource with no committed '.MANIFEST' has one built
                # here, on demand, so listing fails for any reason building
                # a manifest can. Reported and skipped, never raised: a
                # raise truncates the listing at whatever sorted first
                # (ADR 0003, the gain#464 shape, gain#503).
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
