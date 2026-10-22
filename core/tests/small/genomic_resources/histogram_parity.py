"""The one statement of when two number histograms are the same.

Three suites compare a histogram built one way against one built another --
``add_batch`` against an ``add_value`` loop, the bulk scan against the
per-record scan -- and each used to carry its own copy of the comparison,
extended by hand.  This is the copy they share.
"""
import numpy as np
from gain.genomic_resources.histogram import NumberHistogram


def assert_histograms_equal(
    got: NumberHistogram, want: NumberHistogram, label: object = None,
) -> None:
    """Bars, out-of-range counts, min/max and the moments agree.

    Everything must match exactly.  The bars and counts are integer
    arithmetic.  The two sums are the same per-term products, which
    ``Moments`` adds exactly and rounds once, so the order of the
    additions does not change them by a bit.
    """
    assert np.array_equal(got.bars, want.bars), (label, got.bars, want.bars)
    assert got.out_of_range_bins == want.out_of_range_bins, label
    assert np.array_equal(
        [got.min_value], [want.min_value], equal_nan=True), label
    assert np.array_equal(
        [got.max_value], [want.max_value], equal_nan=True), label

    assert got.moments is not None, label
    assert want.moments is not None, label
    # Each arm's own invariant, then the two against each other.
    for hist in (got, want):
        assert hist.count == \
            hist.bars.sum() + sum(hist.out_of_range_bins), label
    assert got.moments.count == want.moments.count, label
    for name in ("sum", "sum_of_squares"):
        theirs = getattr(got.moments, name)
        ours = getattr(want.moments, name)
        assert theirs == ours, (label, name, theirs, ours)
