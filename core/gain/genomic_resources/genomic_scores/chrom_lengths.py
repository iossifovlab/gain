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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)
from gain.genomic_resources.utils import read_resource_id_label
from gain.utils.log_safety import escape_unsafe_characters

if TYPE_CHECKING:
    from gain.genomic_resources.reference_genome import ReferenceGenome
    from gain.genomic_resources.repository import GenomicResource, Manifest

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
    "as_chrom_length_source",
    "best_chrom_length",
    "derive_chrom_length",
    "derive_chrom_lengths",
    "files_md5_of",
    "load_chrom_lengths",
    "load_current_chrom_lengths",
    "refuse_unanswered_source",
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

    def describes(
        self, resource: GenomicResource, files: Iterable[str],
    ) -> bool:
        """Whether ``resource``, as it is now, is what this was derived from.

        The check a reader with no repository can make: the label as the
        resource carries it today, and the STORED manifest's md5 of every
        one of the table's ``files`` -- a resource with no stored
        manifest is never described, since a read path builds none.  A
        label whose genome did not resolve at repair was recorded as
        none, so it reads as stale here until it does -- conservative on
        purpose (gain#1419); the answer then costs the live probe and
        nothing more.  The implementation's gate, which holds a
        repository, resolves that one case instead.
        """
        if read_resource_id_label(
                resource, "reference_genome") != self.reference_genome:
            return False
        manifest = resource.get_loaded_manifest()
        return manifest is not None and \
            all(file_name in manifest for file_name in files) and \
            self.files_md5 == files_md5_of(manifest, files)


def files_md5_of(
    manifest: Manifest, files: Iterable[str],
) -> dict[str, str | None]:
    """The manifest md5 of every one of ``files``, keyed by name.

    One definition of "the same files" for every gate that asks -- the
    statistics hash, the stored lengths' key and the score's own reader
    -- so they cannot drift apart on what counts as a data file.  As the
    manifest records it, which for an entry it has not digested is none;
    a file the manifest does not list is a ``KeyError``.
    """
    return {
        file_name: manifest[file_name].md5 for file_name in sorted(files)}


@dataclass(frozen=True)
class StoredChromLengths:
    """Every source's answer for every contig, and what they derive from.

    ``table_source`` is the source the score's table answers under: its
    block is the one that names every contig, in the table's order, and
    the one that carries a contig's reason when the table had no length
    for it.
    """

    lengths: dict[str, ChromLength]
    derived_from: DerivedFrom
    table_source: ChromLengthSource


def _serialize(stored: StoredChromLengths) -> str:
    """One block per source, contig -> length.

    A contig a source did not answer is absent from that source's block;
    a contig the TABLE could not answer carries the table's reason in
    the table's own block and in no other.
    """
    table_block: dict[str, int | str] = {}
    sources = {stored.table_source.value: table_block}
    for chrom, resolved in stored.lengths.items():
        for source, length in resolved.answers.items():
            sources.setdefault(source.value, {})[chrom] = length
        if resolved.extent is not None:
            table_block[chrom] = resolved.extent.name.lower()
    return json.dumps({
        "format": CHROM_LENGTHS_FORMAT,
        "derived_from": {
            "reference_genome": stored.derived_from.reference_genome,
            "files_md5": stored.derived_from.files_md5,
        },
        "table_source": stored.table_source.value,
        "sources": sources,
    }, indent=2)


def _is_a_count(value: object) -> bool:
    """An ``int`` that is not a ``bool``, which is an ``int`` too."""
    return isinstance(value, int) and not isinstance(value, bool)


def _deserialize(content: str) -> StoredChromLengths:
    """The stored lengths.  Raises on a document that is not one -- of
    another format, or of the wrong shape -- and the caller reads that
    as absent."""
    document = json.loads(content)
    # ``True`` and ``1.0`` compare equal to ``1``; neither is the format.
    if not _is_a_count(document["format"]) or \
            document["format"] != CHROM_LENGTHS_FORMAT:
        raise ValueError(
            f"format {document['format']!r} is not {CHROM_LENGTHS_FORMAT}")
    table_source = ChromLengthSource(document["table_source"])
    # The table's block names every contig, in the table's order.
    answers: dict[str, dict[ChromLengthSource, int]] = {
        chrom: {} for chrom in document["sources"][table_source.value]}
    extents: dict[str, ContigExtent] = {}
    for source_name, block in document["sources"].items():
        source = ChromLengthSource(source_name)
        for chrom, value in block.items():
            if isinstance(value, str) and source is table_source:
                extents[chrom] = ContigExtent[value.upper()]
            elif _is_a_count(value):
                answers[chrom][source] = value
            else:
                raise TypeError(
                    f"{source_name}/{chrom}: {value!r} is neither a "
                    "length nor the table's reason")
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
        table_source=table_source,
    )


def as_chrom_length_source(
    source: ChromLengthSource | str | None,
) -> ChromLengthSource | None:
    """The member ``source`` names -- itself, or by its string value.

    ``None`` stays ``None``: the caller's "the best-ranked one".  Raises
    ``ValueError`` naming the valid values on a string that is none of
    them.
    """
    if source is None or isinstance(source, ChromLengthSource):
        return source
    try:
        return ChromLengthSource(source)
    except ValueError:
        raise ValueError(
            f"{source!r} is not a chromosome-length source; one of "
            f"{[s.value for s in ChromLengthSource]}") from None


def load_current_chrom_lengths(
    score: GenomicScore,
) -> StoredChromLengths | None:
    """The stored lengths, if the file describes ``score`` as it is now.

    Three ways for it not to, all normal and none a WARNING: no file (a
    resource repaired before the file existed, or never -- the state of
    every resource until its next repair, so said at DEBUG, as anything
    louder would fire on every open of every one of them), a
    ``derived_from`` that no longer matches (a label re-pointed and not
    yet repaired -- trusted, it would answer the old genome's lengths),
    and a contig list that is not the table's (a ``chrom_mapping``
    changed under it) -- the last two INFO, since something changed and
    a repair is due.  Until it runs the score's reads resolve live.
    Asked of an open score.
    """
    stored = load_chrom_lengths(score.resource)
    if stored is None:
        logger.debug(
            "genomic score %s has no stored chromosome lengths; "
            "resolving them live", score.resource_id)
        return None
    # The manifest first, and only the stored one: the table's file set
    # is read off the manifest too, and a read path builds none.
    if score.resource.get_loaded_manifest() is None or \
            not stored.derived_from.describes(
                score.resource, score.resource_files()):
        logger.info(
            "stored chromosome lengths of genomic score %s are stale "
            "(another label, other table files, or no manifest to compare "
            "against); resolving them live", score.resource_id)
        return None
    if list(stored.lengths) != score.get_all_chromosomes():
        logger.info(
            "stored chromosome lengths of genomic score %s list other "
            "contigs than its table; resolving them live until the next "
            "repair", score.resource_id)
        return None
    return stored


def best_chrom_length(
    chrom: str, resolved: ChromLength, contigs: list[str],
) -> ChromLengthAnswer:
    """``resolved.best``, or the table's refusal when there is none --
    ``contigs`` being the table's, which the refusal names."""
    best = resolved.best
    if best is None:
        assert resolved.extent is not None
        raise ValueError(resolved.extent.refusal(chrom, contigs))
    return best


def refuse_unanswered_source(
    chrom: str, source: ChromLengthSource, resolved: ChromLength,
    *, table_source: ChromLengthSource, contigs: list[str],
) -> None:
    """Raise for a ``source`` that has no answer in ``resolved``.

    The table's own source is refused in the table's words, which say
    whether the contig is empty or merely unmeasured; any other names
    the sources that do answer the contig.
    """
    if source is table_source and resolved.extent is not None:
        raise ValueError(resolved.extent.refusal(chrom, contigs))
    answering = sorted(resolved.answers, key=lambda s: s.rank, reverse=True)
    raise ValueError(
        f"{source.value} has no length for {chrom}; the sources that "
        f"answer it: {[s.value for s in answering]}")


def save_chrom_lengths(
    resource: GenomicResource, stored: StoredChromLengths,
) -> None:
    """Write ``stored`` as the resource's ``CHROM_LENGTHS_FILE``."""
    with resource.open_raw_file(CHROM_LENGTHS_FILE, mode="wt") as outfile:
        outfile.write(_serialize(stored))


def load_chrom_lengths(resource: GenomicResource) -> StoredChromLengths | None:
    """Read the resource's ``CHROM_LENGTHS_FILE``; ``None`` when it has none.

    Absence is a normal state, not an error: a resource repaired before
    the file existed has nothing stored until its next repair.  A file
    that cannot be read as one -- of another format, or truncated by a
    repair killed mid-write, the write not being atomic -- reads as
    absent too, with a WARNING naming it: absent, the ordinary repair
    rewrites it; raised, the gate would fail the resource over a file
    the repair is about to replace.
    """
    try:
        return _deserialize(resource.get_file_content(CHROM_LENGTHS_FILE))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
        # ``json.JSONDecodeError`` and ``UnicodeDecodeError`` are
        # ``ValueError``s; so is an enum member the name does not
        # match.  The others are a document of the wrong shape -- and
        # ``OSError`` a fetch that failed, which a remote protocol
        # reports a transient 5xx as: the optional file must not fail
        # the open of a score that reads fine without it.  The cause
        # quotes the file, which is repository content: escaped so it
        # cannot end the line and start a forged record (gain#642).
        logger.warning(
            "resource <%s>: %s cannot be read as stored chromosome "
            "lengths (%s); treating it as absent",
            resource.resource_id, CHROM_LENGTHS_FILE,
            escape_unsafe_characters(str(err)))
        return None
