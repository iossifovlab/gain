# pylint: disable=C0114,C0116
"""The number histogram and the min/max fold under ONE numeric contract.

``non_numeric_error`` promises that a reader of a nullified score's reason
cannot tell which of the two reducers produced it.  That is only true if the
two refuse the same values and fold the rest to the same Python number.  The
slow path is one shared ``as_python_number``, but each twin still owns its
nan skip and the inline fast-path test that decides whether to call it, so
this is the test that breaks when one of them stops routing through the
rule, or drifts on the part it keeps (gain#1338, gain#1358).
"""
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue

#: The suffix that names the reducer is the ONE thing the two wordings may
#: differ in.
_REDUCER_SUFFIX = re.compile(r" to (number histogram|a min/max)$")


def _outcome(fold: Callable[[], Any]) -> tuple[str, str, str]:
    """What feeding one value did, in a form the twins can be compared by.

    A fold is reported as the type and repr of the extremum it left behind
    (repr, so that two nans -- a skipped ``None`` -- compare equal); a refusal
    as the wording with the reducer's name stripped.
    """
    try:
        folded = fold()
    except TypeError as err:
        return ("refused", "TypeError", _REDUCER_SUFFIX.sub("", str(err)))
    return ("folded", type(folded).__name__, repr(folded))


def _histogram_fold(value: Any) -> Callable[[], Any]:
    histogram = NumberHistogram(NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 10},
        "number_of_bins": 10,
    }))

    def fold() -> Any:
        histogram.add_value(value)
        return histogram.min_value
    return fold


def _min_max_fold(value: Any) -> Callable[[], Any]:
    min_max = MinMaxValue("s")

    def fold() -> Any:
        min_max.add_value(value)
        return min_max.min
    return fold


@pytest.mark.parametrize(
    "value",
    [
        # Folded, as themselves.
        0.5, 3, True, None,
        # Folded, as the Python value they hold.
        np.float32(0.5), np.float16(0.5), np.float64(0.5),
        np.int64(3), np.bool_(1),
        # Refused past the nan skip, by the type gate.
        0.5 + 0j, np.complex128(0.5), np.array(0.5), np.array([0.5]),
        # Refused at the nan skip.
        "aaa", np.str_("aaa"), Decimal("0.5"),
    ],
    ids=repr,
)
def test_the_histogram_and_the_min_max_fold_or_refuse_a_value_alike(
    value: Any,
) -> None:
    histogram_outcome = _outcome(_histogram_fold(value))
    min_max_outcome = _outcome(_min_max_fold(value))

    assert histogram_outcome == min_max_outcome
