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

A repair stores what the resolver found as ``CHROM_LENGTHS_FILE`` --
one block per source, contig to length, the table's block carrying the
reason for a contig it had no length for -- together with a
``DerivedFrom`` key: the label the genome resolved from and the manifest
md5 of every table file, which is what a later repair compares to tell a
current file from a stale one without opening the table (gain#1576).
``save_chrom_lengths`` / ``load_chrom_lengths`` are the file's two
seams; loading never raises, a file that cannot be read as one reads as
absent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)

if TYPE_CHECKING:
    from gain.genomic_resources.reference_genome import ReferenceGenome
    from gain.genomic_resources.repository import GenomicResource

    from .base import GenomicScore

logger = logging.getLogger(__name__)

#: Where a repair stores the resolver's answers, relative to the resource.
CHROM_LENGTHS_FILE = "statistics/chrom_lengths.json"

#: The layout of that file.  A stored file of another format is stale:
#: read as absent and rewritten by the next repair.
CHROM_LENGTHS_FORMAT = 1

# Both enums are the table layer's -- the provenance vocabulary because
# three of its four members are facts each backend declares about its own
# format (``chrom_length_source``), the extent because it is the table
# that proves a contig empty or fails to measure it; re-exported from
# here, where the record that carries them lives, so a caller finds them
# beside the resolver (the ``BIGWIG_VALUE_COLUMN`` pattern) and the
# statistics implementation need not import the table package at all
# (gain#410).
__all__ = [
    "CHROM_LENGTHS_FILE",
    "CHROM_LENGTHS_FORMAT",
    "ChromLength",
    "ChromLengthAnswer",
    "ChromLengthSource",
    "ContigExtent",
    "DerivedFrom",
    "StoredChromLengths",
    "derive_chrom_length",
    "derive_chrom_lengths",
    "load_chrom_lengths",
    "save_chrom_lengths",
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
    """Every source's answer for every contig, and what they derive from."""

    lengths: dict[str, ChromLength]
    derived_from: DerivedFrom


def _serialize(
    stored: StoredChromLengths, table_source: ChromLengthSource,
) -> str:
    """One block per source, contig -> length.

    A contig a source did not answer is absent from that source's block;
    a contig the TABLE could not answer carries the table's reason in
    the table's own block, ``table_source``, and in no other.  The
    table's block comes first: it is the one that names every contig,
    in the table's order, which is the order the file is read back in.
    """
    sources: dict[str, dict[str, int | str]] = {table_source.value: {}}
    for chrom, resolved in stored.lengths.items():
        for source, length in resolved.answers.items():
            sources.setdefault(source.value, {})[chrom] = length
        if resolved.extent is not None:
            sources.setdefault(table_source.value, {})[chrom] = \
                resolved.extent.name.lower()
    return json.dumps({
        "format": CHROM_LENGTHS_FORMAT,
        "derived_from": {
            "reference_genome": stored.derived_from.reference_genome,
            "files_md5": stored.derived_from.files_md5,
        },
        "sources": sources,
    }, indent=2)


def _deserialize(content: str) -> StoredChromLengths | None:
    """The stored lengths, or ``None`` for a file of another format.

    Raises ``ValueError``, ``KeyError`` or ``TypeError`` on a document
    that is not one: the caller reads those as absent.
    """
    document = json.loads(content)
    if document["format"] != CHROM_LENGTHS_FORMAT:
        return None
    # Contigs in order of first appearance across the blocks, which is
    # the table's order: the writer puts the table's block -- the one
    # that names every contig -- first.
    answers: dict[str, dict[ChromLengthSource, int]] = {}
    extents: dict[str, ContigExtent] = {}
    for source_name, block in document["sources"].items():
        source = ChromLengthSource(source_name)
        for chrom, value in block.items():
            if isinstance(value, str):
                extents[chrom] = ContigExtent[value.upper()]
            elif isinstance(value, int) and not isinstance(value, bool):
                answers.setdefault(chrom, {})[source] = value
            else:
                raise TypeError(
                    f"{source_name}/{chrom}: {value!r} is neither a "
                    "length nor a reason")
            answers.setdefault(chrom, {})
    derived_from = document["derived_from"]
    return StoredChromLengths(
        lengths={
            chrom: ChromLength(
                answers=chrom_answers, extent=extents.get(chrom))
            for chrom, chrom_answers in answers.items()
        },
        derived_from=DerivedFrom(
            reference_genome=derived_from["reference_genome"],
            files_md5=dict(derived_from["files_md5"]),
        ),
    )


def save_chrom_lengths(
    resource: GenomicResource, stored: StoredChromLengths,
    table_source: ChromLengthSource,
) -> None:
    """Write ``stored`` as the resource's ``CHROM_LENGTHS_FILE``.

    ``table_source`` is the source the score's table answers under --
    the block that carries a contig's reason when the table had no
    length for it.
    """
    with resource.open_raw_file(CHROM_LENGTHS_FILE, mode="wt") as outfile:
        outfile.write(_serialize(stored, table_source))


def load_chrom_lengths(resource: GenomicResource) -> StoredChromLengths | None:
    """Read the resource's ``CHROM_LENGTHS_FILE``; ``None`` when it has none.

    Absence is a normal state, not an error: a resource repaired before
    the file existed has nothing stored until its next repair, and one
    written in another format is read the same way.  A file that cannot
    be read as one -- a repair killed mid-write leaves a truncated one,
    and the write is not atomic -- reads as absent too, with a WARNING
    naming it: absent, the ordinary repair rewrites it; raised, the gate
    would fail the resource over a file the repair is about to replace.
    """
    try:
        content = resource.get_file_content(CHROM_LENGTHS_FILE)
    except FileNotFoundError:
        return None
    try:
        stored = _deserialize(content)
    except (ValueError, KeyError, TypeError, AttributeError) as err:
        # ``json.JSONDecodeError`` is a ``ValueError``; so is an enum
        # member the name does not match.  The others are a document
        # of the wrong shape.
        logger.warning(
            "resource <%s>: %s cannot be read as stored chromosome "
            "lengths (%s); treating it as absent",
            resource.resource_id, CHROM_LENGTHS_FILE, err)
        return None
    if stored is None:
        logger.info(
            "resource <%s>: %s is of another format; treating it as absent",
            resource.resource_id, CHROM_LENGTHS_FILE)
    return stored
