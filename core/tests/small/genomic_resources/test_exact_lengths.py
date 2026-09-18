# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The two accumulators behind :class:`ExactLengths` agree on the record.

The dict tally folds one distinct length at a time and is what the allele
scan uses; the array tally folds whole batches and is what a scan over
billions of segments needs.  Both freeze to the same record type, and a
file written from either must be indistinguishable -- which is what these
tests pin, along with the batch fold's own rules.
"""
import numpy as np
from gain.genomic_resources.statistics.exact_lengths import (
    LengthArrayTally,
    LengthTally,
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
