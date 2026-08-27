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
    a_vcf_info_score,
    an_allele_score,
)


def _hist_conf(
    view_min: float = 0, view_max: float = 1,
) -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": view_min, "max": view_max},
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
    # ``AlleleScore.fetch_region_segments`` yields ``(pos, pos,
    # values)``, so a record weighs 1 however far its ``pos_end`` reaches.
    # Span-weighting it
    # in bulk would silently produce a different histogram.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref = G._do_histogram(resource, confs, "chr1", 1, 40)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 40)
    _assert_hists_equal(bulk, ref)
    # Three records, one count each -- not 10 + 1 + 1 for the spans.
    assert bulk["s"].bars.sum() == 3


def test_a_multi_base_record_is_owned_by_the_region_holding_its_begin(
    tmp_path: pathlib.Path,
) -> None:
    # gain#816: a region owns the records whose pos_begin falls in it,
    # and no other.  The ten-base record at 10..19 reaches into 15..25
    # but begins outside it, so that region owns nothing at all; 5..15
    # owns it and the record sharing its position, and each weighs 1.
    # Were the record measured by every region its span reached, it
    # would be counted twice over the two.
    resource = _allele_multibase_tabix(tmp_path)
    confs: dict = {"s": _hist_conf()}

    reaching = G._do_histogram(resource, confs, "chr1", 15, 25)
    owning = G._do_histogram(resource, confs, "chr1", 5, 15)

    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 15, 25), reaching)
    _assert_hists_equal(
        G._do_histogram_bulk(resource, confs, "chr1", 5, 15), owning)
    assert reaching["s"].bars.sum() == 0
    assert owning["s"].bars.sum() == 2


def _fragment_tabix(
    tmp_path: pathlib.Path, resource_type: str = "fragment_score",
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


@pytest.mark.legacy_vocabulary
@pytest.mark.parametrize("resource_type", FRAGMENT_SCORE_TYPES)
def test_fragment_score_is_bulk_scan_eligible_in_both_spellings(
    tmp_path: pathlib.Path, resource_type: str,
) -> None:
    # ``fragment_score`` and the deprecated ``cnv_collection`` (gain#471,
    # deprecated by gain#538) are both spellings of one kind.  A gate that
    # named only one would send the other back to the per-record path
    # silently -- no error, no failing test.  Marked ``legacy_vocabulary``:
    # the legacy case declares the old spelling, so it announces it.
    resource = _fragment_tabix(tmp_path, resource_type)
    assert resource.get_type() == resource_type
    assert G._bulk_scan_eligible(resource, ["s"])


@pytest.mark.legacy_vocabulary
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


def test_bulk_min_max_matches_per_record_fragment(
    tmp_path: pathlib.Path,
) -> None:
    resource = _fragment_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 300)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 300)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)


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


def test_a_str_score_and_a_float_score_scan_together(
    tmp_path: pathlib.Path,
) -> None:
    # The histogram build is dispatched per resource, not per score, so a
    # resource mixing a number histogram, a categorical one over a str score
    # and a null config has to be admitted as a whole -- and the batch it
    # scans then carries two array shapes at once, float64 and object.
    #
    # All three ids exist on the resource ON PURPOSE: the eligibility check
    # resolves each id against the resource's score definitions, so ids that
    # named nothing would make this pass without the resource having any say.
    resource = _allele_three_scores_tabix(tmp_path)
    score = build_score_from_resource(resource)
    assert set(score.get_all_scores()) == {"s", "other", "third"}
    confs: dict = {
        "s": _hist_conf(),
        "other": CategoricalHistogramConfig.default_config(),
        "third": NullHistogramConfig("no reason"),
    }
    assert G._can_bulk_histogram(resource, confs)

    ref = G._do_histogram(resource, confs, "chr1", 1, 20)
    bulk = G._do_histogram_bulk(resource, confs, "chr1", 1, 20)

    assert set(bulk) == set(ref) == {"s", "other"}
    assert np.array_equal(bulk["s"].bars, ref["s"].bars)
    assert bulk["other"].raw_values == ref["other"].raw_values
    # sanity: an allele record weighs 1 however wide it is, so the two
    # alleles here are one count each.
    assert bulk["other"].raw_values == {"aaa": 1, "bbb": 1}


def test_a_histogram_paired_with_the_wrong_score_type_disqualifies_it(
    tmp_path: pathlib.Path,
) -> None:
    # What admits a resource is each histogram sitting on a score whose
    # column comes out in the shape that histogram accumulates -- a number
    # histogram over a numeric score, a categorical one over a str score.
    # Same resource, same three ids, one config swapped onto the wrong score
    # each time.
    resource = _allele_three_scores_tabix(tmp_path)
    number = _hist_conf()
    categorical = CategoricalHistogramConfig.default_config()

    assert G._can_bulk_histogram(resource, {
        "s": number, "other": categorical, "third": number})
    # A categorical histogram over the float score...
    assert not G._can_bulk_histogram(resource, {
        "s": categorical, "other": categorical, "third": number})
    # ...and a number histogram over the str score.
    assert not G._can_bulk_histogram(resource, {
        "s": number, "other": number, "third": number})


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
        G._do_histogram_task(
            resource, confs, "chr1", 1, 300).histograms, ref_hist)
    _assert_min_max_equal(
        G._do_min_max_task(resource, ["s"], "chr1", 1, 300), ref_min_max)
    assert calls == ["_do_histogram_bulk", "_do_min_max_bulk"]


def test_dispatch_keeps_an_ineligible_score_off_the_bulk_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The spy's own control: an allele score with no tabix index serves no
    # column arrays, so neither task may enter a bulk function.  Without
    # this, an empty call log in the test above could mean "the spy never
    # fires" rather than "the bulk path ran".
    #
    # This control used to be an ``np_score``, excluded from the bulk gate
    # by resource type until gain#920 removed the type.  Backend array
    # support is the same control expressed through the predicate that
    # survives.
    resource = (
        an_allele_score().with_score("score", "float")
        .build_resource(tmp_path)
    )
    assert not G._bulk_scan_eligible(resource, ["score"])
    confs: dict = {"score": _hist_conf()}
    calls = _spy_on_bulk(monkeypatch)
    G._do_histogram_task(resource, confs, "1", 1, 20)
    G._do_min_max_task(resource, ["score"], "1", 1, 20)
    assert not calls, calls


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
    assert not calls, calls


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


# --- batch boundaries -------------------------------------------------------
#
# ``_SCAN_BATCH_SIZE`` is 100_000 in production, so no fixture in this suite
# reaches a batch boundary by accident -- every scan above runs in exactly one
# batch.  That is precisely where the accumulators are at risk: every bar is
# accrued once per BATCH per score and the overlap guard carries
# ``prev_right`` ACROSS batches, so a double count, a dropped record or a
# lost carry is invisible until a region spans two.
# The tests below force the boundary instead of waiting for a fixture large
# enough to hit one.

_BATCH_SIZES = [1, 2, 3, 100]


def _count_bulk_batches(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the column-array batches the bulk scans actually consume.

    Without this the batch-size tests would be batch-size tests only by
    intention: ``batch_size`` is documented as a HINT that a backend may
    ignore (``BigWigTable`` does), so a scan that quietly delivered every
    record in one batch would satisfy every assertion below while testing
    nothing about boundaries at all.
    """
    counter = [0]
    original = GenomicScore.fetch_region_value_arrays

    def counting(self: GenomicScore, *args: Any, **kwargs: Any) -> Any:
        for batch in original(self, *args, **kwargs):
            counter[0] += 1
            yield batch

    monkeypatch.setattr(
        GenomicScore, "fetch_region_value_arrays", counting)
    return counter


def _five_fragments_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """Five overlapping fragments, of spans 91, 11, 176, 2 and 11."""
    return (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      0.1
            chr1   20         30       0.9
            chr1   25         200      0.5
            chr1   40         41       0.3
            chr1   50         60       0.7
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _five_alleles_tabix(tmp_path: pathlib.Path) -> GenomicResource:
    """Five allele records, three at one position and one of them NA."""
    return (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
            chr1   10         A          C            NA
            chr1   10         A          T            0.9
            chr1   14         C          T            0.3
            chr1   20         G          A            0.5
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )


def _assert_bulk_agrees_at_batch_size(
    resource: GenomicResource,
    monkeypatch: pytest.MonkeyPatch,
    batch_size: int,
    region: tuple[int, int],
    kept: int,
) -> tuple[dict, dict]:
    """Scan ``region`` both ways at ``batch_size`` and assert they agree."""
    start, end = region
    confs: dict = {"s": _hist_conf()}
    ref_hist = G._do_histogram(resource, confs, "chr1", start, end)
    ref_min_max = G._do_min_max(resource, ["s"], "chr1", start, end)

    monkeypatch.setattr(G, "_SCAN_BATCH_SIZE", batch_size)
    batches = _count_bulk_batches(monkeypatch)
    bulk_hist = G._do_histogram_bulk(resource, confs, "chr1", start, end)
    bulk_min_max = G._do_min_max_bulk(resource, ["s"], "chr1", start, end)

    _assert_hists_equal(bulk_hist, ref_hist)
    _assert_min_max_equal(bulk_min_max, ref_min_max)
    # Two scans ran, so two batches is the one-batch-each floor; anything
    # above it means a scan really was split.
    if batch_size < kept:
        assert batches[0] > 2, (batch_size, kept, batches[0])
    else:
        assert batches[0] == 2, (batch_size, kept, batches[0])
    return bulk_hist, bulk_min_max


@pytest.mark.parametrize("batch_size", _BATCH_SIZES)
@pytest.mark.parametrize(
    ("region", "read", "owned"), [((1, 300), 5, 5), ((35, 65), 4, 2)],
    ids=["whole contig", "owned subset"])
def test_bulk_fragment_scan_agrees_across_batch_boundaries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_size: int,
    region: tuple[int, int],
    read: int,
    owned: int,
) -> None:
    # Every fragment weighs 1, so the bars are a per-record tally and a
    # per-batch accumulator that double counts or loses a record shows up
    # there.  The second region reads four fragments and owns two of them
    # (only 40..41 and 50..60 BEGIN inside 35..65), so the tally is not
    # merely "all the rows the backend read".
    bulk_hist, _bulk_min_max = _assert_bulk_agrees_at_batch_size(
        _five_fragments_tabix(tmp_path), monkeypatch, batch_size,
        region, read)
    assert bulk_hist["s"].bars.sum() == owned


@pytest.mark.parametrize("batch_size", _BATCH_SIZES)
@pytest.mark.parametrize(
    ("region", "kept", "binned"), [((1, 30), 5, 4), ((12, 30), 2, 2)],
    ids=["whole contig", "owned subset"])
def test_bulk_allele_scan_agrees_across_batch_boundaries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    batch_size: int,
    region: tuple[int, int],
    kept: int,
    binned: int,
) -> None:
    # Three of the five records share position 10 and one of THOSE is NA, so
    # a batch boundary can fall inside a shared-position site and inside the
    # nan handling at once.
    bulk_hist, _bulk_min_max = _assert_bulk_agrees_at_batch_size(
        _five_alleles_tabix(tmp_path), monkeypatch, batch_size, region, kept)
    # ``kept`` records are read; the NA one is not binned, so the unclipped
    # region bins four of its five.  The clipped region starts past the NA.
    assert bulk_hist["s"].bars.sum() == binned


# --- degenerate regions and columns -----------------------------------------


def test_an_all_na_fragment_column_folds_to_nothing(
    tmp_path: pathlib.Path,
) -> None:
    # Every value is nan, so the ``if finite.size:`` block that folds min/max
    # never runs and each accumulator has to yield its seeded answer.  A
    # reduction that forgot to drop the nans first would report nan-vs-number
    # differently between the two paths.
    resource = (
        a_fragment_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  s
            chr1   10         100      NA
            chr1   20         30       NA
            chr1   25         200      NA
            """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}
    ref_hist = G._do_histogram(resource, confs, "chr1", 1, 300)
    ref_min_max = G._do_min_max(resource, ["s"], "chr1", 1, 300)
    bulk_hist = G._do_histogram_bulk(resource, confs, "chr1", 1, 300)
    bulk_min_max = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 300)

    _assert_hists_equal(bulk_hist, ref_hist)
    _assert_min_max_equal(bulk_min_max, ref_min_max)
    assert np.isnan(bulk_min_max["s"].min)
    assert np.isnan(bulk_min_max["s"].max)
    assert bulk_hist["s"].bars.sum() == 0


@pytest.mark.parametrize(
    "make_resource", [_allele_tabix, _fragment_tabix],
    ids=["allele", "fragment"])
def test_a_region_past_the_end_of_the_table_scans_to_nothing(
    tmp_path: pathlib.Path,
    make_resource: Callable[[pathlib.Path], GenomicResource],
) -> None:
    # No batch is produced at all, so every accumulator is asked for its
    # answer without ever having been fed one.  A min/max seeded at a real
    # number, or a histogram seeded with a bar, would show up here.
    resource = make_resource(tmp_path)
    confs: dict = {"s": _hist_conf()}
    ref_hist = G._do_histogram(resource, confs, "chr1", 5000, 6000)
    ref_min_max = G._do_min_max(resource, ["s"], "chr1", 5000, 6000)
    bulk_hist = G._do_histogram_bulk(resource, confs, "chr1", 5000, 6000)
    bulk_min_max = G._do_min_max_bulk(resource, ["s"], "chr1", 5000, 6000)

    _assert_hists_equal(bulk_hist, ref_hist)
    _assert_min_max_equal(bulk_min_max, ref_min_max)
    assert bulk_hist["s"].bars.sum() == 0
    assert np.isnan(bulk_min_max["s"].min)


@pytest.mark.parametrize(
    "make_resource", [_allele_tabix, _fragment_tabix],
    ids=["allele", "fragment"])
def test_a_region_holding_a_single_record(
    tmp_path: pathlib.Path,
    make_resource: Callable[[pathlib.Path], GenomicResource],
) -> None:
    # The one-record batch is the degenerate case of every vectorized step:
    # the within-batch overlap check compares ``kleft[1:]`` against
    # ``kright[:-1]`` -- both empty here -- and the reductions run over a
    # single element.
    resource = make_resource(tmp_path)
    # 14 is the allele fixture's lone non-shared record.  24..30 is chosen
    # so the fragment fixture leaves exactly one: all three fragments are
    # fetched (10..100 and 20..30 both reach into it) and only 25..200
    # BEGINS inside, so the single record is one the selection had to
    # think about rather than the only row the backend returned.
    start, end = (14, 14) if make_resource is _allele_tabix else (24, 30)
    confs: dict = {"s": _hist_conf()}
    ref_hist = G._do_histogram(resource, confs, "chr1", start, end)
    ref_min_max = G._do_min_max(resource, ["s"], "chr1", start, end)
    bulk_hist = G._do_histogram_bulk(resource, confs, "chr1", start, end)
    bulk_min_max = G._do_min_max_bulk(resource, ["s"], "chr1", start, end)

    _assert_hists_equal(bulk_hist, ref_hist)
    _assert_min_max_equal(bulk_min_max, ref_min_max)
    assert bulk_hist["s"].bars.sum() == 1


@pytest.mark.parametrize(
    ("make_resource", "below", "above"),
    [(_allele_tabix, 2, 1), (_fragment_tabix, 1, 1)],
    ids=["allele", "fragment"])
def test_values_outside_the_view_range_reach_the_out_of_range_bins(
    tmp_path: pathlib.Path,
    make_resource: Callable[[pathlib.Path], GenomicResource],
    below: int,
    above: int,
) -> None:
    # Every other test here uses a view range wide enough to hold the whole
    # fixture, so ``out_of_range_bins`` is compared as [0, 0] and the
    # comparison says nothing.  A narrow range makes it carry real counts on
    # both sides.
    resource = make_resource(tmp_path)
    confs: dict = {"s": _hist_conf(0.25, 0.55)}
    ref_hist = G._do_histogram(resource, confs, "chr1", 1, 300)
    bulk_hist = G._do_histogram_bulk(resource, confs, "chr1", 1, 300)

    _assert_hists_equal(bulk_hist, ref_hist)
    assert bulk_hist["s"].out_of_range_bins == [below, above]


def test_multi_base_allele_record_min_max_reads_the_same_selection(
    tmp_path: pathlib.Path,
) -> None:
    # The ten-base allele record is exercised on the histogram path above;
    # min/max is the other consumer of the same selection code, so it
    # gets the same fixture.
    resource = _allele_multibase_tabix(tmp_path)
    ref = G._do_min_max(resource, ["s"], "chr1", 1, 40)
    bulk = G._do_min_max_bulk(resource, ["s"], "chr1", 1, 40)
    _assert_min_max_equal(bulk, ref)
    assert (bulk["s"].min, bulk["s"].max) == (0.1, 0.9)

    # 15..25 is reached by the ten-base record but owns none, so it
    # reduces nothing; 20..40 owns the 30..30 record alone.  Both paths
    # must agree, or a region would measure differently by which one
    # served it.
    for start, end, extremes in ((15, 25, None), (20, 40, (0.5, 0.5))):
        region_ref = G._do_min_max(resource, ["s"], "chr1", start, end)
        region_bulk = G._do_min_max_bulk(resource, ["s"], "chr1", start, end)
        _assert_min_max_equal(region_bulk, region_ref)
        if extremes is None:
            assert np.isnan(region_bulk["s"].min)
            assert np.isnan(region_bulk["s"].max)
        else:
            assert (region_bulk["s"].min, region_bulk["s"].max) == extremes
