# pylint: disable=C0114,C0116,W0621
import io

import pytest
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    plot_length_histogram,
)

# Every histogram that reaches the plotter must draw without complaint.
# A logarithmic axis warns "Data has no positive values" and degenerates
# on an all-zero set, so the callers refuse to plot one -- and these
# tests fail rather than pass quietly if that ever stops holding.
pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@pytest.fixture
def drawn(monkeypatch):
    """The Axes ``plot_length_histogram`` actually drew on.

    The chart itself is the behaviour under test -- which scale, which
    bars, which labels -- and a PNG cannot be asked.  So the figure the
    plotter builds is captured on its way out of matplotlib and read
    back afterwards; closing a figure does not discard its artists.
    """
    import matplotlib
    matplotlib.use("agg")
    import matplotlib.pyplot as plt

    captured = []
    subplots = plt.subplots

    def capturing_subplots(*args, **kwargs):
        figure, axes = subplots(*args, **kwargs)
        captured.append(axes)
        return figure, axes

    monkeypatch.setattr(plt, "subplots", capturing_subplots)
    return captured


def a_histogram(**counts: int) -> list[int]:
    """A length histogram on the fixed ladder, by bin index."""
    histogram = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for index, count in counts.items():
        histogram[int(index.removeprefix("bin"))] = count
    return histogram


def test_the_counts_axis_is_logarithmic(drawn: list) -> None:
    plot_length_histogram(
        io.BytesIO(), a_histogram(bin0=1000, bin3=10), "segment")

    assert drawn[0].get_yscale() == "log"


def test_a_bin_counted_once_still_draws_a_visible_bar(drawn: list) -> None:
    # The instinctive bottom=1 on a counts axis would flatten this bar
    # to zero height and silently drop the bin off the chart.  This
    # pins that one mistake only: symlog also leaves room below one, so
    # the scale itself is pinned by the yscale test above, not here.
    plot_length_histogram(io.BytesIO(), a_histogram(bin4=1), "segment")

    assert drawn[0].get_ylim()[0] < 1


def test_bins_at_the_display_cap_fold_into_one_bar(drawn: list) -> None:
    # The fold is display-only: the counts above the cap are summed
    # into the final bar rather than dropped, so the chart still totals
    # what the statistics file carries.
    histogram = a_histogram(bin13=3, bin20=5, bin31=7)

    plot_length_histogram(io.BytesIO(), histogram, "segment")

    heights = [patch.get_height() for patch in drawn[0].patches]
    assert len(heights) == 14
    assert heights[-1] == sum(histogram[13:])


def test_every_bar_carries_its_own_label_at_the_default_cap(
    drawn: list,
) -> None:
    # Fourteen labels read cleanly across the chart width.  Every fourth
    # would put ticks at bins 12 and 13 side by side, colliding "4K"
    # with the overflow bar's own label.
    plot_length_histogram(io.BytesIO(), a_histogram(bin0=1), "segment")

    labels = [text.get_text() for text in drawn[0].get_xticklabels()]
    assert labels == [
        "1", "2", "4", "8", "16", "32", "64", "128", "256", "512",
        "1K", "2K", "4K", "≥8K",
    ]


def test_a_raised_cap_widens_the_axis_and_thins_the_labels(
    drawn: list,
) -> None:
    # A resource kind whose lengths genuinely run longer raises its own
    # axis; past sixteen bars labelling every one would crowd, so the
    # every-fourth rule takes over.
    plot_length_histogram(
        io.BytesIO(), a_histogram(bin0=1, bin25=4), "fragment",
        display_cap=2 ** 20)

    assert len(drawn[0].patches) == 21
    labels = [text.get_text() for text in drawn[0].get_xticklabels()]
    assert labels == ["1", "16", "256", "4K", "64K", "≥1M"]


def test_a_raised_cap_never_crowds_the_last_two_labels(
    drawn: list,
) -> None:
    # The last tick is pinned to the overflow bar, so an every-fourth
    # tick can land right beside it -- at any cap whose top bin is one
    # past a multiple of four.  That is the same collision the
    # every-bin rule exists to avoid at the default cap.
    plot_length_histogram(
        io.BytesIO(), a_histogram(bin0=1), "segment", display_cap=2 ** 17)

    ticks = list(drawn[0].get_xticks())
    assert ticks[-1] - ticks[-2] > 1
