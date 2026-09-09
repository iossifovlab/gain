# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The logical ``get_`` read plane of ``PositionScore`` (#727).

On this plane a position score is a function from a genomic position to a
record of named score values, defined on the per-position expansion: a gap in
coverage is not an absence but a run of positions whose value is ``None``,
and it counts.  These tests pin the plane's own contract; its agreement with
the segment-shaped reads is pinned by the bridge test against
``aggregate_region``.
"""
from __future__ import annotations

import pathlib
from collections.abc import Generator

import pytest
from gain.genomic_resources.aggregators import PositionScoreAggregationQuery
from gain.genomic_resources.genomic_scores import PositionScore
from gain.genomic_resources.testing.builders import (
    BigWigScoreBuilder,
    PositionScoreBuilder,
    a_bigwig_score,
    a_grr,
    a_position_score,
)

from tests.small.genomic_resources.conftest import a_flag_score

# Two records with a two-position hole between them: positions 10-13 carry
# 0.2, 14-15 nothing, 16 carries 0.8.
_GAPPED = """
    chrom  pos_begin  pos_end  s
    1      10         13       0.2
    1      16         16       0.8
"""


@pytest.fixture
def gapped(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data(_GAPPED)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("s")).open()


def test_a_region_expands_to_one_tuple_per_position(
    gapped: PositionScore,
) -> None:
    with gapped:
        assert list(gapped.get_scores_in_region("1", 12, 16, ["s"])) == [
            (0.2,), (0.2,), (None,), (None,), (0.8,),
        ]


@pytest.fixture
def two_scored(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("s", (
        a_position_score()
        .with_score("a", "float")
        .with_score("b", "str")
        .with_data("""
            chrom  pos_begin  pos_end  a    b
            1      10         13       0.2  x
            1      16         16       0.8  y
        """)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("s")).open()


def test_a_singular_read_refuses_score_none_on_two_scores(
    two_scored: PositionScore,
) -> None:
    with two_scored, pytest.raises(ValueError, match="exactly one"):
        two_scored.get_score_in_region("1", 10, 16)


def test_scores_none_resolves_to_every_score_in_definition_order(
    two_scored: PositionScore,
) -> None:
    with two_scored:
        assert list(two_scored.get_scores_in_region("1", 13, 16)) == [
            (0.2, "x"), (None, None), (None, None), (0.8, "y"),
        ]


def test_the_singular_region_read_yields_bare_values(
    gapped: PositionScore,
) -> None:
    # `score=None` means "all the scores this resource has", which a singular
    # method can honour because this resource declares exactly one.
    with gapped:
        assert list(gapped.get_score_in_region("1", 12, 16)) == [
            0.2, 0.2, None, None, 0.8,
        ]


def test_a_malformed_region_is_refused_when_called_not_when_consumed(
    gapped: PositionScore,
) -> None:
    # The refusal must not hide inside the generator: building it IS the
    # mistake, and the guard fires there (the fetch_records pattern).
    with gapped:
        with pytest.raises(ValueError, match="not among the available"):
            gapped.get_scores_in_region("nope", 10, 16)
        with pytest.raises(ValueError, match="start"):
            gapped.get_scores_in_region("1", 0, 16)
        with pytest.raises(ValueError, match="end"):
            gapped.get_scores_in_region("1", 16, 10)


def test_a_replacement_makes_the_gap_count(gapped: PositionScore) -> None:
    # Positions 14 and 15 are uncovered.  Without a replacement they are
    # inert for every aggregator; with one they count -- the entire
    # observable difference between this plane and `aggregate_region`.
    with gapped:
        assert gapped.get_scores_in_region_agg(
            "1", 10, 16,
            [PositionScoreAggregationQuery("s", none_value_replacement=0.0)],
        ) == (pytest.approx((0.2 * 4 + 0.0 * 2 + 0.8) / 7),)
        assert gapped.get_scores_in_region_agg(
            "1", 10, 16, [PositionScoreAggregationQuery("s")],
        ) == (pytest.approx((0.2 * 4 + 0.8) / 5),)


def test_the_singular_aggregating_read_returns_a_bare_value(
    gapped: PositionScore,
) -> None:
    with gapped:
        assert gapped.get_score_in_region_agg(
            "1", 10, 16, none_value_replacement=0.0,
        ) == pytest.approx((0.2 * 4 + 0.8) / 7)
        assert gapped.get_score_in_region_agg(
            "1", 10, 16, aggregator="max") == 0.8


def test_a_replacement_of_the_wrong_type_is_refused(
    gapped: PositionScore,
) -> None:
    with gapped:
        with pytest.raises(ValueError, match="does not match"):
            gapped.get_score_in_region_agg(
                "1", 10, 16, none_value_replacement="zero")
        # bool is not a numeric replacement, exactly as a bool-typed score
        # is not a numeric one (validate_aggregator's precedent).
        with pytest.raises(ValueError, match="does not match"):
            gapped.get_score_in_region_agg(
                "1", 10, 16, none_value_replacement=True)


@pytest.fixture
def flagged(tmp_path: pathlib.Path) -> PositionScore:
    return a_flag_score(tmp_path).open()


def test_a_query_invalid_several_ways_reports_the_first_ground(
    gapped: PositionScore, flagged: PositionScore,
) -> None:
    """Which refusal wins is a decision, so it is pinned rather than left.

    The order is: the score must exist, then its replacement must be of a
    type the score can mean, then an aggregator must be resolvable.  A
    query can fail two of those at once, and the resolver walks them in
    that order -- so this pins the walk, not merely each guard (gain#1087,
    where the guards became shared and the interleave did not).

    What each guard SAYS is pinned once, against both surfaces at once, in
    ``test_score_aggregation``: this surface's wording of the two shared
    refusals is not restated here, so rewording one is one edit.
    """
    # Unknown score AND a replacement no score could take: the score must
    # exist before there is a value type to judge a replacement against.
    with gapped, pytest.raises(ValueError) as unknown:
        gapped.get_scores_in_region_agg(
            "1", 10, 16, [
                PositionScoreAggregationQuery(
                    "nope", none_value_replacement="zero"),
            ])
    assert "is not defined by resource" in str(unknown.value)

    # Bad replacement AND no resolvable aggregator: the replacement is
    # judged first, so `does not match` -- not `no default aggregator`.
    with flagged, pytest.raises(ValueError) as mismatch:
        flagged.get_scores_in_region_agg(
            "1", 10, 10, [
                PositionScoreAggregationQuery(
                    "flag", none_value_replacement="zero"),
            ])
    assert "does not match its value type" in str(mismatch.value)


def test_a_replacement_substitutes_for_covered_but_na_positions(
    tmp_path: pathlib.Path,
) -> None:
    # Position 11 is covered by a record whose value is NA ("." parses to
    # None for a float score).  The replacement substitutes for EVERY null
    # of the expansion, uncovered and covered-but-NA alike.
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            1      10         10       0.2
            1      11         11       .
        """)
    )).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("s")).open()
    with score:
        assert list(score.get_score_in_region("1", 10, 11)) == [
            pytest.approx(0.2), None]
        assert score.get_score_in_region_agg(
            "1", 10, 11, none_value_replacement=1.0,
        ) == pytest.approx((0.2 + 1.0) / 2)


# Two records overlapping on positions 12-14: reads never validate (ADR
# 0008), so the logical plane must still answer, and it answers FIRST-wins.
_OVERLAPPING = """
    chrom  pos_begin  pos_end  s
    1      10         14       1.0
    1      12         16       2.0
"""


@pytest.fixture
def overlapping(tmp_path: pathlib.Path) -> PositionScore:
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data(_OVERLAPPING)
    )).build_repo(tmp_path)
    return PositionScore(repo.get_resource("s")).open()


def test_where_two_records_cover_one_position_the_first_answers(
    overlapping: PositionScore,
) -> None:
    # The later record contributes only the positions the earlier one did
    # not, so every position answers exactly once.
    with overlapping:
        assert list(overlapping.get_score_in_region("1", 10, 16)) == [
            1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0,
        ]


def test_first_wins_repairs_the_double_count_aggregate_region_has(
    overlapping: PositionScore,
) -> None:
    # `aggregate_region` weighs each record by its clipped span, so the
    # three shared positions count twice: (1.0*5 + 2.0*5) / 10 = 1.5.  On
    # the logical plane each position contributes exactly once by
    # construction, so accumulated weight equals the region width, 7.
    with overlapping:
        assert overlapping.get_score_in_region_agg("1", 10, 16) == \
            pytest.approx((1.0 * 5 + 2.0 * 2) / 7)
        assert overlapping.aggregate_region("1", 10, 16, ["s"]) == [
            pytest.approx(1.5)]


def test_bins_follow_the_global_grid_and_edge_bins_are_clipped(
    gapped: PositionScore,
) -> None:
    # The grid is anchored at position 1, not at the query: with bin_size 5
    # the bins are 6-10, 11-15, 16-20, whatever the query.  Edge bins are
    # clipped to the query so the yielded bounds name exactly what was
    # aggregated -- and every bin in range is emitted, including 21-25 and
    # 26, which no record touches.
    with gapped:
        assert list(gapped.get_scores_in_bins(
            "1", 10, 26, 5, [PositionScoreAggregationQuery("s")],
        )) == [
            (10, 10, (pytest.approx(0.2),)),
            (11, 15, (pytest.approx(0.2),)),
            (16, 20, (pytest.approx(0.8),)),
            (21, 25, (None,)),
            (26, 26, (None,)),
        ]


def test_a_replacement_counts_the_uncovered_part_of_a_bin(
    gapped: PositionScore,
) -> None:
    # Bin 11-15 holds three covered positions (0.2) and two uncovered; the
    # replacement makes the uncovered two count.
    with gapped:
        assert list(gapped.get_scores_in_bins(
            "1", 11, 15, 5,
            [PositionScoreAggregationQuery("s", none_value_replacement=0.0)],
        )) == [(11, 15, (pytest.approx(0.2 * 3 / 5),))]


def test_a_contig_the_score_never_mentions_bins_as_one_uncovered_run(
    gapped: PositionScore,
) -> None:
    # A genome-wide run over a track that skips a chromosome is the normal
    # case, not an error the aggregating reads should raise.  The absent
    # contig folds as a single uncovered run, on the ordinary global grid:
    # same bounds a covered contig would yield, and -- with no replacement
    # -- the ``None`` an uncovered bin yields anywhere else.
    with gapped:
        assert list(gapped.get_scores_in_bins(
            "nope", 10, 26, 5, [PositionScoreAggregationQuery("s")],
        )) == [
            (10, 10, (None,)),
            (11, 15, (None,)),
            (16, 20, (None,)),
            (21, 25, (None,)),
            (26, 26, (None,)),
        ]


def test_a_contig_the_score_never_mentions_aggregates_as_uncovered(
    gapped: PositionScore,
) -> None:
    # The region-aggregating read takes the exemption on the same terms as
    # the binned one, and the point of it is that the two windows below are
    # not distinguishable: a contig the score never mentions answers exactly
    # as a window on a contig it has but does not cover.
    with gapped:
        assert gapped.get_score_in_region_agg("nope", 10, 26) is None
        assert gapped.get_score_in_region_agg("1", 100, 116) is None

        # And with a replacement, both count the whole window by width.
        counted = {
            "aggregator": "count", "none_value_replacement": 0.0,
        }
        assert gapped.get_score_in_region_agg("nope", 10, 26, **counted) == 17
        assert gapped.get_score_in_region_agg("1", 100, 116, **counted) == 17


def test_a_replacement_covers_a_contig_the_score_never_mentions(
    gapped: PositionScore,
) -> None:
    # The uncovered run is folded weighted by what each bin takes of it, so
    # the replacement reaches the aggregator once per POSITION, not once per
    # bin.  ``count`` is where that is visible: it returns the weight it was
    # given, so the clipped edge bins 10-10 and 26-26 count 1 while the full
    # bins count 5.  ``mean`` of a constant is that constant, whatever the
    # width -- both are pinned, because only the pair distinguishes a
    # correctly weighted fold from one that added the replacement once.
    with gapped:
        assert list(gapped.get_scores_in_bins("nope", 10, 26, 5, [
            PositionScoreAggregationQuery(
                "s", "count", none_value_replacement=0.0),
            PositionScoreAggregationQuery(
                "s", "mean", none_value_replacement=7.0),
        ])) == [
            (10, 10, (1, pytest.approx(7.0))),
            (11, 15, (5, pytest.approx(7.0))),
            (16, 20, (5, pytest.approx(7.0))),
            (21, 25, (5, pytest.approx(7.0))),
            (26, 26, (1, pytest.approx(7.0))),
        ]


def test_a_segment_straddling_a_bin_boundary_splits_at_it(
    tmp_path: pathlib.Path,
) -> None:
    # The record 6-12 spans the 10|11 boundary: five of its bases weigh
    # into bin 6-10 and two into bin 11-15, where they meet 13-15's 4.0.
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            1      6          12       1.0
            1      13         15       4.0
        """)
    )).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("s")).open()
    with score:
        assert list(score.get_score_in_bins("1", 6, 15, 5)) == [
            (6, 10, pytest.approx(1.0)),
            (11, 15, pytest.approx((1.0 * 2 + 4.0 * 3) / 5)),
        ]


# The plane rides `fetch_region_segments`, so it must answer
# identically off
# every backend a position score realizes onto.  One dataset (10-13: 0.2,
# 16: 0.8), three realizations; a bigWig stores float32, hence approx.
def _backed_builder(
    backend: str,
) -> PositionScoreBuilder | BigWigScoreBuilder:
    if backend == "bigwig":
        return (
            a_bigwig_score().with_score("s", "float")
            .with_data("""
                1  9   13  0.2
                1  15  16  0.8
            """)
            .with_chrom_lens({"1": 1000})
        )
    builder = a_position_score().with_score("s", "float").with_data(_GAPPED)
    return builder.with_tabix() if backend == "tabix" else builder


@pytest.fixture(params=["inmemory", "tabix", "bigwig"])
def backed(
    request: pytest.FixtureRequest, tmp_path: pathlib.Path,
) -> PositionScore:
    repo = a_grr().with_resource(
        "s", _backed_builder(request.param)).build_repo(tmp_path)
    return PositionScore(repo.get_resource("s")).open()


def test_every_backend_answers_the_logical_plane_alike(
    backed: PositionScore,
) -> None:
    with backed:
        assert list(backed.get_score_in_region("1", 12, 16)) == [
            pytest.approx(0.2), pytest.approx(0.2), None, None,
            pytest.approx(0.8),
        ]
        assert backed.get_score_at_position("1", 13) == pytest.approx(0.2)
        assert backed.get_score_at_position("1", 15) is None
        assert backed.get_score_in_region_agg(
            "1", 10, 16, none_value_replacement=0.0,
        ) == pytest.approx((0.2 * 4 + 0.8) / 7)
        assert list(backed.get_score_in_bins("1", 10, 16, 5)) == [
            (10, 10, pytest.approx(0.2)),
            (11, 15, pytest.approx(0.2)),
            (16, 16, pytest.approx(0.8)),
        ]
        # Past the data is uncovered on every backend alike -- the bigWig
        # knows its chromosome length where the tabular backends do not,
        # and must not turn that knowledge into an error.
        assert list(backed.get_score_in_region("1", 900, 902)) == [
            None, None, None,
        ]


# All eleven registered aggregators skip None, so with no replacement set
# the logical plane answers exactly what `aggregate_region` answers -- for
# every one of them, not just the numeric ones.  Removable together with
# `aggregate_region`, should #258's remaining slices retire it.
_ALL_AGGREGATORS = (
    "max", "min", "mean", "median", "count", "concatenate", "mode",
    "join(;)", "list", "bool", "value_count",
)


@pytest.mark.parametrize("aggregator", _ALL_AGGREGATORS)
def test_without_replacement_it_bridges_to_aggregate_region(
    gapped: PositionScore, aggregator: str,
) -> None:
    """With no replacement, nulls are inert and the two paths agree.

    The bridge that says `none_value_replacement` is the ENTIRE observable
    difference between the logical plane and `aggregate_region` on
    non-overlapping data.  Removable together with `aggregate_region`.
    """
    with gapped:
        assert gapped.get_scores_in_region_agg(
            "1", 10, 16, [PositionScoreAggregationQuery("s", aggregator)],
        ) == tuple(gapped.aggregate_region(
            "1", 10, 16, [("s", aggregator)]))


def test_a_fully_shadowed_record_contributes_nothing(
    tmp_path: pathlib.Path,
) -> None:
    # A record entirely inside an earlier one has no position left to
    # answer for -- distinct from the partial overlap, which still
    # contributes its tail.
    repo = a_grr().with_resource("s", (
        a_position_score().with_score("s", "float").with_data("""
            chrom  pos_begin  pos_end  s
            1      10         20       1.0
            1      12         14       9.9
        """)
    )).build_repo(tmp_path)
    score = PositionScore(repo.get_resource("s")).open()
    with score:
        assert list(score.get_score_in_region("1", 10, 20)) == [1.0] * 11
        assert score.get_score_in_region_agg("1", 10, 20) == pytest.approx(
            1.0)


def test_the_bins_read_refuses_the_spans_the_region_read_refuses(
    gapped: PositionScore,
) -> None:
    # Eagerly, when called -- these return generators, so a refactor into a
    # generator function would silently defer every guard to first next().
    #
    # The SPANS only.  An unknown contig is where the bins read parts from
    # the region read (gain#1211): it folds one uncovered run instead of
    # refusing, which
    # ``test_a_contig_the_score_never_mentions_bins_as_one_uncovered_run``
    # pins.
    with gapped:
        with pytest.raises(ValueError, match="1-based"):
            gapped.get_scores_in_bins(
                "1", 0, 16, 5, [PositionScoreAggregationQuery("s")])
        with pytest.raises(ValueError, match="precedes"):
            gapped.get_score_in_bins("1", 16, 10, 5)
        with pytest.raises(ValueError, match="at least one position"):
            gapped.get_score_in_bins("1", 10, 16, 0)


def test_a_record_outside_the_region_is_dropped_not_counted(
    gapped: PositionScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backend answering outside the query must not corrupt the plane.

    ``_score_segments`` deliberately yields such a record through, at its
    own extent -- the misconfigured backend it implies is refused at
    ``open()``, not by a read (gain#553, ADR 0008).  The walker must drop
    it, as ``aggregate_region`` drops
    it with the same ``clip_span``; counting it would yield phantom
    positions past the region width and feed the aggregator a negative
    weight.
    """
    def outside(
        chrom: str,
        pos_begin: int | None = None,
        pos_end: int | None = None,
        scores: list[str] | None = None,
    ) -> Generator[tuple[int, int, list[float]], None, None]:
        # a [20, 25] record, entirely past the [10, 16] query
        yield (20, 25, [9.9])

    monkeypatch.setattr(gapped, "fetch_region_segments", outside)
    with gapped:
        assert list(gapped.get_score_in_region("1", 10, 16)) == [None] * 7
        assert gapped.get_score_in_region_agg("1", 10, 16) is None


def test_an_absent_contig_is_no_licence_to_skip_the_other_guards(
    gapped: PositionScore,
) -> None:
    # The exemption widens what a CONTIG may be and nothing else.  Every
    # other eager refusal the aggregating reads make must fire on an absent
    # contig exactly as on a populated one -- otherwise a typo in the contig
    # would start hiding a second mistake behind it.  Hence both arms: "1"
    # is the contig the fixture HAS, and is here as the control the absent
    # "nope" must match refusal for refusal.
    with gapped:
        for chrom in ("nope", "1"):
            with pytest.raises(ValueError, match="nosuch"):
                gapped.get_scores_in_bins(
                    chrom, 10, 26, 5,
                    [PositionScoreAggregationQuery("nosuch")])
            with pytest.raises(ValueError, match="nosuch"):
                gapped.get_scores_in_region_agg(
                    chrom, 10, 26, [PositionScoreAggregationQuery("nosuch")])
            with pytest.raises(ValueError, match="1-based"):
                gapped.get_score_in_bins(chrom, 0, 26, 5)
            with pytest.raises(ValueError, match="precedes"):
                gapped.get_score_in_region_agg(chrom, 26, 10)

    # And a closed score is refused before any of it.
    with pytest.raises(ValueError, match="not open"):
        gapped.get_score_in_region_agg("nope", 10, 26)
    with pytest.raises(ValueError, match="not open"):
        gapped.get_score_in_bins("nope", 10, 26, 5)


def test_an_absent_contig_is_answered_without_asking_the_backend(
    gapped: PositionScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exemption short-circuits ABOVE the fetch, not inside it.

    The per-kind record transform and every backend table keep their own
    refusal of an unknown contig (gain#1211 changes neither).  They stay
    correct only because nothing asks them about a contig that is not
    there, so that nothing-is-asked is the property worth pinning.
    """
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the backend was asked about an absent contig")

    monkeypatch.setattr(gapped, "fetch_region_segments", refuse)
    with gapped:
        # The bounds and values are pinned by the two tests above; what is
        # under test here is only that neither read reaches the backend.
        assert gapped.get_score_in_region_agg("nope", 10, 26) is None
        assert [
            value for _, _, value in gapped.get_score_in_bins("nope", 10, 26, 5)
        ] == [None] * 5

        # The spy is live rather than vacuous: a contig the score DOES have
        # still goes to the backend, so the two reads above were exempted
        # and not merely silenced.
        with pytest.raises(AssertionError, match="was asked"):
            gapped.get_score_in_region_agg("1", 10, 16)


def test_a_position_read_answers_one_position(
    gapped: PositionScore,
) -> None:
    with gapped:
        assert gapped.get_score_at_position("1", 12) == 0.2
        assert gapped.get_score_at_position("1", 14) is None
        assert gapped.get_scores_at_position("1", 16) == (0.8,)
        assert gapped.get_scores_at_position("1", 15) == (None,)


def test_a_position_read_refuses_what_the_region_read_refuses(
    gapped: PositionScore,
) -> None:
    with gapped:
        with pytest.raises(ValueError, match="not among the available"):
            gapped.get_score_at_position("nope", 12)
        with pytest.raises(ValueError, match="1-based"):
            gapped.get_scores_at_position("1", 0)


def test_a_position_read_answers_one_value_per_id_asked(
    two_scored: PositionScore,
) -> None:
    """One value per REQUESTED id, in the order asked, duplicates included.

    The point read does not dedupe what it is asked for.  The position
    annotator leans on exactly that -- it asks one source per attribute and
    pairs the answers with the attribute names by position, so a read that
    collapsed ``["a", "b", "a"]`` to two answers would have the third
    attribute fall off the end (gain#1111).  The fixture's two scores differ
    in TYPE, so an answer in the wrong slot is visible.
    """
    with two_scored:
        assert two_scored.get_scores_at_position(
            "1", 10, ["a", "b", "a"]) == (0.2, "x", 0.2)


def test_where_two_records_cover_one_position_the_first_answers_a_point_read(
    overlapping: PositionScore,
) -> None:
    # The same first-wins rule the region read follows, asked one position
    # at a time: 12 is covered by both records and answers the earlier one.
    # A second record at a position is not an error here -- several records
    # at one position is a malformed position score, and refusing it is the
    # statistics scan's job rather than a reader's (ADR 0008).
    with overlapping:
        assert overlapping.get_scores_at_position("1", 11) == (1.0,)
        assert overlapping.get_scores_at_position("1", 12) == (1.0,)
        assert overlapping.get_scores_at_position("1", 15) == (2.0,)


def test_a_position_read_refuses_a_closed_score(
    gapped: PositionScore,
) -> None:
    # Every read on the plane resolves through ``_region_read_defs``, which
    # refuses a closed score before it touches a record -- so the point read
    # refuses one too, rather than reaching a table that is not there.
    #
    # NOT a new refusal: the removed ``fetch_position_scores`` raised the
    # same message from its first statement, because ``get_all_chromosomes``
    # checks ``is_open`` before it answers.  Pinned here because it was
    # unpinned before, and because the route it arrives by is now the
    # plane's shared one rather than this read's own.
    gapped.close()
    with pytest.raises(ValueError, match="is not open"):
        gapped.get_scores_at_position("1", 12)


def test_positions_past_the_data_are_uncovered_not_errors(
    gapped: PositionScore,
) -> None:
    # No upper-bound check, deliberately (see #727): past the last record is
    # not distinguishable from a gap, and uncovered is None.
    with gapped:
        assert list(gapped.get_score_in_region("1", 900, 902)) == [
            None, None, None,
        ]
