"""The one fixed length ladder, and the chart drawn on it.

ADR 0020 gives **segments** and **fragments** this binning as their
STORED form, so that per-chromosome results merge into exact global ones
and chunked scans merge exactly for the same reason.  Both live in
:mod:`gain.genomic_resources.statistics.coverage`, which is why the
ladder lives in neither: it is the shared contract, not a coverage
detail its sibling happens to import.

**Indels** left the stored ladder in gain#1118 (ADR 0020 as amended).
They keep an exact ``{length: count}`` map in
:mod:`gain.genomic_resources.statistics.indel_lengths` and merge on
that; what they still use from here is the RENDERING -- the map is
projected onto these bins at draw time so the indel chart keeps the
shape the stored histograms drew.  So the two callers now use this
module for different things, and only the coverage pair depends on the
edges being part of any file.

What the ladder does NOT bin at all is the complex allele grid: its
cells are exact lengths (ADR 0020 as amended by gain#779), for reasons
that belong with that grid rather than here.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import IO, Any, TypeGuard

import numpy as np

# Fixed log2 bins: bin ``i`` holds lengths in ``[2**i, 2**(i + 1))``, and
# the last bin is open-ended.  The edges are part of the stored format --
# histograms binned on different edges cannot be merged -- so this
# constant must not change once resources carry statistics built from it.
LENGTH_HISTOGRAM_BIN_COUNT = 32

# Where the length axis stops on the chart.  Real data dies long before
# the ladder's open-ended top bin -- on a real allele score insertions
# die at ~1K and deletions at ~512, and segment lengths have the same
# shape -- so a full-ladder axis draws three quarters of nothing.  Bins
# at or above this length are summed into one overflow bar, whose height
# is itself the signal that something runs past the cap.  Display only:
# the stored histogram keeps all 32 bins, so the exact tail remains
# readable in the statistics file.
LENGTH_HISTOGRAM_DISPLAY_CAP = 2 ** 13


def length_histogram_bin_index(length: int) -> int:
    """The fixed log2 bin a length of that many base pairs falls in."""
    if length < 1:
        raise ValueError(f"length must be positive: {length}")
    return min(length.bit_length() - 1, LENGTH_HISTOGRAM_BIN_COUNT - 1)


# The same ladder as an array of lower edges, for vectorized binning over
# a batch of lengths.  Pinned equal to ``length_histogram_bin_index`` by
# test_the_batch_binning_agrees_with_the_per_length_one.
LENGTH_BIN_EDGES = 2 ** np.arange(LENGTH_HISTOGRAM_BIN_COUNT, dtype=np.int64)


def accumulate_bins(target: list[int], source: Iterable[int]) -> None:
    """Add one length histogram into another, bin for bin.

    The one statement of "counts are added, not replaced", for every
    place two histograms on the fixed ladder come together: a merge of
    two regions, a batch of fresh counts, a global roll-up.
    """
    for index, count in enumerate(source):
        target[index] += count


def binwise_sum(histograms: Iterable[Iterable[int]]) -> list[int]:
    """Merge any number of histograms on this ladder into one.

    Exact rather than approximate, and that is the point of the fixed
    edges: every histogram is binned the same way, so a global roll-up
    is a merge and never a re-scan.
    """
    merged = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for histogram in histograms:
        accumulate_bins(merged, histogram)
    return merged


def histogram_on_this_ladder(counts: Iterable[Any] | None) -> list[int] | None:
    """A stored histogram, or ``None`` when it is not one of ours.

    ``None`` in means the key was absent; ``None`` out additionally
    covers a histogram of the wrong length, which was binned on foreign
    edges and so cannot merge with these fixed bins.  Either way the
    reader learns *unknown*, which is a different answer from an
    all-zero histogram -- that one says the thing was measured and found
    empty.  Stated once here because both stored statistics apply it.
    """
    if counts is None:
        return None
    histogram = [int(count) for count in counts]
    if len(histogram) != LENGTH_HISTOGRAM_BIN_COUNT:
        return None
    return histogram


def _bin_edge_label(edge: int) -> str:
    for unit, factor in (("G", 2 ** 30), ("M", 2 ** 20), ("K", 2 ** 10)):
        if edge >= factor:
            return f"{edge // factor}{unit}"
    return str(edge)


def has_counts_to_plot(
    histogram: list[int] | None,
) -> TypeGuard[list[int]]:
    """Whether a length histogram has a positive count to draw.

    Unknown and known-and-empty are one answer here: the counts axis is
    logarithmic and can render neither, and a chart of nothing under a
    "Segment lengths" heading states nothing either.

    Coverage's two groups -- segments and fragments -- are the callers.
    The indel groups asked this too until gain#1118 took them off the
    stored ladder: they carry an exact length map now, so the same
    question is ``lengths is None or not lengths.alleles``, read off the
    thing they actually store rather than off bins derived from it.
    """
    return histogram is not None and any(histogram)


def plot_length_histogram(
    outfile: IO,
    histogram: list[int],
    item: str,
    display_cap: int = LENGTH_HISTOGRAM_DISPLAY_CAP,
) -> None:
    """Render a length histogram on the fixed log2 bins as PNG.

    Styled to sit beside the per-score value histograms on the resource
    info page: same figure size and label font as
    :mod:`gain.genomic_resources.histogram` renders.  ``item`` names
    what was measured -- segments, fragments, insertions, deletions --
    and appears in both axis labels; the bins are the same ladder every
    time, which is what lets one renderer serve them all.  Required,
    with no default: a fragment histogram silently labelled "segment" is
    the one mistake this parameter exists to prevent.

    ``display_cap`` is the length the drawn axis stops at: every bin at
    or above it becomes one overflow bar.  A parameter rather than a
    constant so a resource kind whose lengths genuinely run longer can
    raise its own axis without anything touching the stored format,
    which the fold never reads back.  It is snapped down to its own bin
    on the ladder, so a cap between two edges caps at the lower one --
    pass a power of two to get the axis the number reads as.
    """
    # pylint: disable=import-outside-toplevel
    import matplotlib
    matplotlib.use("agg")
    import matplotlib.pyplot as plt

    from gain.genomic_resources.histogram import (
        HISTOGRAM_LABELS_FONT_SIZE,
    )

    # The bins from the cap up are drawn as one bar; the counts move,
    # never vanish, so the bars still total the histogram.
    top = length_histogram_bin_index(display_cap)
    bars = [*histogram[:top], sum(histogram[top:])]

    figure, axes = plt.subplots(figsize=(15, 10))
    axes.bar(range(len(bars)), bars, width=0.9, align="edge")
    # Ticks at each bar's lower edge, the last one -- open-ended,
    # whether by the ladder or by the cap -- labeled as a floor.  Up to
    # sixteen bars every bar is labeled, which reads cleanly across the
    # chart and needs no collision rule; a caller that raises the cap
    # past that falls back to every fourth.
    last = len(bars) - 1
    ticks = (
        list(range(len(bars))) if len(bars) <= 16
        # The last tick is pinned to the open-ended bar, so an
        # every-fourth tick landing right beside it is dropped rather
        # than drawn into its label.
        else [*(t for t in range(0, last, 4) if last - t > 1), last]
    )
    labels = [_bin_edge_label(2 ** tick) for tick in ticks[:-1]]
    labels.append(f"≥{_bin_edge_label(2 ** last)}")
    axes.set_xticks(ticks)
    axes.set_xticklabels(labels, fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.tick_params(axis="y", labelsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_xlabel(
        f"{item} length (bp)", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_ylabel(f"{item}s", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    # Counts span orders of magnitude on genome-scale scores, so the
    # axis is logarithmic.  Plain log, not symlog: nothing on a counts
    # axis lies between zero and one, and symlog spends a whole decade
    # of chart height there.  An empty bin is simply absent, which is
    # what it already looked like.  No ylim is pinned -- autoscale
    # leaves room below one, so a count-1 bin still draws a bar, while
    # bottom=1 would flatten it to nothing.
    axes.set_yscale("log")
    figure.tight_layout()
    figure.savefig(outfile, format="png")
    plt.close(figure)
