# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``GenomicScore.aggregate_region``: a region reduced to one value per score.

The aggregating counterpart of ``fetch_region_segments``.  What these
pin is mostly its agreement with things that already exist -- the annotators,
and
the per-type weighting rule ``WeightedValues`` documents -- because a helper
that answers a region differently from the annotator would be worse than no
helper at all.
"""
from __future__ import annotations

import pathlib
from collections.abc import Generator

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    PositionScore,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.testing import build_inmemory_test_resource
from gain.genomic_resources.testing.builders import a_grr, a_position_score

# Two records, deliberately of DIFFERENT widths, so that anything which
# forgets to weight by span gets a visibly different answer: mean over the
# records is (0.2 + 0.8) / 2 = 0.5, mean over the base pairs is
# (0.2*4 + 0.8*1) / 5 = 0.32.
_WIDE = """
    chrom  pos_begin  pos_end  s
    1      10         13       0.2
    1      14         14       0.8
"""


@pytest.fixture
def wide(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data(_WIDE)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("s")).open()


def test_a_bare_score_id_uses_the_resource_default(wide: PositionScore) -> None:
    with wide:
        # float on a position score defaults to `mean`, span-weighted.
        assert wide.aggregate_region("1", 10, 14, ["s"]) == [
            pytest.approx((0.2 * 4 + 0.8) / 5)]


def test_a_pair_names_the_aggregator(wide: PositionScore) -> None:
    with wide:
        assert wide.aggregate_region("1", 10, 14, [("s", "max")]) == [0.8]
        assert wide.aggregate_region("1", 10, 14, [("s", "min")]) == [0.2]


def test_one_score_may_be_requested_twice_with_different_aggregators(
    wide: PositionScore,
) -> None:
    # The reason the result is a list and not a dict: an annotation config
    # routinely exposes one source as both a min and a max attribute, and a
    # dict keyed by score id would silently drop one of them.
    with wide:
        assert wide.aggregate_region(
            "1", 10, 14, [("s", "min"), "s", ("s", "max")]) == [
            0.2, pytest.approx((0.2 * 4 + 0.8) / 5), 0.8]


def test_a_position_score_weights_by_base_pair(wide: PositionScore) -> None:
    # 0.32 is the span-weighted mean; 0.5 would be the unweighted one.  The
    # difference is the whole point of _record_weight.
    with wide:
        assert wide.aggregate_region("1", 10, 14, ["s"]) == [
            pytest.approx(0.32)]
        assert wide.aggregate_region("1", 10, 14, ["s"]) != [
            pytest.approx(0.5)]


def test_it_agrees_with_fetch_region_weighted_values(
    wide: PositionScore,
) -> None:
    """The annotator's path and this one must fold the same pairs.

    ``fetch_region_weighted_values`` is what ``PositionScoreAnnotator``
    aggregates; both now derive the weight from ``_record_weight``, and this
    is the test that says so in terms of values rather than of code.
    """
    with wide:
        from gain.genomic_resources.aggregators import Aggregator
        aggregator = Aggregator.build("mean")
        expected = aggregator.aggregate_weighted(
            (values[0], weight)
            for values, weight in wide.fetch_region_weighted_values(
                "1", 10, 14, ["s"])
        )
        assert wide.aggregate_region("1", 10, 14, ["s"]) == [
            pytest.approx(expected)]


def test_a_record_the_query_clips_to_nothing_is_not_aggregated(
    wide: PositionScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record with no part inside the window never reaches an aggregator.

    ``fetch_region_segments`` deliberately yields an out-of-region record
    through (gain#553, ADR 0008), at its own extent.  ``aggregate_region``
    carries its OWN ``clip_span`` -- the agreement test above cannot see a
    symmetric removal from both it and ``fetch_region_weighted_values``, so
    each copy gets its own pin.  Without it the dead record's value would
    count -- for a negative number of times, even; ``mean`` catches that,
    and ``max`` -- which registers a value however small its weight --
    catches the zero-overlap record too (gain#639).
    """
    def outside(
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[tuple[int, int, list[float]], None, None]:
        # a [20, 25] record, entirely past the [10, 16] query
        yield (20, 25, [9.9])
        # a record starting ONE past the query -- clipping it naively
        # would produce a zero span, not a skip
        yield (17, 17, [5.5])
        yield (10, 14, [1.0])

    monkeypatch.setattr(wide, "fetch_region_segments", outside)

    with wide:
        assert wide.aggregate_region("1", 10, 16, ["s", ("s", "max")]) == [
            pytest.approx(1.0), 1.0]


def test_an_empty_region_lets_each_aggregator_answer(
    wide: PositionScore,
) -> None:
    # Not a blanket None from THIS method: the answer belongs to each
    # aggregator, and they disagree with each other -- `list` says [] where
    # `count` says None rather than 0 (CountAggregator.get_final chooses
    # that).  Pinned as-is so the helper is never tempted to normalize them.
    with wide:
        assert wide.aggregate_region(
            "1", 900, 950, [("s", "count"), ("s", "list"), ("s", "max")]) == [
            None, [], None]


def test_a_parametrized_aggregator_is_accepted(
    tmp_path: pathlib.Path,
) -> None:
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "str").with_data("""
            chrom  pos_begin  pos_end  s
            1      10         10       a
            1      11         11       b
        """)
    )).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("s")).open()
    with score:
        assert score.aggregate_region("1", 10, 11, [("s", "join(;)")]) == [
            "a;b"]


def test_an_unknown_score_names_the_resource(wide: PositionScore) -> None:
    with wide, pytest.raises(ValueError, match="not defined by resource"):
        wide.aggregate_region("1", 10, 14, ["nope"])


def test_an_unknown_aggregator_names_the_score(wide: PositionScore) -> None:
    # Aggregator.build raises a bare KeyError('mediann') on its own.
    with wide, pytest.raises(ValueError, match="mediann"):
        wide.aggregate_region("1", 10, 14, [("s", "mediann")])


def test_a_bool_score_has_no_default_and_says_so(
    tmp_path: pathlib.Path,
) -> None:
    # `bool` is the one value type whose class default is deliberately None:
    # there is no reduction to pick on the caller's behalf.
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: position_score
            table:
                filename: data.mem
            scores:
                - id: flag
                  type: bool
                  name: flag
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  flag
            1      10         10       True
        """,
    })
    score = PositionScore(res).open()
    with score, pytest.raises(ValueError, match="no default aggregator"):
        score.aggregate_region("1", 10, 10, ["flag"])


def test_an_allele_score_counts_each_line_once(
    tmp_path: pathlib.Path,
) -> None:
    """An allele line counts once, whatever the position span.

    The rule ``WeightedValues`` states per type, and the reason
    ``_record_weight`` is a class hook rather than ``right - left + 1`` in
    the base: two alleles at one position must average as two values.
    """
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: allele_score
            table:
                filename: data.mem
                reference:
                  name: reference
                alternative:
                  name: alternative
            scores:
                - id: freq
                  type: float
                  name: freq
        """,
        "data.mem": """
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.2
            1      10         A          T            0.8
        """,
    })
    score = AlleleScore(res).open()
    with score:
        assert score.aggregate_region("1", 10, 10, [("freq", "mean")]) == [
            pytest.approx(0.5)]
        # and the resource default for an allele score is `max`, not `mean`
        assert score.aggregate_region("1", 10, 10, ["freq"]) == [0.8]


def test_a_fragment_counts_once_however_long_it_is(
    tmp_path: pathlib.Path,
) -> None:
    """A fragment is one observation, not one per base pair it spans.

    Deriving the weight from the record's span in the base class would give
    the 100 bp fragment a hundred times the say of the 1 bp one, and disagree
    with the fragment score annotator for every fragment longer than a base.
    """
    res = build_inmemory_test_resource({
        GR_CONF_FILE_NAME: """
            type: fragment_score
            table:
                filename: data.mem
            scores:
                - id: freq
                  type: float
                  name: freq
        """,
        "data.mem": """
            chrom  pos_begin  pos_end  freq
            1      10         109      0.2
            1      200        200      0.8
        """,
    })
    score = FragmentScore(res).open()
    with score:
        assert score.aggregate_region("1", 1, 500, [("freq", "mean")]) == [
            pytest.approx(0.5)]
