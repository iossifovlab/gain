"""PROTOTYPE (gain#823) -- per-record allocations on the segment-score path.

THE QUESTION
------------
``fetch_region_segment_scores`` allocates four Python objects per record.
gain#823 claims two of them are pure waste, and that removing them is 1.96x
faster with output element-wise identical.  Those numbers were measured
against installed ``gain-core`` 2026.8.3 (master ``24f7c0651``), before the
``fetch_region_values`` -> ``fetch_region_segment_scores`` rename and before
the score-filter work landed.  This module re-asks the question on the
CURRENT master, one allocation at a time:

  1. Does each removal individually pay, and how much?
  2. Do they compose to the claimed ~1.96x?
  3. Is the output still element-wise identical to today's method?

It is deliberately three INDEPENDENT toggles rather than one before/after
switch: the issue's table attributes 0.154 us/rec to the ``_fetch`` tuple and
0.089 us/rec to ``_record_to_begin_end``, and a single combined measurement
cannot check that split.

WHAT IS PRESERVED IN EVERY VARIANT
----------------------------------
The things gain#823 explicitly keeps: the six-slot record contract (so the
ADR 0008 ``validate_records`` seam is untouched), the ``pos_end < pos_begin``
validation, the pluggable extractor, and the per-record values list.

This module is pure: no printing, no terminal, no argument parsing.  ``tui.py``
is the throwaway shell around it.  If a variant wins, IT is the thing that
lifts into ``genomic_scores.py`` / ``table_bigwig.py`` -- not the shell.
"""
from __future__ import annotations

import statistics
import time
from collections.abc import Generator, Iterator
from typing import Any, NamedTuple

from gain.genomic_resources.genomic_position_table.record import (
    ALT,
    CHROM,
    POS_BEGIN,
    POS_END,
    REF,
    Record,
)

Segment = tuple[int, int, list[Any]]


class Config(NamedTuple):
    """Which of the three removals are switched on.

    ``all-off`` is not a reimplementation of today's path -- it IS today's
    path, ``score.fetch_region_segment_scores`` called directly, so the
    baseline column can never drift from the method being compared against.
    """

    raw_parser: bool = False   # drop the _fetch 3-tuple (bigWig only)
    inline_span: bool = False  # drop _record_to_begin_end's return tuple
    inline_extract: bool = False  # bypass get_score_values_from_record

    @property
    def label(self) -> str:
        if not any(self):
            return "baseline (today)"
        return "+".join([
            *(["raw-parser"] if self.raw_parser else []),
            *(["inline-span"] if self.inline_span else []),
            *(["inline-extract"] if self.inline_extract else []),
        ])


ALL_OFF = Config()
ALL_ON = Config(True, True, True)

# The eight combinations, baseline first.
ALL_CONFIGS = [
    Config(r, s, e)
    for r in (False, True)
    for s in (False, True)
    for e in (False, True)
]
ALL_CONFIGS.sort(key=lambda c: sum(c))


# ---------------------------------------------------------------------------
# Removal 1: the _fetch 3-tuple  (table_bigwig.py)
# ---------------------------------------------------------------------------

def _records_raw_parser(
    table: Any, chrom: str, pos_begin: int | None, pos_end: int | None,
) -> Generator[Record, None, None]:
    """``get_records_in_region`` with the ``_fetch`` generator level removed.

    Today the chunk loop lives in ``_fetch``, which yields
    ``(interval[0] + 1, interval[1], interval[2])`` -- a 3-tuple built only so
    that ``get_records_in_region`` can hand it to ``parser(chrom, interval)``,
    which reads the three slots back out and builds the six-slot record.

    Here the parser takes the RAW interval and does the ``+1`` itself, so the
    intermediate tuple and one whole generator level both disappear.  The
    parser abstraction survives: still one callable, still applied per record.
    Everything else -- chrom mapping on both ends, the ``max(0, begin - 1)``
    conversion, the chrom-length clamp, the adaptive window, the
    closed-while-in-flight assert -- is carried over unchanged.
    """
    fchrom = table._map_file_chrom(chrom)
    if fchrom not in table.chroms:
        raise KeyError(
            f"bigwig table of resource "
            f"{table.genomic_resource.resource_id}: contig {chrom!r} "
            f"(mapped to {fchrom!r}) is not among the file's contigs")

    chrom_len: int = table.chroms[fchrom]
    start: int = max(0, (0 if pos_begin is None else pos_begin) - 1)
    stop: int = min(chrom_len if pos_end is None else pos_end, chrom_len)

    window = table._window
    fetch_chunk = table._fetch_chunk

    while start < stop:
        assert table._bw_file is not None, \
            "bigWig table closed while a region fetch was in flight"
        intervals, start = fetch_chunk(window, fchrom, start, stop, stop)
        if not intervals:
            return
        for interval in intervals:
            # The parser, taking the raw interval: one layer earlier than
            # today, and the only place the +1 now happens.
            yield (chrom, interval[0] + 1, interval[1], None, None,
                   interval[2])


# ---------------------------------------------------------------------------
# Removals 2 and 3: the span tuple and the extractor call (genomic_scores.py)
# ---------------------------------------------------------------------------
#
# Four specialised loops rather than one loop branching on flags per record --
# a per-record ``if cfg.inline_span`` would charge the measurement for the
# prototype's own scaffolding.  Each is a faithful copy of
# ``_clipped_score_values``; they differ only in the two lines under test.

def _span_error(record: Record) -> OSError:
    """The ``_record_to_begin_end`` refusal, verbatim, off the cold path."""
    chrom, pos_begin, pos_end = record[CHROM], record[POS_BEGIN], \
        record[POS_END]
    ref, alt = record[REF], record[ALT]
    ref_alt = f" {ref}->{alt}" if ref is not None or alt is not None else ""
    return OSError(
        f"The resource record {chrom}:{pos_begin}-{pos_end}{ref_alt} "
        f"has a region with end {pos_end} smaller than the "
        f"beginning {pos_begin}.")


def _clipped_baseline(
    score: Any, records: Iterator[Record],
    pos_begin: int | None, pos_end: int | None, score_defs: list[Any],
) -> Generator[Segment, None, None]:
    """Today's body: the span tuple and the method call, both kept."""
    record_to_begin_end = score._record_to_begin_end
    values_from_record = score.get_score_values_from_record
    for record in records:
        # The three-way unpack, exactly as _clipped_score_values writes it --
        # a slice would add an allocation the code under test does not make.
        _chrom, rec_begin, rec_end = record_to_begin_end(record)
        if pos_begin is not None and rec_end < pos_begin:
            continue
        val = values_from_record(record, score_defs)
        left = max(pos_begin, rec_begin) if pos_begin is not None else rec_begin
        right = min(pos_end, rec_end) if pos_end is not None else rec_end
        yield (left, right, val)


def _clipped_inline_span(
    score: Any, records: Iterator[Record],
    pos_begin: int | None, pos_end: int | None, score_defs: list[Any],
) -> Generator[Segment, None, None]:
    """``_record_to_begin_end`` inlined: no 3-tuple whose CHROM is discarded.

    The only caller unpacks the result as ``_chrom, rec_begin, rec_end`` and
    throws the first element away on the very next line.  The validation the
    method performs is a single comparison and is kept here, in place.
    """
    values_from_record = score.get_score_values_from_record
    for record in records:
        rec_begin = record[POS_BEGIN]
        rec_end = record[POS_END]
        if rec_end < rec_begin:
            raise _span_error(record)
        if pos_begin is not None and rec_end < pos_begin:
            continue
        val = values_from_record(record, score_defs)
        left = max(pos_begin, rec_begin) if pos_begin is not None else rec_begin
        right = min(pos_end, rec_end) if pos_end is not None else rec_end
        yield (left, right, val)


def _clipped_inline_extract(
    score: Any, records: Iterator[Record],
    pos_begin: int | None, pos_end: int | None, score_defs: list[Any],
) -> Generator[Segment, None, None]:
    """The extractor hoisted, the list built inline: one fewer call per record.

    Not a removal of the values list -- gain#823 measured reusing a shared
    buffer as worth 0.017 us/rec against an aliasing hazard, and rejected it.
    This removes the *method call* that wraps the list comprehension.
    """
    record_to_begin_end = score._record_to_begin_end
    extract = score._extract_value
    for record in records:
        # The three-way unpack, exactly as _clipped_score_values writes it --
        # a slice would add an allocation the code under test does not make.
        _chrom, rec_begin, rec_end = record_to_begin_end(record)
        if pos_begin is not None and rec_end < pos_begin:
            continue
        val = [extract(record, sd) for sd in score_defs]
        left = max(pos_begin, rec_begin) if pos_begin is not None else rec_begin
        right = min(pos_end, rec_end) if pos_end is not None else rec_end
        yield (left, right, val)


def _clipped_inline_both(
    score: Any, records: Iterator[Record],
    pos_begin: int | None, pos_end: int | None, score_defs: list[Any],
) -> Generator[Segment, None, None]:
    """Both genomic_scores.py removals together."""
    extract = score._extract_value
    for record in records:
        rec_begin = record[POS_BEGIN]
        rec_end = record[POS_END]
        if rec_end < rec_begin:
            raise _span_error(record)
        if pos_begin is not None and rec_end < pos_begin:
            continue
        val = [extract(record, sd) for sd in score_defs]
        left = max(pos_begin, rec_begin) if pos_begin is not None else rec_begin
        right = min(pos_end, rec_end) if pos_end is not None else rec_end
        yield (left, right, val)


_CLIPPED = {
    (False, False): _clipped_baseline,
    (True, False): _clipped_inline_span,
    (False, True): _clipped_inline_extract,
    (True, True): _clipped_inline_both,
}


# ---------------------------------------------------------------------------
# The one entry point
# ---------------------------------------------------------------------------

def segment_scores(
    score: Any, chrom: str, pos_begin: int, pos_end: int,
    scores: list[str] | None, cfg: Config,
) -> Iterator[Segment]:
    """``fetch_region_segment_scores`` under ``cfg``.

    ``ALL_OFF`` returns the real method, untouched.
    """
    if not any(cfg):
        return score.fetch_region_segment_scores(
            chrom, pos_begin, pos_end, scores)

    records = (
        _records_raw_parser(score.table, chrom, pos_begin, pos_end)
        if cfg.raw_parser
        else score.fetch_records(chrom, pos_begin, pos_end)
    )
    # The same eager guards region_values_from_records runs: closed score,
    # unknown contig, unknown score id -- refused before the first record.
    score_defs = score._region_read_defs(chrom, scores)
    clipped = _CLIPPED[cfg.inline_span, cfg.inline_extract]
    return clipped(score, records, pos_begin, pos_end, score_defs)


def is_bigwig(score: Any) -> bool:
    """Whether this score's table is the one the raw-parser removal is about.

    gain#823 scopes the ``_fetch`` tuple to ``table_bigwig.py``; the other two
    removals are in ``genomic_scores.py`` and are backend-agnostic, which is a
    claim worth pointing this prototype at a tabix copy of the same data to
    check.
    """
    return hasattr(score.table, "_fetch_chunk")


def applicable(score: Any, cfg: Config) -> bool:
    """Whether ``cfg`` can run against this score's backend."""
    return is_bigwig(score) or not cfg.raw_parser


def native_segments(
    score: Any, chrom: str, pos_begin: int, pos_end: int,
) -> Iterator[Segment]:
    """``pyBigWig.intervals()`` with no gain layer -- the floor to measure to.

    Not a candidate implementation: it has no record contract, no validation
    and no extractor.  It is here because "3.37x native" is the claim
    gain#823 makes about where the per-record path lands.
    """
    bw = score.table._bw_file
    fchrom = score.table._map_file_chrom(chrom)
    intervals = bw.intervals(fchrom, max(0, pos_begin - 1), pos_end) or []
    return ((b + 1, e, [v]) for b, e, v in intervals)


# ---------------------------------------------------------------------------
# Measuring and checking -- still pure: they return numbers, they don't print
# ---------------------------------------------------------------------------

class Timing(NamedTuple):
    label: str
    records: int
    median_s: float
    passes: list[float]

    @property
    def us_per_rec(self) -> float:
        return self.median_s / self.records * 1e6 if self.records else 0.0


def time_one_pass(stream_factory: Any) -> tuple[float, int]:
    """One full drain of a fresh stream: wall seconds and record count."""
    start = time.perf_counter()
    n = 0
    for _ in stream_factory():
        n += 1
    return time.perf_counter() - start, n


def measure(
    stream_factories: list[tuple[str, Any]], passes: int,
) -> list[Timing]:
    """Interleave the variants across ``passes`` rounds, report medians.

    Interleaved rather than variant-at-a-time: a 5.9 GB file read through the
    page cache drifts over the length of a run, and taking all of one
    variant's passes before the next's charges that drift entirely to
    whichever went last.  Medians, not means, for the same reason.
    """
    samples: dict[str, list[float]] = {label: [] for label, _ in
                                       stream_factories}
    counts: dict[str, int] = {}
    for _ in range(passes):
        for label, factory in stream_factories:
            elapsed, n = time_one_pass(factory)
            samples[label].append(elapsed)
            counts[label] = n
    return [
        Timing(label, counts[label], statistics.median(samples[label]),
               samples[label])
        for label, _ in stream_factories
    ]


class Verdict(NamedTuple):
    identical: bool
    compared: int
    first_diff: str | None


def verify_identical(
    reference: Iterator[Segment], candidate: Iterator[Segment],
) -> Verdict:
    """Element-wise comparison of two segment streams.

    Exact equality, not approximate: both read the same bytes off the same
    file through the same extractor, so any difference at all is a change in
    behaviour rather than float noise.

    The reference is MATERIALISED before the candidate is opened, rather than
    the two being stepped in lockstep.  A tabix-backed score answers both
    fetches from one ``pysam.TabixFile`` handle, and two live iterators over
    one handle share its cursor -- interleaving their ``next()`` calls makes
    each consume records meant for the other, which reads back as a candidate
    that is off by one from the second record onward.  That is a trap in this
    harness, not a difference in the variants, and it cost a false DIFFERS on
    the tabix copy before the streams were separated.  Costs one list of the
    region's segments; a prototype can afford it.
    """
    expected = list(reference)
    n = 0
    for got in candidate:
        if n >= len(expected):
            return Verdict(False, n, f"candidate ran past record {n}")
        if got != expected[n]:
            return Verdict(
                False, n, f"record {n}: {expected[n]!r} != {got!r}")
        n += 1
    if n != len(expected):
        return Verdict(
            False, n, f"candidate ended at {n}, reference has {len(expected)}")
    return Verdict(True, n, None)
