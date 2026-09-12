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
tabix estimate, applied per contig of the score.  This module answers every
rung live: the genome rung from the ``ReferenceGenome`` a caller with a GRR
hands in (the statistics build does; the score's own methods have none, so
they resolve through the table alone), the rest through the table.  The
stored ``statistics/chrom_lengths.json`` (gain#1419) is a later slice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)

if TYPE_CHECKING:
    from gain.genomic_resources.reference_genome import ReferenceGenome
    from gain.genomic_resources.repository import GenomicResource

    from .base import GenomicScore

# The provenance vocabulary is the table layer's, because three of its four
# members are facts each backend declares about its own format
# (``chrom_length_source``); re-exported from here, where the score-level
# API that answers with it lives, so a caller of ``get_chrom_length_source``
# finds the enum beside the method (the ``BIGWIG_VALUE_COLUMN`` pattern).
__all__ = [
    "CHROM_LENGTHS_FILE",
    "ChromLength",
    "ChromLengthSource",
    "DerivedFrom",
    "StoredChromLengths",
    "derive_chrom_length",
    "derive_chrom_lengths",
    "load_chrom_lengths",
    "save_chrom_lengths",
]

#: Where a repaired score keeps the resolver's answer, beside its other
#: statistics.  Under its own freshness gate, not ``stats_hash``'s.
CHROM_LENGTHS_FILE = "statistics/chrom_lengths.json"


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
    knows is refused the same way.  The score's methods screen first and
    word both refusals as every other score read does.
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
    the consumers that split regions (the statistics build) and persist
    the answer (gain#1419) look up by name; the dict keeps table order.

    Not memoised: on a tabix score every call re-runs the probe per contig,
    which is the cost gain#1419 takes out of the read path by storing this
    at repair.  Raises ``ValueError`` on a score that is not open.
    """
    return {
        chrom: derive_chrom_length(score, chrom, ref_genome)
        for chrom in score.get_all_chromosomes()
    }


@dataclass(frozen=True)
class DerivedFrom:
    """What a stored answer was computed from -- the file's freshness key.

    ``reference_genome`` is the label the genome was actually resolved
    from, ``None`` when there was none to resolve: no label, a label that
    is not a resource id, or a genome the repository could not find.  So
    a genome that turns up later reads as a change.  ``files_md5`` is the
    manifest md5 of every data file of the table, keyed by name -- as the
    manifest records it, which for an entry it has not digested is none.
    """

    reference_genome: str | None
    files_md5: dict[str, str | None]


@dataclass(frozen=True)
class StoredChromLengths:
    """The resolver's answer for every contig, and what it was derived from."""

    lengths: dict[str, ChromLength]
    derived_from: DerivedFrom


def _serialize(stored: StoredChromLengths) -> str:
    def record(resolved: ChromLength) -> dict[str, Any]:
        return {
            "length": resolved.length,
            "source": (
                resolved.source.name if resolved.source is not None
                else None),
            "extent": (
                resolved.extent.name if resolved.extent is not None
                else None),
        }
    return json.dumps({
        "derived_from": {
            "reference_genome": stored.derived_from.reference_genome,
            "files_md5": stored.derived_from.files_md5,
        },
        "lengths": {
            chrom: record(resolved)
            for chrom, resolved in stored.lengths.items()
        },
    }, indent=2)


def _deserialize(content: str) -> StoredChromLengths:
    document = json.loads(content)
    derived_from = document["derived_from"]
    return StoredChromLengths(
        lengths={
            chrom: ChromLength(
                length=record["length"],
                source=(
                    ChromLengthSource[record["source"]]
                    if record["source"] is not None else None),
                extent=(
                    ContigExtent[record["extent"]]
                    if record["extent"] is not None else None),
            )
            for chrom, record in document["lengths"].items()
        },
        derived_from=DerivedFrom(
            reference_genome=derived_from["reference_genome"],
            files_md5=derived_from["files_md5"],
        ),
    )


def save_chrom_lengths(
    resource: GenomicResource, stored: StoredChromLengths,
) -> None:
    """Write ``stored`` as the resource's ``CHROM_LENGTHS_FILE``."""
    with resource.open_raw_file(CHROM_LENGTHS_FILE, mode="wt") as outfile:
        outfile.write(_serialize(stored))


def load_chrom_lengths(resource: GenomicResource) -> StoredChromLengths | None:
    """Read the resource's ``CHROM_LENGTHS_FILE``; ``None`` when it has none.

    Absence is a normal state, not an error: a resource repaired before
    the file existed has nothing stored until its next repair.
    """
    try:
        content = resource.get_file_content(CHROM_LENGTHS_FILE)
    except FileNotFoundError:
        return None
    return _deserialize(content)
