"""The one fixed length ladder every length histogram is binned on.

ADR 0020 gives **segments**, **fragments** and **indels** one binning, so
that per-chromosome results merge into exact global ones and chunked
scans merge exactly for the same reason.  Three statistics share it --
two in :mod:`gain.genomic_resources.statistics.coverage` and one in
:mod:`gain.genomic_resources.statistics.alleles` -- which is why the
ladder lives in neither of them: it is the shared contract, not a
coverage detail its sibling happens to import.

What the ladder does NOT bin is the complex allele grid: its cells are
exact lengths (ADR 0020 as amended by gain#779), for reasons that
belong with that grid rather than here.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import IO

import numpy as np

# Fixed log2 bins: bin ``i`` holds lengths in ``[2**i, 2**(i + 1))``, and
# the last bin is open-ended.  The edges are part of the stored format --
# histograms binned on different edges cannot be merged -- so this
# constant must not change once resources carry statistics built from it.
LENGTH_HISTOGRAM_BIN_COUNT = 32


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


def _bin_edge_label(edge: int) -> str:
    for unit, factor in (("G", 2 ** 30), ("M", 2 ** 20), ("K", 2 ** 10)):
        if edge >= factor:
            return f"{edge // factor}{unit}"
    return str(edge)


def plot_length_histogram(
    outfile: IO,
    histogram: list[int],
    item: str,
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
    """
    # pylint: disable=import-outside-toplevel
    import matplotlib
    matplotlib.use("agg")
    import matplotlib.pyplot as plt

    from gain.genomic_resources.histogram import (
        HISTOGRAM_LABELS_FONT_SIZE,
    )

    figure, axes = plt.subplots(figsize=(15, 10))
    axes.bar(
        range(len(histogram)), histogram, width=0.9, align="edge")
    # Ticks at every fourth bin's lower edge; the last bin is
    # open-ended, so its lower edge is labeled as a floor.
    last = len(histogram) - 1
    ticks = [*range(0, last, 4), last]
    labels = [_bin_edge_label(2 ** tick) for tick in ticks[:-1]]
    labels.append(f"≥{_bin_edge_label(2 ** last)}")
    axes.set_xticks(ticks)
    axes.set_xticklabels(labels, fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.tick_params(axis="y", labelsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_xlabel(
        f"{item} length (bp)", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    axes.set_ylabel(f"{item}s", fontsize=HISTOGRAM_LABELS_FONT_SIZE)
    # Counts span orders of magnitude on genome-scale scores; symlog
    # keeps the small bars visible while zero stays on the axis.
    axes.set_yscale("symlog")
    figure.tight_layout()
    figure.savefig(outfile, format="png")
    plt.close(figure)
