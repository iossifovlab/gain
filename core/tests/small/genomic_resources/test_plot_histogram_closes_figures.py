# pylint: disable=C0114,C0116
import textwrap
from collections.abc import Iterator

import matplotlib.pyplot as plt
import pytest
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    plot_histogram,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import build_inmemory_test_resource

LEAKING_PLOT = textwrap.dedent("""
    import matplotlib.pyplot as plt

    def plot(outfile, hist, score_id, small_desc, large_desc):
        plt.figure()
        plt.figure()
        outfile.write(b"not a png")
""")


@pytest.fixture(autouse=True)
def _close_figures_the_test_opened() -> Iterator[None]:
    before = set(plt.get_fignums())
    yield
    for num in set(plt.get_fignums()) - before:
        plt.close(num)


def _resource(plot_source: str) -> GenomicResource:
    return build_inmemory_test_resource({
        "genomic_resource.yaml": "type: position_score\n",
        "plot.py": plot_source,
    })


def _hist() -> CategoricalHistogram:
    return CategoricalHistogram(
        CategoricalHistogramConfig(plot_function="plot.py:plot"))


def test_plot_histogram_closes_the_figures_a_plot_function_leaves_open(
) -> None:
    res = _resource(LEAKING_PLOT)
    before = plt.get_fignums()

    plot_histogram(res, "score.png", _hist(), "score")

    assert plt.get_fignums() == before


RAISING_PLOT = textwrap.dedent("""
    import matplotlib.pyplot as plt

    class PlotFailedError(Exception):
        pass

    def plot(outfile, hist, score_id, small_desc, large_desc):
        plt.figure()
        raise PlotFailedError("the plot function failed")
""")


def test_plot_histogram_closes_the_figures_when_the_plot_function_raises(
) -> None:
    res = _resource(RAISING_PLOT)
    before = plt.get_fignums()

    with pytest.raises(Exception, match="the plot function failed") as err:
        plot_histogram(res, "score.png", _hist(), "score")

    assert type(err.value).__name__ == "PlotFailedError"
    assert plt.get_fignums() == before


def test_plot_histogram_keeps_a_figure_that_was_open_before_the_call(
) -> None:
    res = _resource(LEAKING_PLOT)
    earlier = plt.figure().number

    plot_histogram(res, "score.png", _hist(), "score")

    assert earlier in plt.get_fignums()
