# pylint: disable=too-many-lines
"""Handling of genomic scores statistics.

Currently we support only genomic scores histograms.
"""
from __future__ import annotations

import gzip
import importlib.util
import io
import json
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass
from typing import IO, Any

import numpy as np
import yaml
from matplotlib import ticker

from gain import logging
from gain.genomic_resources.dvc import DVC_SUFFIX
from gain.genomic_resources.repository import (
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.resource_errors import HistogramError
from gain.genomic_resources.statistics.base_statistic import (
    NON_NUMERIC_ERRORS,
    PYTHON_NUMBER_TYPES,
    Statistic,
    as_python_number,
    non_numeric_error,
)
from gain.genomic_resources.statistics.chart_style import (
    CHART_FIGSIZE,
    CHART_LABEL_FONT_SIZE,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.genomic_resources.statistics.moments import Moments, MomentsSummary

logger = logging.getLogger(__name__)


#: Which score value types a NUMBER histogram can accumulate, one value at a
#: time.  ``bool`` is in: ``numpy`` folds it as 0/1, and a two-bin histogram
#: over a flag is meaningful.  ``str`` is not, and a resource pairing the two
#: aborted its entire statistics build in ``np.isnan`` (gain#1285); since
#: gain#1336 that pairing is refused when the score is CONSTRUCTED, by
#: :func:`~gain.genomic_resources.score_resource.refuse_unfoldable_histograms`,
#: which is the only thing that reads this set.
#:
#: It lives here, with the config whose acceptance it describes, rather than
#: in the statistics scan that used to own it -- the refusal moved to the
#: score layer, and the score layer must not import the scan to ask what a
#: histogram can fold.
#:
#: Deliberately WIDER than the scan's ``_BULK_HISTOGRAM_VALUE_TYPES``, and
#: the two are different questions rather than one rule stated twice: this
#: asks what a histogram can fold value by value, that asks what it can fold
#: a whole column of (a bulk read yields a number histogram's column as
#: ``float64``, which a ``bool`` score's column is not).  Merging them would
#: widen the vectorized path to a type it cannot read.
NUMBER_HISTOGRAM_VALUE_TYPES = ("float", "int", "bool")


@dataclass
class NumberHistogramConfig:
    """Configuration of a number histogram.

    Attributes:
        view_range (tuple[float | None, float | None]): The low and the
            high edge of the binned range. ``None`` at either end means
            the edge is not pinned yet. The histogram needs both edges.

        number_of_bins (int): The number of bars. The default is 100.

        x_log_scale (bool): Whether the bins are spaced on a log scale.
            A log scale requires ``x_min_log``.

        y_log_scale (bool): Whether the plot draws the bar heights on a
            log scale. The bin counts are the same.

        x_min_log (float | None): The high edge of the first bin on a log
            scale. ``None`` when the scale is linear.

        plot_function (str | None): The ``file:function`` name of a custom
            plot function. ``None`` means the built-in plot.
    """

    view_range: tuple[float | None, float | None]
    number_of_bins: int = 100
    x_log_scale: bool = False
    y_log_scale: bool = False
    x_min_log: float | None = None
    plot_function: str | None = None

    def has_view_range(self) -> bool:
        """Whether both ends of the view range are pinned.

        Returns
        -------
        bool
            True when neither edge of ``view_range`` is ``None``. False
            means the config still needs the minimum and the maximum of
            the score before a histogram can be built from it.
        """
        # A histogram can only be built once its bin edges are known, so a
        # config whose range is half-open still needs the score's min/max
        # before it can be used.
        return self.view_range[0] is not None and \
            self.view_range[1] is not None

    def to_dict(self) -> dict[str, Any]:
        """Transform number histogram config to dict.

        Returns
        -------
        dict[str, Any]
            The mapping that ``from_dict`` reads back. The key
            ``plot_function`` is present only when the config sets one.
        """
        result = {
            "type": "number",
            "view_range": {
                "min": self.view_range[0],
                "max": self.view_range[1],
            },
            "number_of_bins": self.number_of_bins,
            "x_log_scale": self.x_log_scale,
            "y_log_scale": self.y_log_scale,
            "x_min_log": self.x_min_log,
        }
        if self.plot_function is not None:
            result["plot_function"] = self.plot_function

        return result

    @staticmethod
    def from_dict(parsed: dict[str, Any]) -> NumberHistogramConfig:
        """Build a number histogram config from a parsed yaml file.

        Parameters
        ----------
        parsed : dict[str, Any]
            The ``histogram:`` mapping of a score. Its ``type`` must be
            ``number``. A missing key takes the default of the field.

        Returns
        -------
        NumberHistogramConfig
            The config. A missing ``view_range`` gives ``(None, None)``.

        Raises
        ------
        TypeError
            If ``type`` is not ``number``.
        """
        hist_type = parsed.get("type")
        if hist_type != "number":
            logger.error(
                "Invalid configuration type (%s)"
                " for number histogram!\n%s",
                hist_type, parsed,
            )
            raise TypeError(
                "Invalid configuration for number histogram!\n"
                f"{parsed}",
            )
        yaml_range = parsed.get("view_range", {})
        x_min = yaml_range.get("min", None)
        x_max = yaml_range.get("max", None)
        view_range = (x_min, x_max)
        number_of_bins = parsed.get("number_of_bins", 100)
        x_log_scale = parsed.get("x_log_scale", False)
        y_log_scale = parsed.get("y_log_scale", False)
        x_min_log = parsed.get("x_min_log")
        plot_function = parsed.get("plot_function")

        return NumberHistogramConfig(
            view_range, number_of_bins,
            x_log_scale, y_log_scale,
            x_min_log,
            plot_function=plot_function,
        )

    @staticmethod
    def default_config(
        min_max: MinMaxValue | None,
    ) -> NumberHistogramConfig:
        """Build the config of a score that declares no ``histogram``.

        Parameters
        ----------
        min_max : MinMaxValue | None
            The observed minimum and maximum of the score. ``None`` means
            they are not known yet.

        Returns
        -------
        NumberHistogramConfig
            A config with 100 linear bins. The view range is the observed
            range. It is ``(None, None)`` when ``min_max`` is ``None``.
            When the minimum equals the maximum, the range is the minimum
            to the minimum plus one.
        """
        if min_max is None:
            view_range: tuple[float | None, float | None] = (None, None)
        elif min_max.min == min_max.max:
            view_range = (min_max.min, min_max.min + 1.0)
        else:
            view_range = (min_max.min, min_max.max)
        number_of_bins = 100
        x_log_scale = False
        y_log_scale = False
        return NumberHistogramConfig(
            view_range, number_of_bins, x_log_scale, y_log_scale)


DEFAULT_DISPLAYED_VALUES_COUNT = 20

#: The gzip level and suffix of a full histogram stored gzipped (ADR 0032).
GZIPPED_HISTOGRAM_COMPRESSLEVEL = 6
GZIPPED_JSON_SUFFIX = ".json.gz"


@dataclass
class CategoricalHistogramConfig:
    """Configuration of a categorical histogram.

    Attributes:
        displayed_values_count (int | None): The number of values that the
            summary page shows. The default is 20. ``None`` means no
            limit, when ``displayed_values_percent`` is set.

        displayed_values_percent (float | None): Show the most common
            values until they cover this percent of the counts. ``None``
            means the count rule applies.

        value_order (list[str | int] | None): The values to show, in this
            order. ``None`` or an empty list means the order is by count.

        y_log_scale (bool): Whether the plot draws the counts on a log
            scale.

        label_rotation (int): The rotation of the x labels, in degrees.

        plot_function (str | None): The ``file:function`` name of a custom
            plot function. ``None`` means the built-in plot.

        enforce_type (bool): False means the histogram refuses more than
            ``CategoricalHistogram.UNIQUE_VALUES_LIMIT`` distinct values.
            True means there is no limit. The flag name is the reverse
            of its effect.

        natural_order (bool): Order the displayed values by value, numbers
            before text, and not by count.

        allow_only_whole_values_y (bool): Whether the plot uses whole
            numbers for the y ticks.
    """
    displayed_values_count: int | None = 20
    displayed_values_percent: float | None = None
    value_order: list[str | int] | None = None
    y_log_scale: bool = False
    label_rotation: int = 0
    plot_function: str | None = None
    enforce_type: bool = True
    natural_order: bool = False
    allow_only_whole_values_y: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Transform categorical histogram config to dict.

        Returns
        -------
        dict[str, Any]
            The mapping that ``from_dict`` reads back. The keys
            ``displayed_values_count``, ``displayed_values_percent`` and
            ``plot_function`` are present only when they differ from the
            default.
        """
        result: dict[str, Any] = {
            "type": "categorical",
            "value_order": self.value_order,
            "y_log_scale": self.y_log_scale,
            "label_rotation": self.label_rotation,
        }
        if self.displayed_values_count != DEFAULT_DISPLAYED_VALUES_COUNT:
            result["displayed_values_count"] = self.displayed_values_count
        if self.displayed_values_percent is not None:
            result["displayed_values_percent"] = \
                self.displayed_values_percent
        if self.plot_function is not None:
            result["plot_function"] = self.plot_function
        return result

    @staticmethod
    def default_config() -> CategoricalHistogramConfig:
        """Build the config of a score that declares no ``histogram``.

        Returns
        -------
        CategoricalHistogramConfig
            A config that enforces ``CategoricalHistogram.UNIQUE_VALUES_LIMIT``.
        """
        # ``enforce_type=False`` here does NOT relax value-type checking --
        # CategoricalHistogram.add_value refuses a non-``str``/``int``
        # value either way.  What the flag gates is
        # CategoricalHistogram.UNIQUE_VALUES_LIMIT, and it reads
        # backwards: it is the *default* config that enforces the limit, so a
        # score nobody declared categorical cannot silently histogram
        # thousands of distinct values.  A curator who writes an explicit
        # ``categorical`` block has asserted the score really is categorical,
        # and that config carries no limit.
        return CategoricalHistogramConfig(enforce_type=False)

    @staticmethod
    def from_dict(parsed: dict[str, Any]) -> CategoricalHistogramConfig:
        """Create categorical histogram config from a configuration dict.

        Parameters
        ----------
        parsed : dict[str, Any]
            The ``histogram:`` mapping of a score. Its ``type`` must be
            ``categorical``. When it sets neither
            ``displayed_values_count`` nor ``displayed_values_percent``,
            the count is 20.

        Returns
        -------
        CategoricalHistogramConfig
            The config. It does not enforce the unique values limit.

        Raises
        ------
        TypeError
            If ``type`` is not ``categorical``.
        ValueError
            If both ``displayed_values_count`` and
            ``displayed_values_percent`` are set.
        """
        hist_type = parsed.get("type")
        if hist_type != "categorical":
            raise TypeError(
                "Invalid configuration type for categorical histogram!\n"
                f"{parsed}",
            )
        displayed_values_count = parsed.get(
            "displayed_values_count")
        displayed_values_percent = parsed.get(
            "displayed_values_percent")
        if displayed_values_count is not None \
                and displayed_values_percent is not None:
            raise ValueError(
                "Invalid configuration for categorical histogram: "
                "displayed_values_count and displayed_values_percent "
                "cannot be both set\n"
                f"{parsed}",
            )

        if displayed_values_percent is None and displayed_values_count is None:
            displayed_values_count = DEFAULT_DISPLAYED_VALUES_COUNT

        value_order = parsed.get("value_order", [])
        y_log_scale = parsed.get("y_log_scale", False)
        plot_function = parsed.get("plot_function")
        label_rotation = parsed.get("label_rotation", 0)

        return CategoricalHistogramConfig(
            displayed_values_count=displayed_values_count,
            displayed_values_percent=displayed_values_percent,
            value_order=value_order,
            y_log_scale=y_log_scale,
            plot_function=plot_function,
            label_rotation=label_rotation,
            enforce_type=True,
        )


@dataclass
class NullHistogramConfig:
    """Configuration of a null histogram.

    Attributes:
        reason (str): Why the score has no histogram. The reason is
            required, so a summary page can always explain a null
            histogram.
    """

    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Render this config as the mapping ``from_dict`` reads back.

        Returns
        -------
        dict[str, Any]
            The mapping with the keys ``type`` and ``reason``.
        """
        return {
            "type": "null",
            "reason": self.reason,
        }

    @staticmethod
    def default_config() -> NullHistogramConfig:
        """Build a null config for a caller with no reason to give.

        Returns
        -------
        NullHistogramConfig
            A config with the reason ``Unspecified reason``.
        """
        # ``reason`` is required rather than optional, so that every null
        # histogram on a summary page can say why it is null; this is the
        # placeholder for the few call sites that genuinely have nothing to
        # add.
        return NullHistogramConfig("Unspecified reason")

    @staticmethod
    def from_dict(parsed: dict[str, Any]) -> NullHistogramConfig:
        """Create a null histogram config from a configuration dict.

        Parameters
        ----------
        parsed : dict[str, Any]
            The ``histogram:`` mapping of a score. Its ``type`` must be
            ``null``.

        Returns
        -------
        NullHistogramConfig
            The config. A missing ``reason`` gives ``Unspecified reason``.

        Raises
        ------
        TypeError
            If ``type`` is not ``null``.
        """
        hist_type = parsed.get("type")
        if hist_type != "null":
            raise TypeError(
                "Invalid configuration type for null histogram!\n"
                f"{parsed}",
            )
        reason = parsed.get("reason", "Unspecified reason")

        return NullHistogramConfig(
            reason=reason,
        )


class NumberHistogram(Statistic):
    """A histogram of numeric values in bins.

    Parameters
    ----------
    config : NumberHistogramConfig
        The configuration. Its view range must have both edges set.
    bins : np.ndarray | None
        The bin edges, one more than the bars. Pass both ``bins`` and
        ``bars`` or neither.
    bars : np.ndarray | None
        The count of each bin.

    Raises
    ------
    ValueError
        If the view range has an unset or NaN edge, if a log scale has no
        ``x_min_log``, or if only one of ``bins`` and ``bars`` is given.

    Attributes:
        type (str): The type name that a stored file carries,
            ``number_histogram``.

        config (NumberHistogramConfig): The configuration.

        bins (np.ndarray): The bin edges. There is one more edge than
            there are bars.

        bars (np.ndarray): The count of each bin. Bar ``i`` counts the
            values from ``bins[i]`` up to ``bins[i + 1]``.

        out_of_range_bins (list[int]): The counts of the values below and
            of the values above the view range.

        min_value (float): The smallest value that was added. NaN when
            there is none.

        max_value (float): The largest value that was added. NaN when
            there is none.

        view_range (tuple[float, float]): The low and the high edge of the
            binned range.
    """

    type = "number_histogram"

    def __init__(
            self, config: NumberHistogramConfig,
            bins: np.ndarray | None = None,
            bars: np.ndarray | None = None):
        super().__init__("histogram", "Collects values for histogram.")
        logger.debug("number histogram config: %s", config)

        assert isinstance(config, NumberHistogramConfig)
        self.config = config
        self.out_of_range_values: list[float] = []
        self.out_of_range_bins: list[int] = [0, 0]
        # ``None`` for a histogram loaded from a file that predates the
        # accumulators: unknown, never empty (gain#1589).
        self._moments: Moments | None = Moments()

        self.min_value: float = np.nan
        self.max_value: float = np.nan
        self.choose_bin_index = self.choose_bin_lin
        if self.config.x_log_scale:
            self.choose_bin_index = self.choose_bin_log

        if self.config.x_log_scale and self.config.x_min_log is None:
            raise ValueError(
                "Invalid histogram configuration, missing x_min_log",
            )
        if self.config.view_range[0] is None or \
                self.config.view_range[1] is None or \
                np.isnan(self.config.view_range[0]) or \
                np.isnan(self.config.view_range[1]):
            logger.error(
                "unexpected min/max value: [%s, %s]",
                self.config.view_range[0], self.config.view_range[1])
            raise ValueError(
                "unexpected min/max value:"
                f"[{self.config.view_range[0]}, "
                f"{self.config.view_range[1]}]")

        self.view_range: tuple[float, float] = (
            self.config.view_range[0], self.config.view_range[1])

        if bins is not None and bars is not None:
            self.bins = bins
            self.bars = bars
        elif bins is None and bars is None:
            if self.config.x_log_scale:
                assert self.config.x_min_log is not None
                self.bins = np.array([
                    self.config.view_range[0],
                    * np.logspace(
                        np.log10(self.config.x_min_log),
                        np.log10(self.config.view_range[1]),
                        self.config.number_of_bins,
                    )])
                self._rstep = (self.config.number_of_bins - 1) / \
                    (np.log10(self.view_max())
                     - np.log10(self.config.x_min_log))
            else:
                self.bins = np.linspace(
                    self.config.view_range[0],
                    self.config.view_range[1],
                    self.config.number_of_bins + 1,
                )
                if (self.view_max() - self.view_min()) <= 0:
                    self._rstep = 0
                else:
                    self._rstep = self.config.number_of_bins / \
                        (self.view_max() - self.view_min())
            self.bars = np.zeros(self.config.number_of_bins, dtype=np.int64)
            assert not np.any(np.isnan(self.bins)), ("nan bins", self.config)
        elif self.bins is None or self.bars is None:
            raise ValueError(
                "Cannot instantiate histogram with only bins or only bars!",
            )

    def view_min(self) -> float:
        """The low edge of the first bin.

        Returns
        -------
        float
            The low edge of the view range. Values below it are counted
            in ``out_of_range_bins`` and not binned. It is not the
            smallest value that was added.
        """
        return self.view_range[0]

    def view_max(self) -> float:
        """The high edge of the last bin.

        Returns
        -------
        float
            The high edge of the view range. Values above it are counted
            in ``out_of_range_bins`` and not binned.
        """
        return self.view_range[1]

    def merge(self, other: NumberHistogram) -> None:
        """Add the counts of another histogram into this one.

        Parameters
        ----------
        other : NumberHistogram
            A histogram with the same bin edges. This method changes only
            ``self``.

        Raises
        ------
        AssertionError
            If the bin edges of the two histograms differ.
        """
        assert isinstance(other, NumberHistogram)
        assert self.bins is not None
        assert self.bars is not None
        assert other.bins is not None
        assert other.bars is not None

        assert np.allclose(self.bins, other.bins, rtol=1e-5), \
            (self.bins, other.bins)

        self.bars += other.bars
        self.out_of_range_bins[0] += other.out_of_range_bins[0]
        self.out_of_range_bins[1] += other.out_of_range_bins[1]
        if np.isnan(self.min_value):
            self.min_value = min(other.min_value, self.min_value)
        else:
            self.min_value = min(self.min_value, other.min_value)
        if np.isnan(self.max_value):
            self.max_value = max(other.max_value, self.max_value)
        else:
            self.max_value = max(self.max_value, other.max_value)

        # Unknown on either side is unknown for the whole: a partial sum
        # would read as the sum of everything the bars count.
        theirs = other.moments
        if self._moments is None or theirs is None:
            self._moments = None
        else:
            self._moments.merge(theirs)

    @property
    def moments(self) -> Moments | None:
        """The count, sum and sum of squares, or ``None`` when unknown."""
        # ``None`` for a histogram loaded from a file that predates the
        # accumulators: unknown, never empty.
        return self._moments

    @property
    def count(self) -> int | None:
        """The weighted count of the added values, or ``None`` when unknown."""
        # The same weighting as the bars, in the unit the score names as
        # its ``HISTOGRAM_COUNT_UNIT`` -- so this is ``bars.sum()`` plus
        # both out-of-range counts.
        return None if self._moments is None else self._moments.count

    @property
    def mean(self) -> float | None:
        """The weighted mean, or ``None`` when unknown or nothing was added."""
        return None if self._moments is None else self._moments.mean

    @property
    def std(self) -> float | None:
        """The population standard deviation, or ``None`` when ``mean`` is."""
        return None if self._moments is None else self._moments.std

    def moments_summary(self) -> MomentsSummary | None:
        """The count, mean and standard deviation, as text for the page.

        Returns
        -------
        MomentsSummary | None
            Three ``(label, text)`` pairs: ``n``, ``mean`` and ``sd``.
            ``None`` when the numbers are unknown or nothing was added.
        """
        # ``None`` rather than a guess, so the page leaves the cell empty.
        return None if self._moments is None else self._moments.summary()

    def values_domain(self) -> str:
        """The observed value range, as text for the summary page.

        Returns
        -------
        str
            The smallest and the largest added value, as ``[min, max]``.
            It is not the view range.
        """
        return f"[{self.min_value:0.3g}, {self.max_value:0.3g}]"

    def add_value(
        self, value: float | np.generic | None, count: int = 1,
    ) -> None:
        """Add a value to the histogram.

        Parameters
        ----------
        value : float | np.generic | None
            The value. ``None`` and NaN are skipped and nothing is counted.
        count : int
            The weight of the value.

        Raises
        ------
        TypeError
            If the value is not a real number, for example text.
        """
        # ``np.generic`` is in the signature because a numpy scalar is a real
        # caller's value, not a curiosity (gain#1338).
        #
        # ``np.isnan`` is what refuses a value it cannot read as a number,
        # and it raises BEFORE the allow-list below can be reached -- which
        # is why that refusal, written for exactly the ``str`` case, could
        # never fire for it, and a nullified score's reason read ``ufunc
        # 'isnan' not supported`` (gain#1312).  Re-wording numpy's complaint
        # is the whole fix; the skip itself is untouched, so ``None`` is
        # still an NA cell rather than a contract breach.
        #
        # Catching beats pre-checking the type here, and measurably: this
        # runs per value of every record, and an ``isinstance`` ahead of the
        # skip costs it ~24% (``str | bytes`` builds a union object on every
        # call), where a try/except that does not fire costs ~3%.
        try:
            if value is None or np.isnan(value):
                return
        except NON_NUMERIC_ERRORS as err:
            raise non_numeric_error(value, "number histogram") from err

        # Reached only by values ``np.isnan`` accepted -- text and
        # ``Decimal`` never get this far, the skip above refuses those.  A
        # Python number folds as-is; everything else goes through the rule
        # the min/max twin shares, which says why a numpy scalar folds as
        # ``item()`` and why a complex or an array does not (gain#1338,
        # gain#1358).  The isinstance stays inline because this runs per
        # value of every record and a Python float is what the scan folds.
        if not isinstance(value, PYTHON_NUMBER_TYPES):
            value = as_python_number(value, "number histogram")

        self.min_value = min(value, self.min_value)
        self.max_value = max(value, self.max_value)

        # Costs this arm ~10% (1.65 -> 1.82 us/value over 100k values),
        # the same as the three updates inlined -- the call is not what
        # is paid for.  Worn here because this arm is the fallback; the
        # scan's hot path is :meth:`add_batch`.
        if self._moments is not None:
            self._moments.add(value, count)

        index = self.choose_bin_index(value)
        if index < 0:
            logger.warning(
                "out of range %s value %s", self.view_range, value)
            tindex = index + 2
            self.out_of_range_bins[tindex] += count
            return

        self.bars[index] += count

    def add_batch(
        self, values: np.ndarray, weights: np.ndarray,
    ) -> None:
        """Add a batch of ``(value, weight)`` pairs, vectorized.

        The result is the same as calling ``add_value`` for each pair in
        order. The sums behind ``mean`` and ``std`` agree only to rounding.

        Parameters
        ----------
        values : np.ndarray
            The values. NaN values are skipped. The array is converted to
            ``float64``.
        weights : np.ndarray
            The weight of each value, of the same length as ``values``.
        """
        # Bit-for-bit equivalent to calling ``add_value`` over each pair in
        # order: the same bin selection for both x-scales (the truncation of
        # ``choose_bin_lin`` / ``choose_bin_log`` and the clamp to
        # ``number_of_bins - 1``), the same below/above ``out_of_range_bins``
        # split, the same ``min_value``/``max_value`` tracking, and the same
        # nan-skip.  This is the hot path for the statistics scan, where a
        # per-value Python call dominates the cost.
        #
        # The one thing NOT bit-for-bit is the accumulators behind
        # ``count`` / ``mean`` / ``std``: ``count`` is, and the
        # two sums agree to rounding -- see ``Moments.add_batch``.
        #
        # The equivalence covers dtype as well as arithmetic: the ``float64``
        # coercion below is what ``add_value`` reproduces by normalizing a
        # numpy scalar, so a narrow column -- ``float32``, ``float16``,
        # ``bool`` -- folds the same through either arm (gain#1338).
        #
        # Where it does not hold: this arm also folds values ``add_value``
        # refuses outright, because ``asarray`` converts them silently -- a
        # ``complex`` loses its imaginary part, and an object array of
        # ``Decimal`` converts.  Nothing in a scan produces either; they are
        # reachable only by calling this directly.
        #
        # Both x-scales are vectorized.  The log one is bit-exact for the same
        # reason the linear one is and one more: ``np.log10`` returns the same
        # float for a scalar as for that scalar inside an array, at every array
        # width numpy dispatches differently on -- checked over many decades by
        # test_add_batch_matches_add_value_loop_log_fuzz, which varies batch
        # size precisely to cover numpy's scalar-loop and SIMD kernels.
        values = np.asarray(values, dtype=np.float64)
        weights = np.asarray(weights, dtype=np.int64)

        finite = ~np.isnan(values)
        if not finite.any():
            return
        values = values[finite]
        weights = weights[finite]

        # ``add_value`` seeds min/max at nan and folds each value in with
        # ``min(value, self.min_value)``; over a set that is just the extremum.
        batch_min = float(values.min())
        batch_max = float(values.max())
        self.min_value = batch_min if np.isnan(self.min_value) \
            else min(self.min_value, batch_min)
        self.max_value = batch_max if np.isnan(self.max_value) \
            else max(self.max_value, batch_max)

        # Over EVERY finite value, out-of-range ones included, at its real
        # value -- as ``add_value`` folds it before it decides the bin.
        if self._moments is not None:
            self._moments.add_batch(values, weights)

        below = values < self.view_min()
        above = values > self.view_max()
        self.out_of_range_bins[0] += int(weights[below].sum())
        self.out_of_range_bins[1] += int(weights[above].sum())

        in_range = ~(below | above)
        idx = self._bin_indices(values[in_range])
        # Clamp to the last bin, exactly as choose_bin_lin/choose_bin_log do.
        np.minimum(idx, self.config.number_of_bins - 1, out=idx)
        np.add.at(self.bars, idx, weights[in_range])

    def _bin_indices(self, values: np.ndarray) -> np.ndarray:
        """Vectorized, unclamped bin index for values already known in range.

        The array counterpart of :meth:`choose_bin_lin` /
        :meth:`choose_bin_log`, minus the below/above tests its caller has
        already applied.  Both scalar forms truncate with ``int(...)``, which
        rounds toward zero; the offsets here are non-negative, so that is
        floor, which ``astype(np.int64)`` reproduces.
        """
        if not self.config.x_log_scale:
            linear: np.ndarray = (
                (values - self.view_min()) * self._rstep).astype(np.int64)
            return linear

        assert self.config.x_min_log is not None
        x_min_log = self.config.x_min_log
        # choose_bin_log puts everything under x_min_log in bin 0 -- the bin
        # spanning view_min..x_min_log, which is why the log formula's index
        # starts at 1 -- and log-bins the rest.  Zeros give the first case for
        # free, so only the second is computed.
        indices = np.zeros(values.shape, dtype=np.int64)
        logged = values >= x_min_log
        indices[logged] = ((
            np.log10(values[logged]) - np.log10(x_min_log))
            * self._rstep).astype(np.int64) + 1
        return indices

    def choose_bin_lin(self, value: float) -> int:
        """Compute bin index for a passed value for linear x-scale.

        :meta private:
        """
        if value < self.view_min():
            return -2
        if value > self.view_max():
            return -1
        index = int((value - self.view_min()) * self._rstep)
        return min(index, self.config.number_of_bins - 1)

    def choose_bin_log(self, value: float) -> int:
        """Compute bin index for a passed value for log x-scale.

        :meta private:
        """
        assert self.config.x_log_scale
        assert self.config.x_min_log is not None

        if value < self.view_min():
            return -2
        if value > self.view_max():
            return -1

        if value < self.config.x_min_log:
            return 0

        index = int(
            (np.log10(value) - np.log10(self.config.x_min_log))
            * self._rstep) + 1
        return min(index, self.config.number_of_bins - 1)

    def to_dict(self) -> dict[str, Any]:
        """Render this histogram as the mapping ``from_dict`` reads back.

        Returns
        -------
        dict[str, Any]
            A mapping that JSON can store as it is. The bin edges and the
            counts are lists.
        """
        # Bin edges and bar counts are carried as plain lists rather than
        # arrays, so the result is JSON-serialisable as it stands.
        return {
            "config": self.config.to_dict(),
            "bins": self.bins.tolist(),
            "bars": self.bars.tolist(),
            "out_of_range_bins": self.out_of_range_bins,
            "min_value": float(self.min_value),
            "max_value": float(self.max_value),
            # The accumulators, never the mean or sd derived from them.
            **Moments.stored(self._moments),
        }

    def serialize(self) -> str:
        """Render this histogram as the JSON stored in the resource.

        Returns
        -------
        str
            The JSON text.
        """
        return json.dumps(self.to_dict(), indent=2)

    def plot(
        self,
        outfile: IO,
        score_id: str,
        y_axis_label: str | None = None,
        small_values_description: str | None = None,
        large_values_description: str | None = None,
    ) -> None:
        """Plot the histogram and write the image into ``outfile``.

        Parameters
        ----------
        outfile : IO
            A binary file object that receives the image.
        score_id : str
            The score name. It is the x axis label.
        y_axis_label : str | None
            The y axis label. ``None`` means ``count``.
        small_values_description : str | None
            The text under the low end of the x axis. The plot shows both
            descriptions or neither.
        large_values_description : str | None
            The text under the high end of the x axis.
        """
        # pylint: disable=import-outside-toplevel
        import matplotlib
        matplotlib.use("agg")
        import matplotlib.pyplot as plt
        width = self.bins[1:] - self.bins[:-1]

        fig, ax = plt.subplots(figsize=CHART_FIGSIZE, tight_layout=True)
        ax.bar(
            x=self.bins[:-1], height=self.bars,
            log=self.config.y_log_scale,
            width=width,
            align="edge")

        if self.config.x_log_scale:
            ax.set_xscale("log")

        if (
            small_values_description is not None
            and large_values_description is not None
        ):
            sec = ax.secondary_xaxis(location=0)

            if self.config.x_log_scale:
                left_location = self.bins[1]
            else:
                left_location = self.bins[0]
            right_location = self.bins[-1]

            sec.set_ticks(
                [
                    left_location,
                    right_location,
                ],
                labels=[
                    f"\n{small_values_description}",
                    f"\n{large_values_description}",
                ],
                wrap=True,
                color="gray",
                style="italic",
                fontsize=CHART_LABEL_FONT_SIZE,
            )

        ax.set_xlabel(f"\n{score_id}", fontsize=CHART_LABEL_FONT_SIZE)
        ax.set_ylabel(
            "count" if y_axis_label is None else y_axis_label,
            fontsize=CHART_LABEL_FONT_SIZE)

        ax.grid(axis="y")
        ax.grid(axis="x")

        fig.savefig(outfile)
        plt.close(fig)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> NumberHistogram:
        """Build a number histogram from a dict.

        Parameters
        ----------
        data : dict[str, Any]
            A mapping like the result of ``to_dict``. A missing key
            ``min_value`` or ``max_value`` gives NaN. A missing
            ``out_of_range_bins`` gives two zeros. A mapping without the
            stored count, sum and sum of squares gives a histogram with
            unknown ``count``, ``mean`` and ``std``.

        Returns
        -------
        NumberHistogram
            The histogram.

        Raises
        ------
        KeyError
            If the key ``config`` is missing.
        """
        config = NumberHistogramConfig.from_dict(data["config"])

        hist = NumberHistogram(
            config,
            bins=np.array(data.get("bins")),
            bars=np.array(data.get("bars")),
        )
        hist.min_value = data.get("min_value", np.nan)
        hist.max_value = data.get("max_value", np.nan)
        hist.out_of_range_bins = data.get("out_of_range_bins", [0, 0])
        hist._moments = Moments.from_stored(data)

        return hist

    @staticmethod
    def deserialize(content: str) -> NumberHistogram:
        """Rebuild a number histogram from ``serialize`` output.

        Parameters
        ----------
        content : str
            The JSON text.

        Returns
        -------
        NumberHistogram
            The histogram.
        """
        data = json.loads(content)
        return NumberHistogram.from_dict(data)


class HistogramStatisticMixin:
    """Mixin for creating statistics classes with histograms."""

    @staticmethod
    def get_histogram_file(score_id: str) -> str:
        return f"histogram_{score_id}.yaml"

    @staticmethod
    def get_histogram_image_file(score_id: str) -> str:
        return f"histogram_{score_id}.png"


class NullHistogram(Statistic):
    """A histogram that has no counts and records why.

    Parameters
    ----------
    config : NullHistogramConfig | None
        The configuration. ``None`` gives the reason ``Unspecified
        reason``.

    Attributes:
        type (str): The type name that a stored file carries,
            ``null_histogram``.

        reason (str): Why there is no histogram.
    """

    type = "null_histogram"

    def __init__(self, config: NullHistogramConfig | None) -> None:
        super().__init__(
            "null_histogram", "Used for invalid/annulled histograms",
        )
        if config is None:
            config = NullHistogramConfig.default_config()
        self.reason = config.reason

    def add_value(
        self, value: Any, count: int = 1,  # ruff: ignore[unused-method-argument]
    ) -> None:
        """Discard the value.

        Parameters
        ----------
        value : Any
            Ignored.
        count : int
            Ignored.
        """
        # A null histogram counts nothing by design, so that a statistics
        # build can feed every score the same way without first asking
        # whether this one has a histogram.  The region scan relies on it:
        # a score whose histogram refuses a value is nullified in place,
        # and the region's remaining records are still handed to it.
        # pylint: disable=unused-argument
        return

    def merge(
        self, other: Histogram,  # ruff: ignore[unused-method-argument]
    ) -> None:
        """Do nothing, because there are no counts to add.

        Parameters
        ----------
        other : Histogram
            Ignored.
        """
        # Present so that ``merge`` can be called on any ``Histogram``.  The
        # region merge sets a null histogram aside before merging, so this
        # does not run there.
        # pylint: disable=unused-argument
        return

    def to_dict(self) -> dict[str, Any]:
        """Render this histogram as the mapping ``from_dict`` reads back.

        Returns
        -------
        dict[str, Any]
            A mapping with the key ``config``. It holds the reason, which
            is all the content of a null histogram.
        """
        return {
            "config": {
                "type": "null",
                "reason": self.reason,
            },
        }

    def values_domain(self) -> str:
        """Report that there is no domain.

        Returns
        -------
        str
            The text ``NO DOMAIN``.
        """
        return "NO DOMAIN"

    def moments_summary(self) -> None:
        """Return ``None``, because a null histogram has no moments.

        Returns
        -------
        None
            Always.
        """
        return

    # pylint: disable=unused-argument
    def plot(self, _outfile: IO, _score_id: str) -> None:
        """Draw nothing and leave ``outfile`` untouched.

        Parameters
        ----------
        _outfile : IO
            Ignored.
        _score_id : str
            Ignored.
        """
        # The caller is expected to render ``reason`` in place of the image,
        # rather than to link an image file this never wrote.
        return

    def serialize(self) -> str:
        """Render this histogram as the JSON stored in the resource.

        Returns
        -------
        str
            The JSON text.
        """
        return json.dumps(self.to_dict(), indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> NullHistogram:
        """Build a null histogram from a dict.

        Parameters
        ----------
        data : dict[str, Any]
            A mapping like the result of ``to_dict``. A missing ``reason``
            gives an empty text.

        Returns
        -------
        NullHistogram
            The histogram.

        Raises
        ------
        TypeError
            If the type in ``config`` is not ``null``.
        """
        config = data["config"]
        hist_type = config.get("type")
        if hist_type != "null":
            raise TypeError(
                f"Invalid configuration type for null histogram!\n{data}",
            )
        reason = config.get("reason", "")
        return NullHistogram(NullHistogramConfig(reason=reason))

    @staticmethod
    def deserialize(content: str) -> NullHistogram:
        """Rebuild a null histogram from ``serialize`` output.

        Parameters
        ----------
        content : str
            The JSON text.

        Returns
        -------
        NullHistogram
            The histogram.
        """
        data = json.loads(content)
        return NullHistogram.from_dict(data)


class CategoricalHistogram(Statistic):
    """A histogram that counts each distinct value of a score.

    Parameters
    ----------
    config : CategoricalHistogramConfig
        The configuration.
    counter : dict[str | int, int] | None
        The counts of the values. ``None`` means an empty histogram.
    truncated : bool
        True when ``counter`` holds only the values of a truncated file.
    unique_values : int | None
        The distinct values of the full histogram. ``None`` means the
        length of ``counter``.
    total_count : int | None
        The sum of the counts of the full histogram. ``None`` means the
        sum of ``counter``.

    Attributes:
        type (str): The type name that a stored file carries,
            ``categorical_histogram``.

        UNIQUE_VALUES_LIMIT (int): The most distinct values that a
            histogram accepts when its config does not set
            ``enforce_type``. The value is 100.

        config (CategoricalHistogramConfig): The configuration.

        enforce_type (bool): The ``enforce_type`` flag of the config.

        truncated (bool): True when this histogram was loaded from a
            truncated file. Its counts are then only the kept values.

        y_log_scale (bool): The ``y_log_scale`` flag of the config.
    """

    type = "categorical_histogram"

    UNIQUE_VALUES_LIMIT = 100

    # pylint: disable=too-few-public-methods
    def __init__(
        self,
        config: CategoricalHistogramConfig,
        counter: dict[str | int, int] | None = None,
        *,
        truncated: bool = False,
        unique_values: int | None = None,
        total_count: int | None = None,
    ):
        super().__init__(
            "categorical_histogram",
            "Collects values for categorical histogram.",
        )
        self.config = config
        self.enforce_type = config.enforce_type

        if counter is not None:
            self._counter = Counter(counter)
        else:
            self._counter = Counter()

        self.truncated = truncated
        self._unique_values = unique_values
        self._total_count = total_count

        self.y_log_scale = config.y_log_scale

    def add_value(
        self, value: str | int | None,
        count: int = 1,
    ) -> None:
        """Add a value to the categorical histogram.

        Parameters
        ----------
        value : str | int | None
            The value. ``None`` is skipped and nothing is counted.
        count : int
            The weight of the value.

        Raises
        ------
        TypeError
            If the value is neither text nor an integer.
        HistogramError
            If the config does not set ``enforce_type`` and the histogram
            now holds more than ``UNIQUE_VALUES_LIMIT`` distinct values.
        """
        if value is None:
            return
        if not isinstance(value, str | int):
            raise TypeError(
                "Only string or int values can be added categorical "
                f"histogram; bad <{value}>",
            )
        self._counter[value] += count
        if not self.enforce_type and \
                len(self._counter) > CategoricalHistogram.UNIQUE_VALUES_LIMIT:
            raise HistogramError(
                f"Too many unique values {len(self._counter)} "
                f"for categorical histogram.",
            )

    def add_batch(
        self, values: np.ndarray, weights: np.ndarray,
    ) -> None:
        """Add a batch of ``(value, weight)`` pairs, vectorized.

        The result is the same as calling ``add_value`` for each pair in
        order.

        Parameters
        ----------
        values : np.ndarray
            The values. ``None`` cells are skipped.
        weights : np.ndarray
            The weight of each value, of the same length as ``values``.

        Raises
        ------
        TypeError
            If a value is neither text nor an integer.
        HistogramError
            If the config does not set ``enforce_type`` and the batch
            takes the histogram past ``UNIQUE_VALUES_LIMIT``.
        """
        # This is the hot path for the statistics scan, where a per-value
        # Python call dominates the cost.
        #
        # The limit's message reports ``UNIQUE_VALUES_LIMIT + 1`` because that
        # is the count ``add_value`` always raises at: it tests after every
        # single add, so the first add that exceeds the limit is the one that
        # raises, and the histogram holds exactly one value too many.  Which
        # values the counter holds when it raises is not otherwise observable
        # -- a histogram that raises is replaced by a ``NullHistogram``
        # carrying the message.
        #
        # A batch containing BOTH a value of an unusable type and enough new
        # values to trip the limit reports the type failure, wherever the two
        # sit relative to each other; the per-record path reports whichever
        # comes first.  The distinction is unreachable through the scan, which
        # batches a ``str`` score's column -- every cell of which is a ``str``
        # or the ``None`` this skips.
        cells = np.asarray(values, dtype=object).tolist()
        if weights.size and bool(np.all(weights == 1)):
            # The C-level count, for the kinds that weigh every record 1.
            batch = Counter(cells)
            batch.pop(None, None)
        else:
            batch = Counter()
            for cell, weight in zip(cells, weights.tolist(), strict=True):
                if cell is None:
                    continue
                batch[cell] += weight

        for cell in batch:
            if not isinstance(cell, str | int):
                raise TypeError(
                    "Only string or int values can be added categorical "
                    f"histogram; bad <{cell}>",
                )

        self._counter.update(batch)
        if not self.enforce_type and \
                len(self._counter) > CategoricalHistogram.UNIQUE_VALUES_LIMIT:
            raise HistogramError(
                f"Too many unique values "
                f"{CategoricalHistogram.UNIQUE_VALUES_LIMIT + 1} "
                f"for categorical histogram.",
            )

    def merge(self, other: CategoricalHistogram) -> None:
        """Add the counts of another histogram into this one.

        Parameters
        ----------
        other : CategoricalHistogram
            A full histogram with the same config.

        Raises
        ------
        HistogramError
            If either histogram is truncated, or if the config does not
            set ``enforce_type`` and the sum has too many distinct values.
        AssertionError
            If the two configs differ.
        """
        assert isinstance(other, CategoricalHistogram)
        assert self.config == other.config
        if self.truncated or other.truncated:
            raise HistogramError(
                "Can not merge a truncated categorical histogram sidecar; "
                "merge needs the full histogram values.",
            )
        self._counter += other._counter
        if not self.enforce_type and \
                len(self._counter) > CategoricalHistogram.UNIQUE_VALUES_LIMIT:
            raise HistogramError(
                f"Can not merge categorical histograms; "
                f"too many unique values {len(self._counter)}")

    @property
    def raw_values(self) -> dict[str | int, int]:
        """Every counted value with its count, unordered.

        A truncated histogram returns only the values that its file kept.
        """
        # A custom ``plot_function`` receives the histogram itself and so
        # can read either this or ``display_values``.  On a histogram
        # loaded back from a truncated sidecar the counter holds only the
        # values that sidecar carried; ``unique_values`` and
        # ``total_count`` are the ones that still describe the full data.
        return dict(self._counter)

    @property
    def unique_values(self) -> int:
        """The number of distinct values of the full histogram.

        A truncated histogram returns the count of the full data, not the
        count of the values it kept.
        """
        if self._unique_values is not None:
            return self._unique_values
        return len(self._counter)

    @property
    def total_count(self) -> int:
        """The sum of all counts of the full histogram.

        A truncated histogram returns the sum of the full data, not the
        sum of the values it kept.
        """
        if self._total_count is not None:
            return self._total_count
        return sum(self._counter.values())

    @property
    def display_values(self) -> dict[str | int, int]:
        """The counts that the summary page shows, in display order.

        The config selects and orders the values. The values that the
        selection leaves out are summed under the key ``Other``.

        Raises
        ------
        ValueError
            If the config sets ``value_order`` and the order leaves out a
            counted value.
        """
        # A truncated instance carries exactly the values its config selected
        # for display at serialization time, so they are returned verbatim --
        # re-running the selection against the truncated counter would compute
        # percentages and orderings against the wrong totals.
        if self.truncated:
            return dict(self._counter)
        values = {}
        if self.config.value_order:
            for key in self.config.value_order:
                values[key] = self._counter[key]
            if len(values) < len(self._counter):
                raise ValueError(
                    "misconfigured categorical histogram value_order",
                    f"{self.config.value_order} < {self._counter.keys()}")
            return values

        if self.config.displayed_values_percent is not None:
            total = sum(self._counter.values())
            displayed = 0
            other = 0
            displayed_percent = self.config.displayed_values_percent
            for key, count in self._counter.most_common():
                if 100.0 * displayed / total < displayed_percent:
                    values[key] = count
                    displayed += count
                else:
                    other += count
            if other > 0:
                values["Other"] = other
            return values

        ordering = None
        if self.config.natural_order:
            ordering = sorted(
                self._counter, key=lambda x: (isinstance(x, str), x))
        else:
            ordering = [key for key, _ in self._counter.most_common()]

        for key in ordering[:self.config.displayed_values_count]:
            values[key] = self._counter[key]

        if self.config.displayed_values_count is not None and \
                len(self._counter) > self.config.displayed_values_count:
            other = 0
            for key in ordering[self.config.displayed_values_count:]:
                other += self._counter[key]
            if other > 0:
                values["Other"] = other

        return values

    def moments_summary(self) -> None:
        """Return ``None``, because a categorical value has no mean.

        Returns
        -------
        None
            Always.
        """
        return

    def values_domain(self) -> str:
        """The displayed values, as text for the summary page.

        Returns
        -------
        str
            The displayed values, separated by commas. A truncated
            histogram adds the text ``(top N of M values)``.
        """
        domain = ", ".join(str(k) for k in self.display_values)
        if self.truncated and len(self._counter) < self.unique_values:
            return (
                f"{domain} "
                f"(top {len(self._counter)} of {self.unique_values} values)"
            )
        return domain

    def to_dict(self) -> dict[str, Any]:
        """Render this histogram as the mapping ``from_dict`` reads back.

        Returns
        -------
        dict[str, Any]
            A mapping with the keys ``config`` and ``values``. It holds
            every counted value, not only the displayed ones.
        """
        return {
            "config": self.config.to_dict(),
            "values": dict(self._counter),
        }

    def serialize(self) -> str:
        """Render the full histogram as the JSON stored in the resource.

        Returns
        -------
        str
            The JSON text.
        """
        return json.dumps(self.to_dict(), indent=2)

    def serialize_gzipped(self) -> bytes:
        """Render the ``serialize`` JSON as gzip bytes.

        Returns
        -------
        bytes
            The same histogram gives the same bytes on every call.
        """
        # No timestamp (``mtime=0``), no file name and a fixed level, so the
        # same histogram gives the same bytes and md5 on every rebuild.
        buffer = io.BytesIO()
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=buffer,
            compresslevel=GZIPPED_HISTOGRAM_COMPRESSLEVEL, mtime=0,
        ) as gzfile:
            gzfile.write(self.serialize().encode("utf-8"))
        return buffer.getvalue()

    def serialize_truncated(self) -> str:
        """Render the small truncated form of this histogram as JSON.

        Returns
        -------
        str
            The JSON text. It holds the config, the values that the
            config selects for display, ``unique_values`` and
            ``total_count`` of the full histogram, and the flag
            ``truncated``. A value in ``value_order`` that has no count
            gets the count 0.
        """
        # The sidecar is the small, always-readable companion of a full
        # histogram whose values file may be absent from a checkout
        # (DVC-tracked, not pulled).
        #
        # The carried values follow the display selection so the sidecar
        # renders what the full histogram would: every ``value_order`` key
        # with its real count for an ordered config, the values covering
        # ``displayed_values_percent`` for a percent config, and the
        # ``displayed_values_count`` most common values otherwise.
        values: dict[str | int, int]
        if self.config.value_order:
            # A JSON round-trip stringifies the counter's keys while the
            # config may order the values by their original (e.g. int)
            # keys, so a histogram loaded back from disk falls back to
            # the stringified key -- it must serialize the same counts a
            # live one does.
            values = {
                key: self._counter.get(key, self._counter.get(str(key), 0))
                for key in self.config.value_order}
        elif self.config.displayed_values_percent is not None:
            values = dict(self._counter.most_common(len(self.display_values)))
        else:
            top = self.config.displayed_values_count \
                or DEFAULT_DISPLAYED_VALUES_COUNT
            values = dict(self._counter.most_common(top))
        return json.dumps({
            "config": self.config.to_dict(),
            "values": values,
            "truncated": True,
            "unique_values": self.unique_values,
            "total_count": self.total_count,
        }, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CategoricalHistogram:
        """Build a categorical histogram from a dict.

        Parameters
        ----------
        data : dict[str, Any]
            A mapping like the result of ``to_dict``, or the truncated
            form. A full histogram has no ``truncated``,
            ``unique_values`` or ``total_count`` key. They default to
            False and to the values computed from the counts.

        Returns
        -------
        CategoricalHistogram
            The histogram.

        Raises
        ------
        KeyError
            If the key ``config`` is missing.
        """
        config = CategoricalHistogramConfig.from_dict(data["config"])
        return CategoricalHistogram(
            config,
            data.get("values"),
            truncated=data.get("truncated", False),
            unique_values=data.get("unique_values"),
            total_count=data.get("total_count"),
        )

    @staticmethod
    def deserialize(content: str) -> CategoricalHistogram:
        """Rebuild a categorical histogram from ``serialize`` output.

        Parameters
        ----------
        content : str
            The JSON text.

        Returns
        -------
        CategoricalHistogram
            The histogram.
        """
        data = json.loads(content)
        return CategoricalHistogram.from_dict(data)

    def plot(
        self,
        outfile: IO,
        score_id: str,
        *,
        y_axis_label: str | None = None,
        small_values_description: str | None = None,
        large_values_description: str | None = None,
    ) -> None:
        """Plot the displayed values and write the image into ``outfile``.

        Parameters
        ----------
        outfile : IO
            A binary file object that receives the image.
        score_id : str
            The score name. It is the x axis label.
        y_axis_label : str | None
            The y axis label. ``None`` means ``count``.
        small_values_description : str | None
            The text under the first value. The plot shows both
            descriptions or neither.
        large_values_description : str | None
            The text under the last value.
        """
        # pylint: disable=import-outside-toplevel
        import matplotlib
        matplotlib.use("agg")
        import matplotlib.pyplot as plt
        display_values = self.display_values
        values = [str(k) for k in display_values]
        counts = list(display_values.values())

        fig, ax = plt.subplots(figsize=CHART_FIGSIZE, tight_layout=True)
        ax.bar(
            x=values,
            height=counts,
            tick_label=[str(v) for v in values],
            log=self.config.y_log_scale,
            align="center",
        )

        if len(values) == 1:
            ax.set_xlim(-2.5, 2.5)

        if self.config.allow_only_whole_values_y:
            ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

        if small_values_description is not None and \
                large_values_description is not None:

            sec = ax.secondary_xaxis(location=0)
            sec.set_ticks(
                [
                    0,
                    len(values) - 1,
                ],
                labels=[
                    f"\n{small_values_description}",
                    f"\n{large_values_description}",
                ],
                wrap=True,
                color="gray",
                style="italic",
                fontsize=CHART_LABEL_FONT_SIZE,
            )

        ax.set_xlabel(f"\n{score_id}", fontsize=CHART_LABEL_FONT_SIZE)
        ax.set_ylabel(
            "count" if y_axis_label is None else y_axis_label,
            fontsize=CHART_LABEL_FONT_SIZE)

        label_angle = self.config.label_rotation % 360
        if self.config.label_rotation < 0:
            label_angle = 360 + label_angle

        if label_angle != 0:
            ax.set_xticklabels(
                [str(v) for v in values],
                rotation=self.config.label_rotation,
                ha="center",
                va="top",
                rotation_mode="default",
                fontsize=CHART_LABEL_FONT_SIZE,
            )

        fig.savefig(outfile)
        plt.close(fig)


def build_histogram_config(
        config: dict[str, Any] | None) -> HistogramConfig | None:
    """Create histogram config form configuration dict."""
    if config is None:
        return None
    if "histogram" in config:
        hist_config = config["histogram"]
        if "type" not in hist_config:
            return NullHistogramConfig(
                f"Missing histogram type in configuration {config}")
        hist_type = hist_config["type"]
    else:
        return None

    if hist_type == "number":
        return NumberHistogramConfig.from_dict(hist_config)
    if hist_type == "categorical":
        return CategoricalHistogramConfig.from_dict(hist_config)
    if hist_type == "null":
        return NullHistogramConfig.from_dict(hist_config)

    return NullHistogramConfig(f"Invalid histogram configuration {config}")


def build_default_histogram_conf(
    value_type: str,
) -> NumberHistogramConfig | CategoricalHistogramConfig | NullHistogramConfig:
    """Build default histogram config for given value type."""
    if value_type in ["int", "float"]:
        return NumberHistogramConfig.default_config(None)

    if value_type == "str":
        return CategoricalHistogramConfig.default_config()

    return NullHistogramConfig(
        "No histogram configured and no default config available for type "
        f"{value_type}",
    )


def build_empty_histogram(
    config: HistogramConfig,
) -> NumberHistogram | CategoricalHistogram | NullHistogram:
    """Create an empty histogram from a deserialize histogram dictionary."""
    # pylint: disable=broad-except
    try:
        if isinstance(config, NumberHistogramConfig):
            return NumberHistogram(config)
        if isinstance(config, CategoricalHistogramConfig):
            return CategoricalHistogram(config)
        if isinstance(config, NullHistogramConfig):
            return NullHistogram(config)
        return NullHistogram(NullHistogramConfig(
            "Could not match histogram config type",
        ))
    except BaseException as err:
        logger.warning(
            "Failed to create empty histogram from config", exc_info=True)
        return NullHistogram(NullHistogramConfig(
            f"Failed to create empty histogram from config: {err}"))


def load_histogram(
    resource: GenomicResource, filename: str,
) -> Histogram:
    """Load a stored histogram from a resource.

    Parameters
    ----------
    resource : GenomicResource
        The resource that holds the file.
    filename : str
        The file name in the resource. The suffix selects the format:
        ``.yaml``, ``.json`` or ``.json.gz`` (gzipped JSON).

    Returns
    -------
    Histogram
        The histogram. A missing file, an unknown suffix, an unknown
        histogram type and a body that fails to deserialize each give a
        ``NullHistogram`` with the reason.

    Raises
    ------
    KeyError
        If the parsed file has no ``config`` key or no ``type`` in it.
    yaml.YAMLError
        If a ``.yaml`` file is not valid YAML.
    json.JSONDecodeError
        If a ``.json`` or ``.json.gz`` file is not valid JSON.
    """
    gzipped = filename.endswith(GZIPPED_JSON_SUFFIX)
    try:
        if gzipped:
            with resource.open_raw_file(filename, mode="rb") as infile:
                content = gzip.decompress(infile.read()).decode("utf-8")
        else:
            with resource.open_raw_file(filename) as infile:
                content = infile.read()
    except FileNotFoundError:
        # Handled, not fatal: a null histogram is returned.  So no
        # traceback -- during a repair run this is an expected consequence
        # of statistics that have not been built yet, and its traceback was
        # the only one the user saw, pointing away from the real fault
        # (gain#364).  WARNING rather than DEBUG keeps a genuinely missing
        # histogram on an otherwise healthy resource visible.
        logger.warning(
            "unable to load histogram file: %s; file not found", filename)
        return NullHistogram(NullHistogramConfig(
            "Histogram file not found.",
        ))
    if filename.endswith(".yaml"):
        hist_data = yaml.safe_load(content)
    elif gzipped or filename.endswith(".json"):
        hist_data = json.loads(content)
    else:
        logger.error(
            "Invalid histogram file format: %s", filename)
        return NullHistogram(NullHistogramConfig(
            "Invalid histogram file format.",
        ))
    config = hist_data["config"]
    hist_type = config["type"]
    try:
        if hist_type == "number":
            return NumberHistogram.from_dict(hist_data)
        if hist_type == "categorical":
            return CategoricalHistogram.from_dict(hist_data)
        if hist_type == "null":
            return NullHistogram.from_dict(hist_data)

        return NullHistogram(NullHistogramConfig("Invalid histogram type"))
    except BaseException:  # pylint: disable=broad-except
        logger.exception(
            "Failed to deserialize histogram from %s",
            filename,
        )
        return NullHistogram(NullHistogramConfig(
            "Failed to deserialize histogram.",
        ))


def truncated_histogram_filename(histogram_filename: str) -> str:
    """Return the truncated-sidecar filename for a histogram filename.

    ``statistics/histogram_cell.json`` maps to
    ``statistics/truncated/histogram_cell.json``: a directory rather
    than a name suffix, because the score-id part of the histogram
    filename is arbitrary (a score may itself be named
    ``foo_truncated``) while no score id can contain ``/``.
    """
    directory, sep, basename = histogram_filename.rpartition("/")
    return f"{directory}{sep}truncated/{basename}"


def drop_stale_histogram_file(
    resource: GenomicResource, filename: str,
) -> None:
    """Delete a histogram file an earlier build left in ``resource``.

    A DVC-tracked file -- one with a ``.dvc`` pointer beside it -- is left
    in place with a warning, whether or not its blob is pulled: deleting
    the blob alone leaves a pointer the manifest build re-adds an entry
    from, for a file that is gone, and a pointer-only checkout still
    carries that stale entry.  A file that is not there at all is not an
    error, since a score that never had statistics has nothing to drop.
    """
    proto = resource.proto
    if proto.file_exists(resource, f"{filename}{DVC_SUFFIX}"):
        logger.warning(
            "stale <%s> of resource <%s> is DVC-tracked; left in place, "
            "run 'dvc remove %s%s' to drop it",
            filename, resource.resource_id, filename, DVC_SUFFIX)
        return
    if not proto.file_exists(resource, filename):
        return
    assert isinstance(proto, ReadWriteRepositoryProtocol)
    proto.delete_resource_file(resource, filename)


def save_histogram(
    resource: GenomicResource,
    filename: str,
    histogram: Histogram,
) -> None:
    """Save histogram into a resource."""
    if not filename.endswith(".json"):
        logger.error(
            "Invalid histogram file format: %s; "
            "histograms are stored in JSON format only", filename)
        raise ValueError(
            f"Invalid histogram file format: <{filename}>; "
            f"histograms are stored in JSON format only",
        )
    with resource.open_raw_file(filename, mode="wt") as outfile:
        outfile.write(histogram.serialize())


HistogramConfig = \
    NullHistogramConfig | CategoricalHistogramConfig | NumberHistogramConfig
Histogram = NullHistogram | CategoricalHistogram | NumberHistogram


def _import_from_string(module_name: str, source_code: str) -> Any:
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    exec(source_code, module.__dict__)  # ruff: ignore[exec-builtin] pylint: disable=exec-used
    sys.modules[spec.name] = module
    return module


def plot_histogram(
    res: GenomicResource,
    image_filename: str,
    hist: Histogram,
    score_id: str,
    small_values_desc: str | None = None,
    large_values_desc: str | None = None,
) -> None:
    """Plot histogram and save it into the resource."""
    if isinstance(hist, NullHistogram):
        return

    if hist.config.plot_function is None:
        with res.open_raw_file(image_filename, mode="wb") as outfile:
            hist.plot(
                outfile,
                score_id,
                small_values_description=small_values_desc,
                large_values_description=large_values_desc,
            )
        return

    plot_file, plot_function_name = hist.config.plot_function.split(":")
    with res.open_raw_file(
        plot_file,
        mode="rt",
    ) as srcfile:
        source_code = srcfile.read()
        plot_module_name = str(
            pathlib.Path(res.resource_id) /
            pathlib.Path(plot_file).with_suffix("")).replace("/", ".")
        plot_module = _import_from_string(
            plot_module_name, source_code)
        func = getattr(plot_module, plot_function_name)

    with res.open_raw_file(
        image_filename,
        mode="wb",
    ) as outfile:
        func(
            outfile,
            hist,
            score_id,
            small_values_desc,
            large_values_desc,
        )

    return
