# pylint: disable=W0621
from gain.genomic_resources.genomic_scores import clip_span, clip_to_region


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
