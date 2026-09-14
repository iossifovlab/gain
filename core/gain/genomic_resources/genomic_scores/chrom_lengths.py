"""Chromosome lengths of a genomic score, and where each one came from.

A score's table can usually say how long a contig is, but the backends
answer with different confidence -- a bigWig header is exact, the tabix
probe brackets an upper bound, the in-memory backend only knows how far its
rows reach -- and the callers that split a contig into regions also need to
know WHY there is no length when there is none (gain#509).  So the record
here keeps three things apart: the number, its provenance, and the
:class:`~gain.genomic_resources.genomic_position_table.ContigExtent` reason
when the number is absent.

The ladder the epic (gain#1412) settles is genome label → bigWig header →
tabix estimate, applied per contig of the score.  The resolver here answers
every rung live: the genome rung from the ``ReferenceGenome`` a caller with
a GRR hands in, the rest through the table.  The caller is
:meth:`~gain.genomic_resources.implementations.genomic_scores_impl.base.GenomicScoreImplementation.get_chrom_lengths`,
which resolves the score's ``reference_genome`` label through its
repository; nothing is stored, because every consumer of a score's lengths
holds a repository to ask through (gain#1448).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)

if TYPE_CHECKING:
    from gain.genomic_resources.reference_genome import ReferenceGenome

    from .base import GenomicScore

# The provenance vocabulary is the table layer's, because three of its four
# members are facts each backend declares about its own format
# (``chrom_length_source``); re-exported from here, where the record that
# carries it lives, so a caller finds the enum beside the resolver (the
# ``BIGWIG_VALUE_COLUMN`` pattern).
__all__ = [
    "ChromLength",
    "ChromLengthSource",
    "derive_chrom_length",
    "derive_chrom_lengths",
]


@dataclass(frozen=True)
class ChromLength:
    """One contig's length, its source, or the reason there is none.

    Exactly one of the two shapes: ``length`` and ``source`` set with
    ``extent`` ``None``, or both ``None`` with ``extent`` saying why --
    ``EMPTY`` when the table proved the contig holds no records,
    ``UNDETERMINED`` when the probe could not answer for a contig that may
    well hold some.
    """

    length: int | None
    source: ChromLengthSource | None
    extent: ContigExtent | None


def derive_chrom_length(
    score: GenomicScore, chrom: str,
    ref_genome: ReferenceGenome | None = None,
) -> ChromLength:
    """Resolve one contig of ``score`` through the ladder.

    ``ref_genome`` is the top rung: a contig it lists is answered with its
    true length, tagged ``REFERENCE_GENOME``, and the table is never asked.
    A contig it does not list -- or no genome at all -- falls through to
    the table, whose source is whatever the backend declares its lengths
    to be.  Raises ``ValueError`` when the score is not open or does not
    carry ``chrom`` -- a bad question, as opposed to an absent answer -- in
    the TABLE's words, since it is the table that refuses; the genome rung
    sits behind the table's own screen so that a contig only the genome
    knows is refused the same way.
    """
    if (ref_genome is not None
            and score.table.has_chromosome(chrom)
            and chrom in ref_genome.get_all_chrom_lengths()):
        return ChromLength(
            length=ref_genome.get_chrom_length(chrom),
            source=ChromLengthSource.REFERENCE_GENOME, extent=None)
    length = score.table.find_chromosome_length(chrom)
    if isinstance(length, ContigExtent):
        return ChromLength(length=None, source=None, extent=length)
    return ChromLength(
        length=length, source=score.table.chrom_length_source, extent=None)


def derive_chrom_lengths(
    score: GenomicScore,
    ref_genome: ReferenceGenome | None = None,
) -> dict[str, ChromLength]:
    """Resolve every contig of ``score``, in the table's order.

    The universe is the score's contigs -- ``get_all_chromosomes()`` --
    never the whole genome; a whole-reference denominator is a property of
    the genome and belongs to coverage (gain#1041).  Keyed by contig so
    the consumer that splits regions (the statistics build) looks up by
    name; the dict keeps table order.

    Not memoised: on a tabix score every call re-runs the probe per
    contig.  Raises ``ValueError`` on a score that is not open.
    """
    return {
        chrom: derive_chrom_length(score, chrom, ref_genome)
        for chrom in score.get_all_chromosomes()
    }
