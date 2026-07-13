"""Record contract and pure tabular parser factory.

A genomic position table yields a **record**: a plain six-element tuple whose
slot positions are named by the module-level integer constants below --
chromosome, start position, end position, reference allele, alternative
allele, and an opaque backend payload.  The five core fields are decoded
eagerly; the payload stays lazy (for a tabular backend it is the raw row,
which decodes columns only when a caller asks for one).

The *tuple* is immutable: six slots, none of which can be rebound.  That
promise stops at the payload, which is the backend's raw row held **by
reference** -- it is deliberately neither copied nor frozen, because that is
what keeps it lazy.  A mutable raw row therefore stays mutable through the
record's payload slot.  (Both halves are pinned in test_record_parser.py.)

This module is deliberately pure: it imports no pysam, no file handles and no
genomic resource, so :func:`build_tabular_parser` can be unit-tested against
plain lists of strings.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# Slot positions inside a record tuple.
CHROM = 0
POS_BEGIN = 1
POS_END = 2
REF = 3
ALT = 4
PAYLOAD = 5

# A record is a plain six-element tuple; ``tuple[Any, ...]`` keeps the slots
# individually usable without over-constraining the payload's static type.
Record = tuple[Any, ...]

# A raw tabular row: an indexable sequence of string cells.
TabularRow = Sequence[str]

# A tabular parser maps a raw row to a record, or to ``None`` when the row's
# contig is absent from a configured chromosome map.
TabularParser = Callable[[TabularRow], "Record | None"]


def build_tabular_parser(
    chrom_key: int,
    pos_begin_key: int,
    pos_end_key: int,
    ref_key: int | None,
    alt_key: int | None,
    rev_chrom_map: dict[str, str] | None,
    *,
    zero_based: bool,
) -> TabularParser:
    """Build a pure row->record parser for a tabular backend.

    The parser is a pure function of the resolved column keys, the reverse
    chromosome map (file contig -> reference contig) and the zero-based flag.
    It fuses record construction with one of four transform specialisations,
    selecting one **once** here rather than branching per line:

    * identity -- no chromosome mapping, one-based coordinates;
    * zero-based -- shift begin/end from a half-open zero-based interval;
    * chromosome-mapping -- remap the contig, dropping rows whose contig is
      absent from the map (the parser returns ``None``);
    * zero-based **and** chromosome-mapping -- both of the above.

    Reference and alternative are read from their columns when configured and
    are ``None`` otherwise.  The returned interval is closed on both sides and
    one-based, exactly as today.

    What the per-row body costs, precisely.  Each specialisation's body is
    fully inlined: no helper call and no intermediate tuple is built per row,
    only the record itself.  The row still pays two ``is not None`` checks on
    ``ref_key``/``alt_key`` -- they are loop-invariant, but folding them out
    would mean crossing the ref/alt presence (four combinations, since ref and
    alt are configured independently) with the four specialisations here, i.e.
    sixteen near-identical closures.  Two pointer compares are not worth that,
    so the ref/alt reads stay inline and this docstring states the cost rather
    than claiming a branch-free body.  The zero-based ``pos_begin == pos_end``
    check is data-dependent and cannot be hoisted at all.
    """
    if rev_chrom_map is not None and zero_based:
        def parse_zero_based_mapped(raw: TabularRow) -> Record | None:
            rchrom = rev_chrom_map.get(raw[chrom_key])
            if rchrom is None:
                return None
            pos_begin = int(raw[pos_begin_key])
            pos_end = int(raw[pos_end_key])
            if pos_begin == pos_end:
                pos_end += 1
            pos_begin += 1
            return (
                rchrom, pos_begin, pos_end,
                raw[ref_key] if ref_key is not None else None,
                raw[alt_key] if alt_key is not None else None,
                raw)
        return parse_zero_based_mapped

    if rev_chrom_map is not None:
        def parse_mapped(raw: TabularRow) -> Record | None:
            rchrom = rev_chrom_map.get(raw[chrom_key])
            if rchrom is None:
                return None
            return (
                rchrom, int(raw[pos_begin_key]), int(raw[pos_end_key]),
                raw[ref_key] if ref_key is not None else None,
                raw[alt_key] if alt_key is not None else None,
                raw)
        return parse_mapped

    if zero_based:
        def parse_zero_based(raw: TabularRow) -> Record | None:
            pos_begin = int(raw[pos_begin_key])
            pos_end = int(raw[pos_end_key])
            if pos_begin == pos_end:
                pos_end += 1
            pos_begin += 1
            return (
                raw[chrom_key], pos_begin, pos_end,
                raw[ref_key] if ref_key is not None else None,
                raw[alt_key] if alt_key is not None else None,
                raw)
        return parse_zero_based

    def parse_identity(raw: TabularRow) -> Record | None:
        return (
            raw[chrom_key], int(raw[pos_begin_key]), int(raw[pos_end_key]),
            raw[ref_key] if ref_key is not None else None,
            raw[alt_key] if alt_key is not None else None,
            raw)
    return parse_identity
