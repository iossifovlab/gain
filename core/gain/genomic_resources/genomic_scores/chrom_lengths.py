"""Chromosome lengths of a genomic score, and where each one came from.

The resolver behind :meth:`~.base.GenomicScore.get_chrom_length` and its two
siblings.  A score's table can usually say how long a contig is, but the
backends answer with different confidence -- a bigWig header is exact, the
tabix probe brackets an upper bound, the in-memory backend only knows how far
its rows reach -- and the callers that split a contig into regions also need
to know WHY there is no length when there is none (gain#509).  So the record
here keeps three things apart: the number, its provenance, and the
:class:`~gain.genomic_resources.genomic_position_table.ContigExtent` reason
when the number is absent.  The score's methods expose the ``int`` view only.

The ladder the epic (gain#1412) settles is genome label → bigWig header →
tabix estimate, applied per contig of the score.  This module answers the
table rungs live; the genome rung (gain#1418) and the stored
``statistics/chrom_lengths.json`` (gain#1419) are later slices.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gain.genomic_resources.genomic_position_table import (
    BigWigTable,
    ContigExtent,
    InmemoryGenomicPositionTable,
    TabixGenomicPositionTable,
)

if TYPE_CHECKING:
    from gain.genomic_resources.genomic_position_table.table import (
        GenomicPositionTable,
    )
    from gain.genomic_resources.reference_genome import ReferenceGenome

    from .base import GenomicScore


class ChromLengthSource(enum.Enum):
    """Where a contig's length was read from.

    Only :attr:`REFERENCE_GENOME` and :attr:`BIGWIG` are exact.  The other
    two are what a table can say about itself without a genome: the tabix
    probe answers an upper bound, and the in-memory backend answers how far
    its rows reach, which is an extent of the data rather than a length of
    the contig.  Callers ask :attr:`is_exact` rather than enumerating
    members, so a new source needs no edits at the call sites.
    """

    REFERENCE_GENOME = "reference_genome"
    BIGWIG = "bigwig"
    TABIX_ESTIMATE = "tabix_estimate"
    TABLE_EXTENT = "table_extent"

    @property
    def is_exact(self) -> bool:
        """Whether a length from this source is the contig's true length.

        For the table-derived members this must agree with the backend's
        ``chrom_lengths_are_exact`` flag, which is what coverage's
        ``resolve_chrom_lengths`` reads today: the two classify alike so
        that moving that caller onto this API (gain#1414) changes no
        denominator.  Nothing derives one from the other -- the agreement
        is pinned per backend by
        ``test_a_table_sources_exactness_is_its_backends``, and a backend
        :func:`_table_source` does not know is refused rather than labelled.
        """
        return self in (
            ChromLengthSource.REFERENCE_GENOME,
            ChromLengthSource.BIGWIG,
        )


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
    ref_genome: ReferenceGenome | None = None,  # ruff: ignore[unused-function-argument]
) -> ChromLength:
    """Resolve one contig of ``score`` through the ladder.

    ``ref_genome`` is accepted for the genome rung, which is not yet
    implemented (gain#1418); today every contig is answered by the table.
    Raises ``ValueError`` when the score is not open or does not carry
    ``chrom`` -- a bad question, as opposed to an absent answer -- in the
    TABLE's words, since it is the table that refuses; the score's methods
    screen first and word both refusals as every other score read does.
    """
    length = score.table.find_chromosome_length(chrom)
    if isinstance(length, ContigExtent):
        return ChromLength(length=None, source=None, extent=length)
    return ChromLength(
        length=length, source=_table_source(score.table), extent=None)


def derive_chrom_lengths(
    score: GenomicScore,
    ref_genome: ReferenceGenome | None = None,
) -> dict[str, ChromLength]:
    """Resolve every contig of ``score``, in the table's order.

    The universe is the score's contigs -- ``get_all_chromosomes()`` --
    never the whole genome; a whole-reference denominator is a property of
    the genome and belongs to coverage (gain#1041).  Keyed by contig so
    the consumers that split regions (gain#1418) and persist the answer
    (gain#1419) look up by name; the dict keeps table order.

    Not memoised: on a tabix score every call re-runs the probe per contig,
    which is the cost gain#1419 takes out of the read path by storing this
    at repair.  Raises ``ValueError`` on a score that is not open.
    """
    return {
        chrom: derive_chrom_length(score, chrom, ref_genome)
        for chrom in score.get_all_chromosomes()
    }


def _table_source(table: GenomicPositionTable) -> ChromLengthSource:
    """Name what ``find_chromosome_length`` on ``table`` measures.

    Decided here, in the score layer, from the backend's type -- the way
    :mod:`.base` already routes value extraction -- because the label is a
    fact about how each format answers, and only the score layer holds the
    vocabulary for it.  The length itself is still asked of the table.

    Every arm is explicit and the fall-through raises: a backend this
    ladder has not met would otherwise be labelled by whichever arm came
    last, and its ``is_exact`` could then silently disagree with the flag
    the backend declares.  The cost is the one gain#509 objected to -- a
    fifth backend must edit this -- but here the edit is one line naming
    what the backend's length IS, which no fallback can guess.
    """
    if isinstance(table, BigWigTable):
        return ChromLengthSource.BIGWIG
    if isinstance(table, InmemoryGenomicPositionTable):
        return ChromLengthSource.TABLE_EXTENT
    if isinstance(table, TabixGenomicPositionTable):
        # The VCF backend included: it inherits the tabix probe (gain#509).
        return ChromLengthSource.TABIX_ESTIMATE
    raise NotImplementedError(
        f"{type(table).__name__} does not say what its chromosome lengths "
        f"measure; add it to ChromLengthSource's ladder")
