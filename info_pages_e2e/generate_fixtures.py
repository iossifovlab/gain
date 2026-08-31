"""Build the GRR whose generated pages this suite drives.

Run from the repository root, before ``npx playwright test``::

    uv run python info_pages_e2e/generate_fixtures.py \\
        info_pages_e2e/fixtures/grr

The pages are *generated*, never committed.  A committed page is a
snapshot of a template that has since moved on, and this suite exists to
catch a sorter that stopped working -- not to notice that a copy of last
month's markup still sorts.  ``info_pages_e2e/Dockerfile`` runs this in a
builder stage so the CI image carries fresh pages and no Python at all.

The GRR is realized from ``gain.genomic_resources.testing.builders``
rather than from the ``test_fixtures/mini-GRR`` submodule.  mini-GRR is
GAIn's onboarding example, and the shape these assertions need is not one
it should carry: the traps below (counts whose text and numeric order
disagree, a contig its genome cannot measure) would make it a worse
teaching repository, which is the same reasoning that kept the four
supplement resource types out of it in gain#991.

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
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

#: The reference genome the Coverage score is labelled with.  It resolves
#: ``chr1`` and ``chr2`` and deliberately not ``chr10``: a chromosome with
#: no length has no coverage *fraction*, so its ``Covered %`` cell carries
#: no ``data-sort-value`` and the sorter has to treat it as "no value"
#: rather than as zero.
GENOME_LENGTHS = {"chr1": 100, "chr2": 50}

#: Covered-position counts, in the order the page renders them.  9, 10 and
#: 2 are chosen so that comparing them as text ("10" < "2" < "9") gives a
#: different order than comparing them as numbers: a column that lost its
#: ``data-sort="number"`` would still sort, just wrongly, and only a
#: fixture with this shape notices.  The same shape, and the same reason,
#: as ``COVERED_POSITIONS`` in
#: ``core/tests/small/genomic_resources/test_info_page_sortable_tables.py``.
COVERAGE_DATA = """
chrom  pos_begin  pos_end  score
chr1   1          9        0.1
chr2   1          10       0.2
chr10  1          2        0.3
"""

#: The resource the suite opens, and the genome it is labelled with.  The
#: label is what lets the Coverage denominator resolve, which is the rung
#: that gives two rows a fraction and one none.
COVERAGE_RESOURCE_ID = "scores/coverage"
GENOME_RESOURCE_ID = "genomes/g987"


def build_grr(repo_dir: pathlib.Path) -> None:
    """Realize the fixture GRR into ``repo_dir`` and generate its pages."""
    genome = a_reference_genome()
    for chrom, length in GENOME_LENGTHS.items():
        genome = genome.with_chromosome(chrom, "A" * length)

    (
        a_grr()
        .with_resource(
            COVERAGE_RESOURCE_ID,
            a_position_score()
            .with_score("score", "float")
            .with_data(COVERAGE_DATA)
            .with_tabix()
            .with_labels(reference_genome=GENOME_RESOURCE_ID))
        .with_resource(GENOME_RESOURCE_ID, genome)
        .build_repo(repo_dir)
    )

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
