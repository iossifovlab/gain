# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The two accumulators behind :class:`ExactLengths` agree on the record.

The dict tally folds one distinct length at a time and is what the allele
scan uses; the array tally folds whole batches and is what a scan over
billions of segments needs.  Both freeze to the same record type, and a
file written from either must be indistinguishable -- which is what these
tests pin, along with the batch fold's own rules.
"""
import numpy as np
import pytest
from gain.genomic_resources.statistics.exact_lengths import (
    LENGTH_MAP_CLAMP,
    ExactLengths,
    LengthArrayTally,
    LengthTally,
    length_ladder,
)
from gain.genomic_resources.statistics.length_histogram import (
    LENGTH_HISTOGRAM_BIN_COUNT,
    LENGTH_HISTOGRAM_DISPLAY_CAP,
    length_histogram_bin_index,
)

# Lengths on both sides of the clamp, with repeats, so the record's map
# has a shared key below the clamp and an overflow bucket that two
# different lengths fold into.
_LENGTHS = [1, 2, 2, 3, 8191, 8192, 8200, 40_000]


def _dict_tally(lengths: list[int]) -> LengthTally:
    tally = LengthTally()
    for length in lengths:
        tally.add(length, 1)
    return tally


def test_the_array_tally_freezes_to_the_record_the_dict_tally_does() -> None:
    expected = _dict_tally(_LENGTHS).frozen()

    # Fed in two batches through two tallies and merged, so the merge
    # is under the same assertion as the fold.
    first = LengthArrayTally()
    first.add_batch(np.array(_LENGTHS[:4]))
    second = LengthArrayTally()
    second.add_batch(np.array(_LENGTHS[4:]))
    first.merge(second)

    assert first.frozen() == expected


def test_the_array_tally_folds_one_length_at_a_time_like_the_dict_tally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scalar path (gain#1543): a segment scan meets most of its
    lengths one run at a time, and ``add`` must land each where
    ``add_batch`` would have -- including past the clamp, where the
    counter is the overflow bucket but the scalars keep the true
    length.  It is also pinned NOT to route through ``add_batch``: a
    one-element bincount per segment is the microsecond-scale cost the
    scalar path exists to avoid, on scores with billions of segments."""
    def no_vector_call(*_: object, **__: object) -> None:
        raise AssertionError("add() must make no numpy vector call")
    monkeypatch.setattr(LengthArrayTally, "add_batch", no_vector_call)
    monkeypatch.setattr(np, "bincount", no_vector_call)
    monkeypatch.setattr(np, "minimum", no_vector_call)
    expected = _dict_tally(_LENGTHS).frozen()

    tally = LengthArrayTally()
    for length in _LENGTHS:
        tally.add(length)

    assert tally.frozen() == expected


def test_the_array_tally_freezes_to_plain_python_ints() -> None:
    """Equality is not enough: ``np.int64(1) == 1`` but only one of them
    serialises.  The file writer hands the record to ``json``, so every
    number in it has to be a Python ``int``."""
    tally = LengthArrayTally()
    tally.add_batch(np.array(_LENGTHS))

    record = tally.frozen()

    assert {type(v) for v in (record.total, record.sum, record.min,
                              record.max)} == {int}
    assert {type(k) for k in record.lengths} == {int}
    assert {type(v) for v in record.lengths.values()} == {int}


def test_lengths_past_the_clamp_share_the_overflow_bucket() -> None:
    tally = LengthArrayTally()

    tally.add_batch(np.array([LENGTH_MAP_CLAMP, 8200, 40_000]))

    record = tally.frozen()
    assert record.lengths == {LENGTH_MAP_CLAMP: 3}
    # The scalars are taken on the UNCLAMPED lengths: a max of 40,000
    # is the whole reason they are stored beside the map.
    assert (record.total, record.sum, record.min, record.max) \
        == (3, LENGTH_MAP_CLAMP + 8200 + 40_000, LENGTH_MAP_CLAMP, 40_000)


@pytest.mark.parametrize("bad_length", [0, -1])
def test_a_batch_with_a_length_below_one_is_refused_whole(
    bad_length: int,
) -> None:
    """A 0 would land in counter 0 -- which is not a length -- and a
    negative one would make ``bincount`` raise its own error halfway
    through the fold.  Both are refused BEFORE anything is folded, so the
    tally is exactly what it was."""
    tally = LengthArrayTally()
    tally.add_batch(np.array([1, 2]))
    before = tally.frozen()

    with pytest.raises(ValueError, match="length must be positive"):
        tally.add_batch(np.array([3, bad_length]))

    assert tally.frozen() == before


def test_an_empty_batch_leaves_the_tally_as_it_was() -> None:
    tally = LengthArrayTally()

    tally.add_batch(np.array([], dtype=np.int64))

    assert tally.frozen() == LengthTally().frozen()


def test_the_chart_ladder_derived_from_the_record_is_the_stored_one(
) -> None:
    """The one place the ladder survives is the chart, drawn from the
    record rather than from a stored histogram (gain#1118, gain#1543,
    gain#1544).  For that to change no pixel, the derived ladder must
    equal what a scan used to bin: here four lengths in three bins, two
    of them past the clamp -- folded to one key in the map, yet the same
    bin as before, because the clamp is the bin the plot already sums
    everything above into."""
    lengths = [3, 100, 9000, 9500]
    stored_ladder = [0] * LENGTH_HISTOGRAM_BIN_COUNT
    for length in lengths:
        stored_ladder[length_histogram_bin_index(length)] += 1
    tally = LengthArrayTally()

    tally.add_batch(np.array(lengths, dtype=np.int64))

    record = tally.frozen()
    assert record.lengths == {3: 1, 100: 1, LENGTH_MAP_CLAMP: 2}
    assert record.max == 9500
    assert sum(map(bool, stored_ladder)) == 3
    assert length_ladder(record) == stored_ladder


def test_the_clamp_never_falls_below_the_charts_display_cap() -> None:
    """The chart's bins are derived from the clamped map.  A clamp below
    the display cap would leave bins between the two to be drawn from
    lengths the map had already folded away -- so raising the cap means
    raising the clamp first, and that is a stored-format change."""
    assert LENGTH_MAP_CLAMP >= LENGTH_HISTOGRAM_DISPLAY_CAP


def test_a_stored_record_restores_into_the_array_tally_and_merges_on() -> None:
    """The roll-up of a region already in the file with one just scanned:
    the stored record comes back as a tally, the scanned batch folds on
    top, and the result is what one tally fed everything would hold."""
    stored = _dict_tally([2, 3, 40_000]).frozen()

    tally = LengthArrayTally.restored(stored)
    tally.add_batch(np.array([1, 8200]))

    assert tally.frozen() == _dict_tally([2, 3, 40_000, 1, 8200]).frozen()


def test_a_stored_key_above_the_clamp_is_refused_by_name() -> None:
    """A file whose map was built under a larger clamp has keys the array
    has no counter for.  The dict tally would carry them silently; the
    array tally cannot, and says which key rather than failing on an
    array index."""
    foreign = ExactLengths({LENGTH_MAP_CLAMP + 1: 1}, 1, 8193, 8193, 8193)

    with pytest.raises(ValueError, match="above the clamp"):
        LengthArrayTally.restored(foreign)


def test_merging_an_empty_array_tally_changes_nothing() -> None:
    tally = LengthArrayTally()
    tally.add_batch(np.array([2, 40_000]))
    before = tally.frozen()

    tally.merge(LengthArrayTally())

    assert tally.frozen() == before
