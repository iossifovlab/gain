"""The fixtures the info pages' browser tests are built on.

Two of them.  ``a_coverage_repo`` is a single resource whose statistics
table both sortable-table suites sort; ``a_browse_repo`` is a repository
shaped to be *navigated* -- folders to descend through and terms to
search for -- which is what the index page's own tests need.

Two rather than one because the coverage fixture's traps are tuned to a
sorter and nothing else should perturb them: adding folders to it would
change the table the sort assertions read, and adding a sort trap to the
browse fixture would make its search assertions depend on row order.

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


#: The browse fixture's top-level folders, in the order the tree sorts
#: them.  Three of them, so a search that matches inside one leaves two
#: that must disappear -- a pruned tree with nothing to prune proves
#: nothing.
BROWSE_TOP_LEVEL_FOLDERS = ("genomes", "hg19", "hg38")

#: A term that reaches its resource through the resource's ``summary``
#: and through nothing else.
#:
#: An unqualified FTS5 ``MATCH`` searches every indexed column, so "found
#: via the summary" is only distinguishable from "found via the id" while
#: this word appears in no id, type, description, score id or label
#: anywhere in the fixture.  Nothing in the data enforces that;
#: ``test_info_page_browse_fixture.py`` does.
BROWSE_SUMMARY_ONLY_TERM = "marmoset"
BROWSE_SUMMARY_ONLY_RESOURCE_ID = "hg19/legacy/allele_frequencies"

#: The mirror of it: a term carried only by a resource's id.  Together
#: the two pin the index's two routes independently -- stop indexing
#: summaries and the first goes red while this one stays green.
BROWSE_ID_ONLY_TERM = "phylop"
BROWSE_ID_ONLY_RESOURCE_ID = "hg38/scores/conservation/phylop"

#: The genome, which is the fixture's *second* resource type: a tree with
#: one type in it cannot show that the type filter narrows anything.
BROWSE_GENOME_RESOURCE_ID = "genomes/g984"

#: Every resource the browse fixture carries, deepest path first.  The
#: deepest is four segments, so the tree has a folder inside a folder
#: inside a folder to descend through and walk back up.
BROWSE_RESOURCE_IDS = (
    BROWSE_ID_ONLY_RESOURCE_ID,
    "hg38/scores/conservation/phastcons",
    "hg38/scores/coverage",
    BROWSE_SUMMARY_ONLY_RESOURCE_ID,
    BROWSE_GENOME_RESOURCE_ID,
)

#: Summaries, by resource id.  Deliberately plain prose: each one has to
#: stay clear of both terms above except for the single resource that
#: carries one, and prose that names its own resource is exactly how that
#: stops being true.
_BROWSE_SUMMARIES = {
    BROWSE_ID_ONLY_RESOURCE_ID:
        "Basewise conservation across a vertebrate alignment.",
    "hg38/scores/conservation/phastcons":
        "Posterior probability that a base lies in a conserved element.",
    "hg38/scores/coverage":
        "Sequencing depth at each position.",
    BROWSE_SUMMARY_ONLY_RESOURCE_ID:
        "Allele frequencies from the marmoset cohort.",
    BROWSE_GENOME_RESOURCE_ID:
        "Small reference genome the browse fixture is laid out over.",
}

_BROWSE_SCORE_DATA = """
chrom  pos_begin  pos_end  score
chr1   1          10       0.1
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


def a_browse_repo(where: pathlib.Path) -> GenomicResourceRepo:
    """A repository shaped to be navigated rather than sorted.

    Three top-level folders, a four-segment path to descend, two resource
    types, and the two search terms above -- one reaching its resource
    only through a summary, the other only through an id.

    No labels and no statistics: every column an unqualified ``MATCH``
    can search is a column one of the two terms could leak into, so the
    fixture carries the fewest of them it can and still be a repository.
    """
    genome = a_reference_genome().with_chromosome("chr1", "A" * 100)
    grr = a_grr()
    for resource_id in BROWSE_RESOURCE_IDS:
        summary = _BROWSE_SUMMARIES[resource_id]
        if resource_id == BROWSE_GENOME_RESOURCE_ID:
            grr = grr.with_resource(
                resource_id, genome.with_meta(summary=summary))
            continue
        grr = grr.with_resource(
            resource_id,
            a_position_score()
            .with_score("score", "float")
            .with_data(_BROWSE_SCORE_DATA)
            .with_meta(summary=summary),
        )
    return grr.build_repo(where)
