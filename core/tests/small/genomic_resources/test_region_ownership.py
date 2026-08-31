# pylint: disable=C0114,C0116,W0212,W0621
import pathlib

import pytest
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_fragment_score

_HIST_DICT: dict = {
    "type": "number",
    "view_range": {"min": 0, "max": 1},
    "number_of_bins": 10,
}


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        **_HIST_DICT,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _one_fragment(tmp_path: pathlib.Path) -> GenomicResource:
    """The gain#816 oracle fixture: a single fragment at chr1:8-14."""
    return (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   8          14       0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _histogram_total(
    resource: GenomicResource,
    regions: list[tuple[int | None, int | None]],
) -> float:
    confs: dict = {"s": _hist_conf()}
    results = [
        scan.do_histogram_task(
            resource, confs, "chr1", start, end)
        for start, end in regions
    ]
    merged = scan.merge_histograms(
        resource, *(result.histograms for result in results))
    return merged["s"].bars.sum()


@pytest.mark.parametrize(
    ("label", "regions"),
    [
        ("unbounded", [(None, None)]),
        ("one region", [(1, 20)]),
        ("split at 10", [(1, 10), (11, 20)]),
        ("2bp regions", [(start, start + 1) for start in range(1, 20, 2)]),
    ],
)
def test_a_fragment_is_counted_once_however_the_contig_is_split(
    tmp_path: pathlib.Path,
    label: str,
    regions: list[tuple[int | None, int | None]],
) -> None:
    # gain#816's documented oracle: the one fragment at 8-14 was counted
    # 1 / 2 / 4 times respectively, because every region that FETCHED it
    # counted it.  A region owns the records whose pos_begin falls in it.
    assert _histogram_total(_one_fragment(tmp_path), regions) == 1, label
