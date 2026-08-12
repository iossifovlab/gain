"""PROTOTYPE -- throwaway.  See README.md next to this file.

THE QUESTION
------------
Epic gain#770 wants a position score's info page to document coverage and
content structure: covered positions, segment count, and a segment-length
distribution -- collected on the SAME scan pass that already builds the
value histograms, over a contig that the scan splits into region chunks.

Covered positions merge trivially (chunks are disjoint, so counts add).
Segments do not: a segment the chunking cut in half must not be counted
twice, and a segment spanning three chunks must not be counted three
times.  So the per-chunk statistic has to carry its scanned extent plus
whatever runs are still open at its edges, and the merge has to stitch
them -- which makes the merge ORDER-SENSITIVE, unlike the min/max and
histogram merges the scan already has.

The question this prototype answers: **is that per-chunk shape actually
sufficient, and is the merge correct for every chunking?**  Concretely --

  1. Does `merge(chunks) == unchunked` hold for every region size, for
     runs that span two chunks, three chunks, and a whole chunk?
  2. Is the merge associative -- can regions be folded in any grouping,
     as a task graph is free to do?
  3. What happens when the fold order is wrong (regions of one contig
     out of order, or two contigs interleaved)?  Does it corrupt
     silently, or can the statistic detect it?
  4. Does the value histogram (today's statistic) ride along unchanged?

This module is the answer's carrier: a pure monoid over regions, no I/O
and no gain imports, so that whatever survives can be lifted into
`gain.genomic_resources.statistics` on its own.  The TUI shell imports
this; nothing flows the other way.

MODEL
-----
A *segment* is a maximal run of touching-or-overlapping rows, value-blind
(epic gain#770).  A *covered position* is a position any row spans.

The per-region statistic is the classic "longest run" monoid, carrying:
its extent, the closed segments (those that provably touch neither outer
edge), the open run at the left edge (`head_len`), the open run at the
right edge (`tail_len`), and the `one_run` flag for the case where those
two are the SAME run -- a chunk covered end to end by a single segment,
which is what makes a naive head/tail merge wrong for a run spanning
three or more chunks.

Position vs fragment
--------------------
A position score REFUSES rows that overlap or merely touch (the scan's
own validators raise on `begin <= prev_end` against raw spans), so its
segments are runs of ADJACENT rows -- `begin == prev_end + 1`.  A
fragment score refuses only backwards rows and permits overlapping and
NESTED fragments, so its union needs a running maximum end.  Both kinds
are modelled here, because the shared implementation has to serve both.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

Kind = Literal["position", "fragment"]

# Segment-length histogram: fixed log2 bins, a code-level constant per the
# epic.  Bin k holds lengths [2^k, 2^(k+1)); the last bin is open-ended.
SEG_BINS = 8
SEG_BIN_LABELS = ("1", "2-3", "4-7", "8-15", "16-31", "32-63", "64-127", "128+")

# The value histogram, standing in for today's per-score histograms, so the
# prototype can show it is untouched by any of this.  Four bins over [0, 1).
VALUE_BINS = 4


def seg_bin(length: int) -> int:
    """Index of the segment-length bin holding ``length`` (>= 1)."""
    return min(length.bit_length() - 1, SEG_BINS - 1)


def value_bin(value: float) -> int | None:
    """Index of the value bin, or None when out of the [0, 1) range."""
    if value < 0.0 or value >= 1.0:
        return None
    return min(int(value * VALUE_BINS), VALUE_BINS - 1)


@dataclass(frozen=True, slots=True)
class Row:
    """One table row.  Positions are 1-based and inclusive, as gain's are."""

    chrom: str
    begin: int
    end: int
    value: float


class RefusedError(Exception):
    """What the scan's validators raise; the whole build fails with it."""


@dataclass(frozen=True, slots=True)
class RegionStats:
    """Statistics of one scanned region -- the monoid element.

    ``start``/``end`` are the scanned extent, inclusive; both None for an
    unbounded region (the ``--region-size 0`` path, and any contig whose
    length could not be determined).  An unbounded region has no edges for
    a run to touch, so nothing is left open in it.
    """

    chrom: str
    start: int | None
    end: int | None
    covered: int
    closed_count: int
    closed_hist: tuple[int, ...]
    head_len: int
    tail_len: int
    one_run: bool
    value_hist: tuple[int, ...]
    value_out_of_range: int

    @property
    def open_count(self) -> int:
        """Runs still open at an edge: 1 when head and tail are the same."""
        if self.one_run:
            return 1
        return (1 if self.head_len else 0) + (1 if self.tail_len else 0)


@dataclass(frozen=True, slots=True)
class FinalStats:
    """A contig's statistics once no further region can extend it."""

    chrom: str
    covered: int
    segments: int
    seg_hist: tuple[int, ...]
    value_hist: tuple[int, ...]
    value_out_of_range: int


def _empty_hist(bins: int) -> tuple[int, ...]:
    return (0,) * bins


def _bump(hist: tuple[int, ...], index: int, by: int = 1) -> tuple[int, ...]:
    out = list(hist)
    out[index] += by
    return tuple(out)


def _add_hists(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(x + y for x, y in zip(a, b, strict=True))


# ---------------------------------------------------------------------------
# Reading one region -- what a single scan task does
# ---------------------------------------------------------------------------


def fetch(
    rows: list[Row], chrom: str, start: int | None,
    end: int | None,  # noqa: ARG001  -- unused ON PURPOSE; see below
) -> list[Row]:
    """Rows a region read returns, in table order.

    Deliberately models the scan's actual skip rule and only that one: a
    row is dropped when it ends before the region starts.  A row BEGINNING
    past the region's end is NOT dropped here -- that is gain#636, and the
    clip below is where it shows up.
    """
    hit = [r for r in rows if r.chrom == chrom]
    if start is not None:
        hit = [r for r in hit if r.end >= start]
    return hit


def validate(rows: list[Row], kind: Kind, chrom: str) -> None:
    """Refuse a table this kind's records may not form, on RAW spans.

    Position: a row beginning where its predecessor has not yet ended
    claims a position already taken -- ``begin <= prev_end``, overlap and
    mere touching alike.  Fragment: only a backwards row is refused;
    overlapping and nested fragments are legal.
    """
    prev_end: int | None = None
    prev_begin: int | None = None
    for row in rows:
        if row.end < row.begin:
            raise RefusedError(
                f"{chrom}: row {row.begin}-{row.end} ends before it begins")
        if kind == "position":
            if prev_end is not None and row.begin <= prev_end:
                raise RefusedError(
                    f"{chrom}: row at {row.begin} overlaps or touches a row "
                    f"ending at {prev_end} -- a position score promises one "
                    f"value per position")
        elif prev_begin is not None and row.begin < prev_begin:
            raise RefusedError(
                f"{chrom}: row at {row.begin} is backwards after {prev_begin}")
        prev_end = row.end if prev_end is None else max(prev_end, row.end)
        prev_begin = row.begin


def scan_region(
    rows: list[Row], kind: Kind, chrom: str,
    start: int | None, end: int | None,
) -> RegionStats:
    """Scan one region: the per-chunk statistic, built in one pass.

    This is what rides the existing histogram scan -- it consumes exactly
    the clipped extents both scan paths already hold (the per-record path's
    ``left``/``right``, the bulk path's clipped ``pos_begin``/``pos_end``
    columns), so it costs no extra read.
    """
    fetched = fetch(rows, chrom, start, end)
    validate(fetched, kind, chrom)

    value_hist = _empty_hist(VALUE_BINS)
    value_out = 0
    runs: list[list[int]] = []

    for row in fetched:
        left = row.begin if start is None else max(row.begin, start)
        right = row.end if end is None else min(row.end, end)
        if right < left:
            # gain#636: a row beginning past the region's end clips to an
            # inverted span.  It contributes nothing -- neither coverage nor
            # a negative weight.
            continue

        weight = (right - left + 1) if kind == "position" else 1
        vbin = value_bin(row.value)
        if vbin is None:
            value_out += weight
        else:
            value_hist = _bump(value_hist, vbin, weight)

        # Union, streaming.  Rows arrive non-decreasing by begin (the
        # validators guarantee it), so a run extends while the next row
        # starts no later than one past the run's RUNNING MAXIMUM end --
        # the maximum matters for fragments, which may nest.
        if runs and left <= runs[-1][1] + 1:
            runs[-1][1] = max(runs[-1][1], right)
        else:
            runs.append([left, right])

    covered = sum(r[1] - r[0] + 1 for r in runs)

    head_len = 0
    tail_len = 0
    if runs:
        if start is not None and runs[0][0] == start:
            head_len = runs[0][1] - runs[0][0] + 1
        if end is not None and runs[-1][1] == end:
            tail_len = runs[-1][1] - runs[-1][0] + 1
    one_run = bool(runs) and len(runs) == 1 and head_len > 0 and tail_len > 0

    closed_count = 0
    closed_hist = _empty_hist(SEG_BINS)
    for index, (lo, hi) in enumerate(runs):
        if one_run:
            continue
        if index == 0 and head_len:
            continue
        if index == len(runs) - 1 and tail_len:
            continue
        closed_count += 1
        closed_hist = _bump(closed_hist, seg_bin(hi - lo + 1))

    return RegionStats(
        chrom=chrom, start=start, end=end,
        covered=covered,
        closed_count=closed_count, closed_hist=closed_hist,
        head_len=head_len, tail_len=tail_len, one_run=one_run,
        value_hist=value_hist, value_out_of_range=value_out,
    )


# ---------------------------------------------------------------------------
# Merging regions -- the order-sensitive bit
# ---------------------------------------------------------------------------


class OrderError(Exception):
    """``combine`` was handed two regions it cannot stitch."""


def combine(a: RegionStats, b: RegionStats) -> RegionStats:
    """Stitch region ``b`` onto region ``a``, which must precede it.

    Refuses anything but two extents of one contig that meet exactly:
    the stitch decides whether a run is one segment or two, and it can
    only decide that when it knows the regions are adjacent.  A gap, an
    overlap, a swap or a different contig is an error rather than a
    silently wrong count.
    """
    if a.chrom != b.chrom:
        raise OrderError(
            f"cannot stitch {a.chrom} onto {b.chrom} -- segments never span "
            f"contigs")
    if a.end is None or b.start is None:
        raise OrderError(
            f"{a.chrom}: an unbounded region is a whole contig and has no "
            f"neighbour to stitch")
    if b.start != a.end + 1:
        raise OrderError(
            f"{a.chrom}: regions [{a.start}-{a.end}] and "
            f"[{b.start}-{b.end}] are not adjacent, in this order")

    join = a.tail_len > 0 and b.head_len > 0
    closed_count = a.closed_count + b.closed_count
    closed_hist = _add_hists(a.closed_hist, b.closed_hist)

    if join:
        # The two open runs are one run.  It is closed unless it still
        # reaches an outer edge -- which it does exactly when the region it
        # came from was covered end to end.
        seam = a.tail_len + b.head_len
        if not a.one_run and not b.one_run:
            closed_count += 1
            closed_hist = _bump(closed_hist, seg_bin(seam))
    else:
        # No stitch: an open run that faces the seam is now walled in by
        # uncovered ground, so it closes -- unless it also reaches the far
        # outer edge (``one_run``), where it stays open.
        if a.tail_len and not a.one_run:
            closed_count += 1
            closed_hist = _bump(closed_hist, seg_bin(a.tail_len))
        if b.head_len and not b.one_run:
            closed_count += 1
            closed_hist = _bump(closed_hist, seg_bin(b.head_len))

    if a.head_len == 0:
        head_len = 0
    elif a.one_run and join:
        head_len = a.head_len + b.head_len
    else:
        head_len = a.head_len

    if b.tail_len == 0:
        tail_len = 0
    elif b.one_run and join:
        tail_len = a.tail_len + b.tail_len
    else:
        tail_len = b.tail_len

    return RegionStats(
        chrom=a.chrom, start=a.start, end=b.end,
        covered=a.covered + b.covered,
        closed_count=closed_count, closed_hist=closed_hist,
        head_len=head_len, tail_len=tail_len,
        one_run=a.one_run and b.one_run and join,
        value_hist=_add_hists(a.value_hist, b.value_hist),
        value_out_of_range=a.value_out_of_range + b.value_out_of_range,
    )


def finalize(stats: RegionStats) -> FinalStats:
    """Close what is still open: no further region can extend this contig."""
    segments = stats.closed_count
    seg_hist = stats.closed_hist
    if stats.one_run:
        segments += 1
        seg_hist = _bump(seg_hist, seg_bin(stats.head_len))
    else:
        if stats.head_len:
            segments += 1
            seg_hist = _bump(seg_hist, seg_bin(stats.head_len))
        if stats.tail_len:
            segments += 1
            seg_hist = _bump(seg_hist, seg_bin(stats.tail_len))
    return FinalStats(
        chrom=stats.chrom, covered=stats.covered, segments=segments,
        seg_hist=seg_hist, value_hist=stats.value_hist,
        value_out_of_range=stats.value_out_of_range,
    )


# ---------------------------------------------------------------------------
# Driving a whole scan
# ---------------------------------------------------------------------------


def split_regions(
    chrom: str, chrom_len: int, region_size: int,
) -> list[tuple[str, int | None, int | None]]:
    """Chunk a contig the way the statistics build chunks it."""
    if region_size <= 0:
        return [(chrom, None, None)]
    regions = []
    start = 1
    while start <= chrom_len:
        stop = min(start + region_size - 1, chrom_len)
        regions.append((chrom, start, stop))
        start = stop + 1
    return regions or [(chrom, 1, max(chrom_len, 1))]


def fold(
    regions: list[RegionStats], grouping: str = "left",
) -> tuple[dict[str, RegionStats], list[str]]:
    """Fold per-region statistics into one statistic per contig.

    ``grouping`` picks HOW the regions are folded, which is the whole
    point of asking whether the merge is associative:

    - ``left``     -- sequential, the shape the task graph's varargs merge
                      has today
    - ``pairwise`` -- a balanced binary tree, the shape a distributed
                      merge is free to take
    - ``reverse``  -- regions handed over back to front

    Returns the per-contig statistics and any refusals ``combine`` raised.
    """
    by_chrom: dict[str, list[RegionStats]] = {}
    for region in regions:
        by_chrom.setdefault(region.chrom, []).append(region)

    merged: dict[str, RegionStats] = {}
    problems: list[str] = []
    for chrom, chunks in by_chrom.items():
        order = list(reversed(chunks)) if grouping == "reverse" else chunks
        try:
            if grouping == "pairwise":
                merged[chrom] = _fold_pairwise(order)
            else:
                acc = order[0]
                for nxt in order[1:]:
                    acc = combine(acc, nxt)
                merged[chrom] = acc
        except OrderError as err:
            problems.append(str(err))
            merged[chrom] = chunks[0]
    return merged, problems


def _fold_pairwise(chunks: list[RegionStats]) -> RegionStats:
    level = list(chunks)
    while len(level) > 1:
        nxt = [
            combine(level[i], level[i + 1]) if i + 1 < len(level) else level[i]
            for i in range(0, len(level), 2)
        ]
        level = nxt
    return level[0]


def run_scan(
    rows: list[Row], kind: Kind, region_size: int, grouping: str = "left",
) -> tuple[
    list[RegionStats], dict[str, FinalStats], list[str], str | None,
]:
    """Scan every contig chunked, then fold.  The thing under test."""
    chroms = sorted({row.chrom for row in rows})
    regions: list[RegionStats] = []
    try:
        for chrom in chroms:
            chrom_len = max(r.end for r in rows if r.chrom == chrom)
            for _c, start, end in split_regions(chrom, chrom_len, region_size):
                regions.append(scan_region(rows, kind, chrom, start, end))
    except RefusedError as err:
        return [], {}, [], str(err)

    merged, problems = fold(regions, grouping)
    return regions, {c: finalize(s) for c, s in merged.items()}, problems, None


def run_oracle(
    rows: list[Row], kind: Kind,
) -> tuple[dict[str, FinalStats], str | None]:
    """The answer a single unchunked pass gives -- what chunking must match."""
    chroms = sorted({row.chrom for row in rows})
    out: dict[str, FinalStats] = {}
    try:
        for chrom in chroms:
            out[chrom] = finalize(
                scan_region(rows, kind, chrom, None, None))
    except RefusedError as err:
        return {}, str(err)
    return out, None


def totals(per_chrom: dict[str, FinalStats]) -> FinalStats:
    """The global roll-up: exact, because the bins are fixed."""
    covered = sum(s.covered for s in per_chrom.values())
    segments = sum(s.segments for s in per_chrom.values())
    seg_hist = _empty_hist(SEG_BINS)
    value_hist = _empty_hist(VALUE_BINS)
    out_of_range = 0
    for stats in per_chrom.values():
        seg_hist = _add_hists(seg_hist, stats.seg_hist)
        value_hist = _add_hists(value_hist, stats.value_hist)
        out_of_range += stats.value_out_of_range
    return FinalStats(
        chrom="*", covered=covered, segments=segments, seg_hist=seg_hist,
        value_hist=value_hist, value_out_of_range=out_of_range,
    )


def disagreements(got: FinalStats, want: FinalStats) -> list[str]:
    """Which fields of a chunked scan disagree with the unchunked one."""
    out = []
    if got.covered != want.covered:
        out.append(f"covered {got.covered} != {want.covered}")
    if got.segments != want.segments:
        out.append(f"segments {got.segments} != {want.segments}")
    if got.seg_hist != want.seg_hist:
        out.append(f"segment lengths {got.seg_hist} != {want.seg_hist}")
    if got.value_hist != want.value_hist:
        out.append(f"value histogram {got.value_hist} != {want.value_hist}")
    if got.value_out_of_range != want.value_out_of_range:
        out.append(
            f"out-of-range {got.value_out_of_range} "
            f"!= {want.value_out_of_range}")
    return out


def sweep_region_sizes(
    rows: list[Row], kind: Kind, grouping: str, limit: int = 40,
) -> list[tuple[int, list[str]]]:
    """Every region size from 1 to ``limit``, and where each disagrees.

    The chunk-invariance question asked exhaustively rather than one
    region size at a time -- a single hand-picked size is exactly how a
    stitching bug survives.
    """
    oracle, refused = run_oracle(rows, kind)
    if refused is not None:
        return []
    want = totals(oracle)
    out = []
    for size in range(1, limit + 1):
        _regions, got_per_chrom, problems, err = run_scan(
            rows, kind, size, grouping)
        if err is not None:
            out.append((size, [f"refused: {err}"]))
            continue
        bad = disagreements(totals(got_per_chrom), want)
        out.append((size, bad + problems))
    return out


def with_value(row: Row, value: float) -> Row:
    """Convenience for the shell; keeps ``Row`` frozen."""
    return replace(row, value=value)
