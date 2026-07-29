# pylint: disable=C0114,C0116,W0212,W0621
import pathlib
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation as G,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import FRAGMENT_SCORE_TYPES
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_np_score,
    a_vcf_info_score,
    an_allele_score,
)


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _assert_hists_equal(
    bulk: dict[str, NumberHistogram],
    ref: dict[str, NumberHistogram],
) -> None:
    assert set(bulk) == set(ref)
    for score_id in ref:
        got, want = bulk[score_id], ref[score_id]
        assert np.array_equal(got.bars, want.bars), \
            (score_id, got.bars, want.bars)
        assert got.out_of_range_bins == want.out_of_range_bins, score_id
        assert np.array_equal(
            [got.min_value], [want.min_value], equal_nan=True), score_id
        assert np.array_equal(
            [got.max_value], [want.max_value], equal_nan=True), score_id


def _allele_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score with THREE records sharing position 10."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            0.2
            chr1   10         A          T            0.9
            chr1   14         C          T            0.3
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_allele_score_is_bulk_scan_eligible(tmp_path: pathlib.Path) -> None:
    assert G._bulk_scan_eligible(_allele_tabix(tmp_path), ["s"])


def test_bulk_histogram_matches_per_record_allele_shared_position(
    tmp_path: pathlib.Path,
) -> None:
    # Three records sit at position 10 (distinct ref/alt) -- which is what an
    # allele score IS, and what the position-score overlap guard rejects.
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 20)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 20)
    _assert_hists_equal(bulk, ref)
    # Every record weighs 1, so the bars hold one count per record.
    assert bulk["s"].bars.sum() == 4


def _assert_min_max_equal(bulk: dict, ref: dict) -> None:
    assert set(bulk) == set(ref)
    for sid in ref:
        got, want = bulk[sid], ref[sid]
        assert np.array_equal([got.min], [want.min], equal_nan=True), \
            (sid, got.min, want.min)
        assert np.array_equal([got.max], [want.max], equal_nan=True), \
            (sid, got.max, want.max)
        assert got.count == want.count, (sid, got.count, want.count)


def test_bulk_min_max_matches_per_record_allele_shared_position(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 20)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 20)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)


def test_bulk_allele_matches_per_record_when_region_clips_the_edges(
    tmp_path: pathlib.Path,
) -> None:
    # 10..14 clips the shared-position site at its left edge and the last
    # record at its right, exercising the keep mask alongside the weight rule.
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 10, 14),
        G._do_histogram(resource, confs, "chr1", 10, 14))
    _assert_min_max_equal(
        G._do_min_max_bulk(resource, ["s"], "chr1", 10, 14),
        G._do_min_max(resource, ["s"], "chr1", 10, 14))


def _allele_multibase_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score whose first record spans TEN bases via ``pos_end``."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  reference   alternative  s
            chr1   10         19       AGGGGGGGGG  A            0.1
            chr1   10         10       A           C            0.9
            chr1   30         30       C           T            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_multi_base_allele_record_weighs_one_not_its_span(
    tmp_path: pathlib.Path,
) -> None:
    # ``AlleleScore.fetch_region_values`` yields ``(pos, pos, values)``, so a
    # record weighs 1 however far its ``pos_end`` reaches.  Span-weighting it
    # in bulk would silently produce a different histogram.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 40)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 40)
    _assert_hists_equal(bulk, ref)
    # Three records, one count each -- not 10 + 1 + 1 for the spans.
    assert bulk["s"].bars.sum() == 3


def test_multi_base_allele_record_clipped_by_region_weighs_one(
    tmp_path: pathlib.Path,
) -> None:
    # Region 15..25 keeps only the ten-base record, clipped to five bases: the
    # clipped span (5), the full span (10) and the correct weight (1) are three
    # different numbers, so a wrong weight cannot hide.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 15, 25)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 15, 25)
    _assert_hists_equal(bulk, ref)
    assert bulk["s"].bars.sum() == 1


def _fragment_tabix(
    tmp_path: pathlib.Path, resource_type: str = "cnv_collection",
) -> GenomicResource:
    """A fragment score with overlapping spans of DIFFERING lengths."""
    return (
        a_fragment_score()
        .with_resource_type(resource_type)
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         30       0.9
            chr1   25         200      0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_fragment_score_is_bulk_scan_eligible_in_both_spellings(
    tmp_path: pathlib.Path, resource_type: str,
) -> None:
    # ``fragment_score`` and ``cnv_collection`` are both permanent spellings of
    # one kind (gain#471).  A gate that named only one would send the other
    # back to the per-record path silently -- no error, no failing test.
    resource = _fragment_tabix(tmp_path, resource_type)
    assert resource.get_type() == resource_type
    assert G._bulk_scan_eligible(resource, ["s"])


@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_bulk_histogram_matches_per_record_fragment(
    tmp_path: pathlib.Path, resource_type: str,
) -> None:
    resource = _fragment_tabix(tmp_path, resource_type)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 300)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 300)
    _assert_hists_equal(bulk, ref)
    # Three fragments, one count each; their spans (91, 11, 176) stay out.
    assert bulk["s"].bars.sum() == 3


def test_bulk_min_max_matches_per_record_fragment_including_count(
    tmp_path: pathlib.Path,
) -> None:
    # A fragment score's min/max ALSO reports how many records it saw, and
    # that count reaches the serialized statistic.
    resource = _fragment_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 300)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 300)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)
    assert bulk["s"].count == 3


def test_bulk_fragment_matches_per_record_when_region_clips_the_edges(
    tmp_path: pathlib.Path,
) -> None:
    resource = _fragment_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 26, 150),
        G._do_histogram(resource, confs, "chr1", 26, 150))
    _assert_min_max_equal(
        G._do_min_max_bulk(resource, ["s"], "chr1", 26, 150),
        G._do_min_max(resource, ["s"], "chr1", 26, 150))


def test_vcf_backed_allele_score_is_not_bulk_scan_eligible(
    tmp_path: pathlib.Path,
) -> None:
    # An ``allele_score`` is now an eligible KIND, so what keeps a VCF-backed
    # one on the per-record path is its table: a VCF record's payload is not a
    # raw row, so the backend serves no column arrays.
    resource = a_vcf_info_score().build_resource(tmp_path)
    assert resource.get_type() == "allele_score"
    assert not G._bulk_scan_eligible(resource, ["score"])


def test_np_score_is_not_bulk_scan_eligible(tmp_path: pathlib.Path) -> None:
    # Deliberately left out: no production GRR has an np_score, so the bulk
    # path is not exercised against one.
    resource = (
        a_np_score().with_score("score", "float").with_tabix()
        .build_resource(tmp_path)
    )
    assert not G._bulk_scan_eligible(resource, ["score"])


def test_categorical_histogram_keeps_the_per_record_path(
    tmp_path: pathlib.Path,
) -> None:
    resource = _allele_tabix(tmp_path)
    confs: dict = {"s": CategoricalHistogramConfig.default_config()}
    assert not G._can_bulk_histogram(resource, confs)


def _allele_three_scores_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """An allele score carrying a float, a string and a second float."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_score("other", "str")
        .with_score("third", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s    other  third
            chr1   10         A          G            0.1  aaa    0.4
            chr1   14         C          T            0.3  bbb    0.6
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def test_a_categorical_score_disqualifies_the_whole_resource(
    tmp_path: pathlib.Path,
) -> None:
    # One categorical score is enough: the histogram build is dispatched per
    # resource, not per score, and a null config is merely skipped.
    #
    # All three ids exist on the resource ON PURPOSE.  ``_can_bulk_histogram``
    # short-circuits on the categorical config before it ever opens the score,
    # so ids that name nothing would make this pass without the resource
    # having any say -- and it would keep passing if the short-circuit moved.
    resource = _allele_three_scores_tabix(tmp_path)
    score = build_score_from_resource(resource)
    assert set(score.get_all_scores()) == {"s", "other", "third"}
    confs: dict = {
        "s": _hist_conf(),
        "other": CategoricalHistogramConfig.default_config(),
        "third": NullHistogramConfig("no reason"),
    }
    assert not G._can_bulk_histogram(resource, confs)


def test_the_float_scores_alone_would_have_been_eligible(
    tmp_path: pathlib.Path,
) -> None:
    # The other half of the test above: without the categorical config the
    # SAME resource and the same two number scores do reach the bulk path, so
    # what disqualifies it is the categorical score and nothing else.
    resource = _allele_three_scores_tabix(tmp_path)
    confs: dict = {"s": _hist_conf(), "third": _hist_conf()}
    assert G._can_bulk_histogram(resource, confs)


def _spy_on_bulk(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record, in order, every call that ENTERS a bulk scan function.

    Comparing a task's output against the per-record path cannot tell the
    two paths apart -- they are required to agree to the bit, so a task that
    quietly fell back to ``_do_histogram`` would produce identical numbers
    and the comparison would pass.  Silent fallback is precisely the failure
    mode this gate exists to prevent, so the dispatch tests watch the call
    instead of only the result.
    """
    calls: list[str] = []

    def wrap(name: str) -> None:
        original = getattr(G, name)

        def spy(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(G, name, staticmethod(spy))

    wrap("_do_histogram_bulk")
    wrap("_do_min_max_bulk")
    return calls


@pytest.mark.parametrize(
    "make_resource", [_allele_tabix, _fragment_tabix],
    ids=["allele", "fragment"])
def test_dispatch_uses_bulk_for_allele_and_fragment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    make_resource: Callable[[pathlib.Path], GenomicResource],
) -> None:
    # The task-level entry points, not just the bulk functions directly:
    # both must be eligible, both must actually RUN the bulk function, and
    # both must reproduce the per-record numbers exactly.
    resource = make_resource(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref_hist = G._do_histogram(resource, confs, "chr1", 1, 300)
    ref_min_max = G._do_min_max(resource, ["s"], "chr1", 1, 300)

    calls = _spy_on_bulk(monkeypatch)
    assert G._can_bulk_histogram(resource, confs)
    assert G._bulk_scan_eligible(resource, ["s"])
    _assert_hists_equal(
        G._do_histogram_task(resource, confs, "chr1", 1, 300), ref_hist)
    _assert_min_max_equal(
        G._do_min_max_task(resource, ["s"], "chr1", 1, 300), ref_min_max)
    assert calls == ["_do_histogram_bulk", "_do_min_max_bulk"]


def test_dispatch_keeps_an_ineligible_score_off_the_bulk_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The spy's own control: an np_score is deliberately excluded, so neither
    # task may enter a bulk function.  Without this, an empty call log in the
    # test above could mean "the spy never fires" rather than "the bulk path
    # ran".
    resource = (
        a_np_score().with_score("score", "float").with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"score": _hist_conf()}
    calls = _spy_on_bulk(monkeypatch)
    G._do_histogram_task(resource, confs, "1", 1, 20)
    G._do_min_max_task(resource, ["score"], "1", 1, 20)
    assert calls == []


def test_dispatch_keeps_an_unbounded_region_off_the_bulk_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An eligible KIND is still not enough: the bulk read needs concrete
    # bounds, so a whole-contig scan keeps the per-record path.
    resource = _fragment_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    calls = _spy_on_bulk(monkeypatch)
    G._do_histogram_task(resource, confs, "chr1", None, None)
    G._do_min_max_task(resource, ["s"], "chr1", None, None)
    assert calls == []


@pytest.mark.parametrize(
    ("score_class", "weight_is_span", "expected_weight"),
    [
        (PositionScore, True, 10),
        (AlleleScore, False, 1),
        (FragmentScore, False, 1),
    ],
)
def test_the_weight_rule_is_stated_once_per_kind(
    score_class: type[GenomicScore],
    weight_is_span: bool,
    expected_weight: int,
) -> None:
    # ``RECORD_WEIGHT_IS_SPAN`` (read by the bulk scan, which cannot call a
    # per-record hook) and ``_record_weight`` (read by ``aggregate_region``)
    # are two readings of ONE rule.  They must not be able to disagree, so
    # the hook derives from the flag -- and this pins the derivation in
    # numbers rather than in code: a ten-base record weighs its span for a
    # position score and 1 for the other two kinds.
    assert score_class.RECORD_WEIGHT_IS_SPAN is weight_is_span
    assert score_class._record_weight(10, 19) == expected_weight
