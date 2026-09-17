# pylint: disable=C0114,C0116
import io

import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.statistics.chart_style import CHART_FIGSIZE


@pytest.mark.parametrize("hist", [
    NumberHistogram(NumberHistogramConfig((0, 10), number_of_bins=5)),
    CategoricalHistogram(CategoricalHistogramConfig(value_order=["a", "b"])),
], ids=["number", "categorical"])
def test_a_score_histogram_is_drawn_at_the_size_the_charts_share(
    hist: NumberHistogram | CategoricalHistogram, drawn: list,
) -> None:
    # The per-score histograms set the size every other statistics
    # chart matches on the info page, so they draw at the one named in
    # chart_style, not a tuple of their own.
    hist.plot(io.BytesIO(), "score")

    assert tuple(drawn[0].figure.get_size_inches()) == CHART_FIGSIZE
