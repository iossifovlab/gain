"""Build the GRR whose generated pages this suite drives.

Run from the repository root, before ``npx playwright test``::

    uv run python info_pages_e2e/generate_fixtures.py \\
        info_pages_e2e/fixtures/grr

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
from gain.genomic_resources.testing.info_page_fixtures import a_coverage_repo


def build_grr(repo_dir: pathlib.Path) -> None:
    """Realize the fixture GRR into ``repo_dir`` and generate its pages."""
    a_coverage_repo(repo_dir)

    # `-f` because the builders write a `stats_hash`, so a plain
    # `repo-stats` would decide there is nothing to do and `repo-info`
    # would then render a page with no Coverage section at all.
    cli_manage(["repo-stats", "-f", "-R", str(repo_dir), "-j", "1"])
    cli_manage(["repo-info", "-R", str(repo_dir), "-j", "1"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=pathlib.Path,
        help="directory to build the fixture GRR into; replaced if present")
    args = parser.parse_args(argv)

    # An absolute path: the builders hand the directory to pysam, which
    # resolves it against its own working directory rather than ours.
    repo_dir = args.output.resolve()
    # Rebuilt from scratch every time.  The builders refuse to overwrite a
    # bgzipped table, so a second run into a populated directory fails --
    # and a fixture that is only correct on a clean checkout is worse than
    # one that is rebuilt.
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    build_grr(repo_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
