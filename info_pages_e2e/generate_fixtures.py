"""Build the GRRs whose generated pages this suite drives.

Two of them: a Coverage GRR whose statistics table the sorter tests
sort, and a browse GRR laid out to be navigated and searched.  Both are
written under the directory named on the command line.

Run from the repository root, before ``npx playwright test``::

    uv run python info_pages_e2e/generate_fixtures.py \\
        info_pages_e2e/fixtures

The pages are *generated*, never committed.  A committed page is a
snapshot of a template that has since moved on, and this suite exists to
catch a sorter that stopped working -- not to notice that a copy of last
month's markup still sorts.  ``info_pages_e2e/Dockerfile`` runs this in a
builder stage so the CI image carries fresh pages and no Python at all.

The fixture itself is :mod:`gain.genomic_resources.testing`'s, not this
project's: the traps that make the browser assertions sharp are the same
ones the markup-contract tests in ``core`` are built on, and a second
copy here would give two fixtures that only mean anything while they
happen to agree.  That module ships in the wheel, so the builder stage
can import it having installed nothing but ``gain-core``.

It is not realized from the ``test_fixtures/mini-GRR`` submodule.
mini-GRR is GAIn's onboarding example, and the shape these assertions
need is not one it should carry: a contig whose genome cannot measure it
would make it a worse teaching repository, which is the same reasoning
that kept the four supplement resource types out of it in gain#991.

The pages are produced through ``grr_manage`` -- the command that
publishes a real GRR -- rather than by calling a template directly, so
what the browser opens is a page assembled the way a published one is.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.testing.info_page_fixtures import (
    a_browse_repo,
    a_coverage_repo,
)

#: The generated GRRs, under the directory this script is pointed at.
COVERAGE_GRR_DIRNAME = "grr"
BROWSE_GRR_DIRNAME = "browse"


def build_coverage_grr(repo_dir: pathlib.Path) -> None:
    """Realize the Coverage GRR into ``repo_dir`` and generate its pages."""
    a_coverage_repo(repo_dir)

    # `-f` because the builders write a `stats_hash`, so a plain
    # `repo-stats` would decide there is nothing to do and `repo-info`
    # would then render a page with no Coverage section at all.
    cli_manage(["repo-stats", "-f", "-R", str(repo_dir), "-j", "1"])
    cli_manage(["repo-info", "-R", str(repo_dir), "-j", "1"])


def build_browse_grr(repo_dir: pathlib.Path) -> None:
    """Realize the browse GRR into ``repo_dir`` and generate its pages.

    ``repo-index`` alone, where the Coverage GRR needs the whole
    statistics pass.  This fixture exists to be navigated and searched,
    and the repository index page is assembled from ``.CONTENTS`` and the
    search index -- both of which ``repo-index`` publishes from the
    manifests the builders already wrote.  Computing histograms for five
    resources whose statistics no assertion reads would only make every
    run of this suite slower.
    """
    a_browse_repo(repo_dir)

    cli_manage(["repo-index", "-R", str(repo_dir)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=pathlib.Path,
        help="directory to build the fixture GRRs into; replaced if present")
    args = parser.parse_args(argv)

    # An absolute path: the builders hand the directory to pysam, which
    # resolves it against its own working directory rather than ours.
    fixtures_dir = args.output.resolve()

    # Each GRR is rebuilt from scratch.  The builders refuse to overwrite
    # a bgzipped table, so a second run into a populated directory fails
    # -- and a fixture that is only correct on a clean checkout is worse
    # than one that is rebuilt.
    #
    # Only the two directories this script creates, never the directory
    # it was handed.  What gets deleted here is deleted without asking,
    # and the argument names the fixtures *root* -- one tab-completion
    # away from `info_pages_e2e` itself, whose contents are this suite.
    # Deleting only what we own makes that misfire harmless instead of
    # needing a list of things to refuse.
    for dirname, build in (
        (COVERAGE_GRR_DIRNAME, build_coverage_grr),
        (BROWSE_GRR_DIRNAME, build_browse_grr),
    ):
        repo_dir = fixtures_dir / dirname
        shutil.rmtree(repo_dir, ignore_errors=True)
        build(repo_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
