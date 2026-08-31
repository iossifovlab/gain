"""The Coverage fixture the info pages' sortable-table tests are built on.

Two suites drive the same table from opposite sides.
``core/tests/small/genomic_resources/test_info_page_sortable_tables.py``
pins what the templates *emit* -- which ``<th>`` carries ``data-sort``,
which ``<td>`` carries a ``data-sort-value``.  The ``info_pages_e2e``
Playwright project generates the page and pins what a browser *does*
with it when a header is clicked.

Neither suite is worth much without the traps below, and those traps are
what makes this module exist rather than a copy on each side: the two
suites live in different projects, and each ``<project>/Dockerfile``
copies only its own directory, so the Playwright project cannot import
anything from ``core``'s test tree.  It can import this, because
``gain.genomic_resources.testing`` ships in the wheel its image installs.

Duplicating the shape instead would give two independently tunable
fixtures whose assertions only mean anything while they happen to agree
-- retune one and the other's assertions go vacuous with nothing turning
red.
"""
from __future__ import annotations

import pathlib

from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

#: The resource whose Coverage table both suites drive, and the genome it
#: is labelled with.  The label is the rung that lets the coverage
#: denominator resolve, which is what gives two rows a fraction and one
#: none.
COVERAGE_RESOURCE_ID = "scores/coverage"
GENOME_RESOURCE_ID = "genomes/g984"

#: ``chr1`` and ``chr2`` resolve a length; ``chr10`` deliberately does
#: not, so the Coverage table carries one row whose fraction is None --
#: its ``Covered %`` cell gets no ``data-sort-value``, and the sorter has
#: to treat that as "no value" rather than as zero.
GENOME_LENGTHS = {"chr1": 100, "chr2": 50}

#: The covered-position counts, in the order the page renders them.  9,
#: 10 and 2 are chosen so that comparing them as text ("10" < "2" < "9")
#: differs from comparing them as numbers -- a column that lost its
#: ``data-sort="number"`` would still sort, just wrongly, and only a
#: fixture with this shape notices.
COVERED_POSITIONS = [9, 10, 2]

#: The contigs the fixture carries, in natural order.
CONTIGS = ["chr1", "chr2", "chr10"]

_COVERAGE_DATA = """
chrom  pos_begin  pos_end  score
chr1   1          9        0.1
chr2   1          10       0.2
chr10  1          2        0.3
"""


def a_coverage_repo(where: pathlib.Path) -> GenomicResourceRepo:
    """A three-contig score whose genome knows only two of the contigs."""
    genome = a_reference_genome()
    for chrom, length in GENOME_LENGTHS.items():
        genome = genome.with_chromosome(chrom, "A" * length)
    return (
        a_grr()
        .with_resource(
            COVERAGE_RESOURCE_ID,
            a_position_score()
            .with_score("score", "float")
            .with_data(_COVERAGE_DATA)
            .with_tabix()
            .with_labels(reference_genome=GENOME_RESOURCE_ID))
        .with_resource(GENOME_RESOURCE_ID, genome)
        .build_repo(where)
    )
