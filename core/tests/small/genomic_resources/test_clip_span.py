# pylint: disable=C0114,C0116,W0621
import pathlib

from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_allele_score_from_resource,
    clip_span,
    clip_to_region,
    overlap_fractions_admit,
)
from gain.genomic_resources.testing.builders import an_allele_score


def test_a_record_overhanging_both_edges_is_clipped_to_the_region() -> None:
    assert clip_span(5, 20, 10, 15) == (10, 15)


def test_a_contained_record_passes_through_unchanged() -> None:
    assert clip_span(11, 14, 10, 15) == (11, 14)


def test_a_none_bound_means_unbounded_on_that_side() -> None:
    assert clip_span(5, 20, None, 15) == (5, 15)
    assert clip_span(5, 20, 10, None) == (10, 20)
    assert clip_span(5, 20, None, None) == (5, 20)


def test_a_record_ending_before_the_region_is_skipped() -> None:
    assert clip_span(2, 5, 10, 15) is None


def test_a_record_starting_past_the_region_is_refused_not_inverted() -> None:
    assert clip_span(20, 25, 10, 15) is None


def _multibase_allele_score(tmp_path: pathlib.Path) -> AlleleScore:
    # The first record spans ten bases, so a region query for [15, 25]
    # fetches it, while the point it collapses to (10) falls OUTSIDE the
    # window.  A count-weighted record counts once wherever that point
    # falls -- the region clip reads the window only where the weight
    # derives from the span.
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data(
            """
            chrom  pos_begin  pos_end  reference   alternative  s
            chr1   10         19       AGGGGGGGGG  A            0.1
            chr1   30         30       C           T            0.5
            """)
        .build_resource(tmp_path)
    )
    return build_allele_score_from_resource(resource)


def test_an_allele_point_outside_the_window_still_aggregates_once(
    tmp_path: pathlib.Path,
) -> None:
    with _multibase_allele_score(tmp_path).open() as score:
        assert score.aggregate_region(
            "chr1", 15, 25, [("s", "count")]) == [1]


def test_clip_to_region_clips_the_kept_segments_and_drops_the_rest() -> None:
    segments = [
        (2, 5, "before"),
        (5, 12, "left overhang"),
        (11, 14, "contained"),
        (14, 20, "right overhang"),
        (20, 25, "past"),
    ]

    clipped = list(clip_to_region(iter(segments), 10, 15))

    assert clipped == [
        (10, 12, "left overhang"),
        (11, 14, "contained"),
        (14, 15, "right overhang"),
    ]


def test_a_threshold_of_one_is_full_containment_of_the_side_it_names() -> None:
    # A 50-base record half inside a 100-base region: it covers half the
    # region and half of it is covered, so 1.0 refuses on either side.
    assert not overlap_fractions_admit(51, 100, 1, 100, 1.0, None)
    assert overlap_fractions_admit(51, 100, 1, 100, 0.5, None)
    assert overlap_fractions_admit(51, 100, 51, 100, 1.0, None)


def test_the_two_fractions_ask_about_different_lengths() -> None:
    # Ten bases inside a hundred: 0.1 of the region, all of the record.
    assert overlap_fractions_admit(11, 20, 1, 100, 0.1, 1.0)
    assert not overlap_fractions_admit(11, 20, 1, 100, 0.11, 1.0)


def test_every_threshold_supplied_must_hold() -> None:
    # Passes the region half, fails the record half.
    assert not overlap_fractions_admit(1, 200, 1, 100, 1.0, 1.0)
    assert overlap_fractions_admit(1, 200, 1, 100, 1.0, None)


def test_both_unset_admits_a_record_the_region_does_not_touch() -> None:
    # Not a judgement that the record belongs: with no threshold there is
    # nothing to judge it by, and a region query answering with this row
    # is a misconfigured table, refused at open() rather than here.
    assert overlap_fractions_admit(200, 300, 1, 100, None, None)


def test_a_zero_threshold_is_looser_than_it_looks() -> None:
    # 0 / length >= 0.0 holds, so 0.0 admits a record with no overlap at
    # all -- the one respect in which it is not "any overlap".
    assert overlap_fractions_admit(200, 300, 1, 100, 0.0, 0.0)


def test_an_exact_ratio_is_not_lost_to_floating_point() -> None:
    # One base of a three-base region is exactly 1/3.
    assert overlap_fractions_admit(1, 1, 1, 3, 1 / 3, None)


def test_a_single_base_region_and_record_are_whole_fractions() -> None:
    assert overlap_fractions_admit(7, 7, 7, 7, 1.0, 1.0)
