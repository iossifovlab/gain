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

from gain.genomic_resources.cached_repository import GenomicResourceCachedRepo
from gain.genomic_resources.cli_errors import (
    RESOURCE_ERRORS,
    report_resource_failure,
)
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GenomicResourceRepo,
    ReadOnlyRepositoryProtocol,
)
from gain.utils.helpers import convert_size


def run_list_command(
        proto: ReadOnlyRepositoryProtocol | GenomicResourceRepo,
        args: argparse.Namespace) -> None:
    """List the resources of a repository."""
    search_term = getattr(args, "search", None)
    resource_type = getattr(args, "type", None)
    long_format = getattr(args, "summary", False)
    repos: list = [proto]
    if isinstance(proto, GenomicResourceGroupRepo):
        repos = proto.children
    for repo in repos:
        for res in repo.search_resources(search_term, resource_type):
            try:
                # A resource with no committed '.MANIFEST' has one built
                # here, on demand, so listing fails for any reason building
                # a manifest can. Reported and skipped, never raised: a
                # raise truncates the listing at whatever sorted first
                # (ADR 0003-resource-file-name-containment, the gain#464
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
