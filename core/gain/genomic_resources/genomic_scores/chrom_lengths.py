"""Chromosome lengths of a genomic score, and where each one came from.

A score's table can usually say how long a contig is, but the backends
answer with different confidence -- a bigWig header is exact, the tabix
probe brackets an upper bound, the in-memory backend only knows how far its
rows reach -- and the callers that split a contig into regions also need to
know WHY there is no length when there is none (gain#509).  So the record
here keeps each number under its provenance, picks the best-trusted of
them, and carries the
:class:`~gain.genomic_resources.genomic_position_table.ContigExtent` reason
the table gives when it has no number.

The ladder the epic (gain#1412) settles is genome label → bigWig header →
tabix estimate, applied per contig of the score.  The resolver here asks
every rung live, and every rung answers (gain#1574): the genome rung from
the ``ReferenceGenome`` the caller hands in, the table's own rung through
the table -- a record holds each answer under its source, and ``best``
picks by the source's rank.  It holds no repository, so resolving the
score's ``reference_genome`` label into that genome is the caller's job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)

if TYPE_CHECKING:
    from gain.genomic_resources.reference_genome import ReferenceGenome

    from .base import GenomicScore

# Both enums are the table layer's -- the provenance vocabulary because
# three of its four members are facts each backend declares about its own
# format (``chrom_length_source``), the extent because it is the table
# that proves a contig empty or fails to measure it; re-exported from
# here, where the record that carries them lives, so a caller finds them
# beside the resolver (the ``BIGWIG_VALUE_COLUMN`` pattern) and the
# statistics implementation need not import the table package at all
# (gain#410).
__all__ = [
    "ChromLength",
    "ChromLengthAnswer",
    "ChromLengthSource",
    "ContigExtent",
    "derive_chrom_length",
    "derive_chrom_lengths",
]


@dataclass(frozen=True)
class ChromLengthAnswer:
    """One source's length for a contig: the number and where it came from."""

    length: int
    source: ChromLengthSource


@dataclass(frozen=True)
class ChromLength:
    """One contig's length from every source that had one.

    ``answers`` is each source's length; ``extent`` is the TABLE's reason
    when it had none -- ``EMPTY`` when it proved the contig holds no
    records, ``UNDETERMINED`` when the probe could not answer for a
    contig that may well hold some -- and ``None`` when it answered.  A
    record with no answer always carries the reason; one is refused
    without it.
    """

    answers: Mapping[ChromLengthSource, int]
    extent: ContigExtent | None

    def __post_init__(self) -> None:
        if not self.answers and self.extent is None:
            raise ValueError(
                "a chromosome-length record with no answer and no reason "
                "for its absence")

    @property
    def best(self) -> ChromLengthAnswer | None:
        """The highest-ranked answer with its source; ``None`` when none."""
        if not self.answers:
            return None
        source = max(self.answers, key=lambda s: s.rank)
        return ChromLengthAnswer(self.answers[source], source)


def derive_chrom_length(
    score: GenomicScore, chrom: str,
    ref_genome: ReferenceGenome | None = None,
) -> ChromLength:
    """Resolve one contig of ``score`` through the ladder, every rung.

    ``ref_genome`` is the top rung: a contig it lists is answered with its
    true length under ``REFERENCE_GENOME``.  The table is asked as well,
    genome or not, and answers under whatever source the backend
    declares its lengths to be, or with the reason it cannot.  Raises
    ``ValueError`` when the score is not open or does not carry ``chrom``
    -- a bad question, as opposed to an absent answer -- in the TABLE's
    words, since it is the table that refuses; the genome rung sits
    behind the table's own screen so that a contig only the genome knows
    is refused the same way.
    """
    answers: dict[ChromLengthSource, int] = {}
    extent = None
    length = score.table.find_chromosome_length(chrom)
    if isinstance(length, ContigExtent):
        extent = length
    else:
        answers[score.chrom_length_source] = length
    if (ref_genome is not None
            and chrom in ref_genome.get_all_chrom_lengths()):
        answers[ChromLengthSource.REFERENCE_GENOME] = (
            ref_genome.get_chrom_length(chrom))
    return ChromLength(answers=answers, extent=extent)


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
