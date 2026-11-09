"""The configuration side of the ``fragment_score_binner`` binner.

The GRR convention's constants for single-cell fragment resources, the
spec classes of an entry (:class:`FragmentEntry` and its blocks), the
reader of an entry's config into them, and the resolution of a cell
grouping from a metadata table and of a value grouping from a score's
histogram.  The binding of a job to its resources and the fold of its
fragments into bins are not here.
"""
from __future__ import annotations

import enum
import gzip
import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from zipfile import BadZipFile

import numpy as np
import pandas as pd

from gain.binning.binners import (
    NUMERIC_VALUE_TYPES,
    RunDefinitionError,
    check_keys,
    entry_name,
)
from gain.binning.fragment_array_folds import LENGTH_AGGREGATORS
from gain.genomic_resources.data_frame_resource import (
    load_data_frame_from_resource,
)
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    HistogramError,
    NullHistogram,
    NullHistogramConfig,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

# The GRR convention for single-cell fragment resources (F7).  Every
# other mention of these names refers to the constants.

#: The fragment resource's label naming its cell-metadata ``data_frame``.
CELL_META_RESOURCE_ID_LABEL = "cell_meta_resource_id"
#: The fragment resource's label naming its sample.
SAMPLE_ID_LABEL = "sample_id"
#: The metadata table's column naming a row's sample.
SAMPLE_ID_COLUMN = "sample_id"
#: The metadata table's column holding a cell's barcode.
BARCODE_COLUMN = "barcode"
#: The metadata table's column holding a cell's group.
CLASS_COLUMN = "class"
#: The fragment score carrying a fragment's barcode.
CELL_SCORE = "cell"
#: The fragment score holding a fragment's read pairs.
COUNT_SCORE = "count"

#: The group of an entry that names none.
DEFAULT_GROUP = "all"

ENTRY_KEYS = frozenset({
    "resource_query", "search_term", "pool", "group", "value", "aggregate",
    "meta", "name"})
#: The keys of a metadata grouping, each with its default.
CELL_GROUP_DEFAULTS = {
    "cell_score_id": CELL_SCORE,
    "cell_meta_column": BARCODE_COLUMN,
    "group_meta_column": CLASS_COLUMN,
}
CELL_GROUP_KEYS = frozenset(CELL_GROUP_DEFAULTS)
#: The key of a raw-value grouping: the ``str`` score whose values group.
VALUE_GROUP_KEY = "group_score_id"
GROUP_KEYS = frozenset({"group", VALUE_GROUP_KEY}) | CELL_GROUP_KEYS
VALUE_KEYS = frozenset({"score_id", "value"})
AGGREGATE_KEYS = frozenset({"mode", "aggregator", "uncovered_value"})
#: The keys ``aggregate`` does not take, each with the ``value`` key that
#: says what one fragment adds.
MOVED_TO_VALUE = {"score": "score_id", "value": "value"}
#: The fragment mode reducing, per bin, the fragments that start in it.
FRAGMENT_START = "fragment_start"
#: The fragment mode reducing, per bin, the fragments that overlap it,
#: each weighted by its overlap in base pairs.
FRAGMENT_LENGTH = "fragment_length"
#: The fragment mode reducing, per bin, the coverage profile of the
#: fragments, weighted by base pairs; a base at 0 is uncovered.
COVERAGE_PROFILE = "coverage_profile"
#: The accepted ``aggregate.mode`` values.
MODES = (FRAGMENT_START, FRAGMENT_LENGTH, COVERAGE_PROFILE)
META_SOURCE_KEYS = frozenset({"resource_id", "resource_label", "file_name"})
META_FILE_KEYS = frozenset({"file_separator", "file_format"})
META_KEYS = META_SOURCE_KEYS | META_FILE_KEYS | frozenset({"filter"})
FILTER_KEYS = frozenset({"column", "value", "label"})
#: A local table's formats, each with its separator (none for excel).
FILE_FORMATS: dict[str, str | None] = {
    "csv": ",", "tsv": "\t", "excel": None}


def _as_text(value: Any) -> str | None:
    """A table cell, a label or a score value as the text it is matched by.

    ``None`` for a missing value -- ``None``, NaN, ``pd.NA`` (a cell of
    a nullable-dtype column) or the empty string -- which matches nothing
    and names no group.  An integral float is written as an int, since a
    numeric column read with a missing cell is a float column, and its
    ``2`` must still match a label ``2``.
    """
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value)
    return text or None


def _quoted(names: Iterable[str]) -> str:
    return ", ".join(repr(name) for name in names)


def _partition(
    resources: list[GenomicResource],
    predicate: Callable[[GenomicResource], bool],
) -> tuple[list[str], list[str]]:
    """The ids of ``resources`` that satisfy ``predicate``, and the rest."""
    chosen: list[str] = []
    rest: list[str] = []
    for resource in resources:
        (chosen if predicate(resource) else rest).append(
            resource.resource_id)
    return chosen, rest


@dataclass(frozen=True)
class MetaFilter:
    """One conjunct of a ``meta`` filter: ``column`` equals an operand.

    A cell matches by its text: a number matches as its text, and a
    missing or empty cell has none.

    Attributes
    ----------
    column
        The column of the cell metadata table.
    value
        A literal operand.
    label
        A label of the fragment resource whose rows are selected.  The
        value of that label is the operand.  Give exactly one of
        ``value`` or ``label``.

    Raises
    ------
    RunDefinitionError
        When ``column`` or ``label`` is not a non-empty string, ``value``
        is empty or not a string, or not exactly one of ``value`` and
        ``label`` is given.
    """

    column: str
    value: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        _text("column", self.column)
        if (self.value is None) == (self.label is None):
            raise RunDefinitionError("give exactly one of value or label")
        if self.label is not None:
            _text("label", self.label)
        if self.value == "":
            raise RunDefinitionError("value must not be empty")
        if self.value is not None:
            _text("value", self.value)

    def operand(self, resource: GenomicResource) -> str | None:
        """The text this conjunct's column must equal, for ``resource``."""
        if self.label is None:
            return self.value
        return _as_text(resource.get_labels().get(self.label))


@dataclass(frozen=True)
class MetaTable:
    """Where a cell-metadata table is read from.

    A ``data_frame`` resource, ``resource_id``, or a local file at the
    absolute ``path``, read as ``file_format`` with ``file_separator``
    (the format's own when unset).  The parent resolving a run and a
    worker binding a job read it through the same :meth:`load`.
    """

    resource_id: str | None = None
    path: str | None = None
    file_format: str = "csv"
    file_separator: str | None = None

    @property
    def name(self) -> str:
        """The resource id, or the local file's absolute path."""
        if self.resource_id is not None:
            return self.resource_id
        assert self.path is not None
        return self.path

    @property
    def is_local(self) -> bool:
        return self.path is not None

    def load(self, grr: GenomicResourceRepo) -> pd.DataFrame:
        """Read the table, from ``grr`` or from the local file."""
        if self.resource_id is not None:
            return load_data_frame_from_resource(
                grr.get_resource(self.resource_id))
        assert self.path is not None
        if self.file_format == "excel":
            return pd.read_excel(self.path)
        return pd.read_csv(
            self.path,
            sep=self.file_separator or FILE_FORMATS[self.file_format])

    @property
    def parameters(self) -> dict[str, Any]:
        if self.resource_id is not None:
            return {"meta_resource_id": self.resource_id}
        return {
            "meta_file": self.path,
            "meta_file_format": self.file_format,
            "meta_file_separator": self.file_separator,
        }


class DuplicateBarcodeError(ValueError):
    """A barcode on more than one of a resource's filtered rows."""


@dataclass(frozen=True)
class _TextColumn:
    """A table column as the text its cells are matched by, factorized.

    ``codes`` holds, per cell, the index of its text in ``texts``, or -1
    for a cell with no text form.  Matching a resource's operand is then
    an integer comparison, not a pass of ``_as_text`` over the column.
    """

    codes: np.ndarray
    texts: dict[str, int]

    @staticmethod
    def of(column: pd.Series) -> _TextColumn:
        """Convert each cell of ``column`` to text once and factorize."""
        codes, uniques = pd.factorize(
            pd.Series([_as_text(cell) for cell in column.tolist()],
                      dtype=object))
        return _TextColumn(
            codes, {str(text): i for i, text in enumerate(uniques)})

    def matches(self, operand: str | None) -> np.ndarray:
        """The boolean mask of the cells whose text is ``operand``."""
        if operand is None:
            return np.asarray(self.codes == -1)
        code = self.texts.get(operand)
        if code is None:
            return np.zeros(len(self.codes), dtype=bool)
        return np.asarray(self.codes == code)


@dataclass(frozen=True)
class BoundCellGrouping:
    """How a fragment's barcode becomes a group: a filtered table lookup.

    ``cell_score_id`` is the fragment score carrying the barcode; the
    metadata ``table``, its rows selected per fragment resource by
    ``filters``, maps the barcode in ``cell_meta_column`` to the group in
    ``group_meta_column``.
    """

    cell_score_id: str
    cell_meta_column: str
    group_meta_column: str
    table: MetaTable
    filters: tuple[MetaFilter, ...]

    def prepare_filters(self, table: pd.DataFrame) -> dict[str, _TextColumn]:
        """Each filter column of ``table`` as text, converted once."""
        return {
            column: _TextColumn.of(table[column])
            for column in dict.fromkeys(f.column for f in self.filters)
        }

    def groups_of(
        self, table: pd.DataFrame, resource: GenomicResource,
        prepared: dict[str, _TextColumn] | None = None,
    ) -> dict[str, str]:
        """Map each barcode of ``resource``'s rows to its group.

        The rows are those every filter conjunct selects, as a boolean
        mask; a row whose group is empty names no group and is left out,
        so its barcode maps nowhere.  A barcode on more than one row is
        a :class:`DuplicateBarcodeError`.  ``prepared`` is
        :meth:`prepare_filters` of ``table``, when the caller holds it.
        """
        if prepared is None:
            prepared = self.prepare_filters(table)
        mask = np.ones(len(table), dtype=bool)
        for conjunct in self.filters:
            mask &= prepared[conjunct.column].matches(
                conjunct.operand(resource))
        rows = table[mask]
        mapping: dict[str, str] = {}
        seen: Counter[str] = Counter()
        for barcode, group in zip(
                rows[self.cell_meta_column].tolist(),
                rows[self.group_meta_column].tolist(), strict=True):
            barcode_text, group_text = _as_text(barcode), _as_text(group)
            if barcode_text is None:
                continue
            seen[barcode_text] += 1
            if group_text is not None:
                mapping[barcode_text] = group_text
        repeated = sorted(
            barcode for barcode, count in seen.items() if count > 1)
        if repeated:
            raise DuplicateBarcodeError(
                f"barcodes {_quoted(repeated)} appear on more than one "
                f"of its rows")
        return mapping

    @property
    def score_id(self) -> str:
        """The fragment score a group is looked up by."""
        return self.cell_score_id

    @property
    def dropped_reason(self) -> str:
        return (
            "their barcode absent from the cell metadata rows or of no "
            "group")

    def track_maps(
        self, grr: GenomicResourceRepo, resources: list[GenomicResource],
        index_of: dict[str, int],
    ) -> list[dict[str, int]]:
        """Per resource, each barcode's track index; the table read once."""
        table = self.table.load(grr)
        prepared = self.prepare_filters(table)
        return [
            {
                barcode: index_of[group]
                for barcode, group in self.groups_of(
                    table, resource, prepared).items()
                if group in index_of
            }
            for resource in resources
        ]

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "cell_score_id": self.cell_score_id,
            "cell_meta_column": self.cell_meta_column,
            "group_meta_column": self.group_meta_column,
            **self.table.parameters,
            "filter": [
                {"column": f.column, "value": f.value, "label": f.label}
                for f in self.filters
            ],
        }


@dataclass(frozen=True)
class BoundValueGrouping:
    """How a fragment's raw value of a ``str`` score becomes a group.

    The group of a fragment of the job's ``index``-th resource is
    ``prefixes[index]`` followed by its ``group_score_id`` value -- the
    resource's ``<sample_id>:`` in a pooled job, nothing in an unpooled
    one -- when ``values[index]``, the values its histogram lists, has
    that value, and none otherwise.
    """

    group_score_id: str
    prefixes: tuple[str, ...]
    values: tuple[tuple[str, ...], ...]

    @property
    def score_id(self) -> str:
        """The fragment score whose value is the group."""
        return self.group_score_id

    @property
    def dropped_reason(self) -> str:
        return (
            f"their {self.group_score_id!r} value missing or not among "
            f"the score's histogram values")

    def track_maps(
        self,
        grr: GenomicResourceRepo,  # ruff: ignore[unused-method-argument]
        resources: list[GenomicResource],
        index_of: dict[str, int],
    ) -> list[dict[str, int]]:
        """Per resource, each of its values' track index."""
        # pylint: disable=unused-argument
        assert len(resources) == len(self.prefixes) == len(self.values)
        return [
            {value: index_of[prefix + value] for value in values}
            for prefix, values in zip(self.prefixes, self.values, strict=True)
        ]

    @property
    def parameters(self) -> dict[str, Any]:
        return {VALUE_GROUP_KEY: self.group_score_id}


@dataclass(frozen=True)
class ScoreContribution:
    """What one fragment adds: its value of a numeric score.

    Attributes
    ----------
    score_id
        The fragment score whose value each fragment adds.  Each
        resource of the entry must have it as an ``int`` or a ``float``
        score; the resources are checked when the entry is resolved.

    Raises
    ------
    RunDefinitionError
        When ``score_id`` is not a non-empty string.
    """

    score_id: str

    def __post_init__(self) -> None:
        _text("score_id", self.score_id)


@dataclass(frozen=True)
class ConstantContribution:
    """What one fragment adds: a constant.

    The default, 1, makes the ``sum`` of a bin its count of fragments.

    Attributes
    ----------
    value
        The number each fragment adds.

    Raises
    ------
    RunDefinitionError
        When ``value`` is not an ``int`` or a ``float``.  A ``bool`` is
        not a number here.
    """

    value: float = 1

    def __post_init__(self) -> None:
        value: Any = self.value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise RunDefinitionError(
                f"value must be a number, not {value!r}")


class _ModeDefault(enum.Enum):
    """The marker of an ``uncovered_value`` that the caller did not give."""

    MODE = "mode"


#: The default ``uncovered_value`` of :class:`Aggregate`: the default of
#: its mode.  ``None`` cannot mark it, since ``None`` is a value to give.
_MODE_DEFAULT = _ModeDefault.MODE


@dataclass(frozen=True)
class Aggregate:
    """How a bin reduces the values of its fragments.

    Attributes
    ----------
    mode
        Which fragments reach a bin: :data:`FRAGMENT_START`,
        :data:`FRAGMENT_LENGTH` or :data:`COVERAGE_PROFILE`.
    aggregator
        The fold over the values of a bin.  The two length modes take
        only :data:`LENGTH_AGGREGATORS`.
    uncovered_value
        What an uncovered base adds, with a weight of 1 bp, as a
        ``float``; ``None`` adds nothing.  When it is not given, it is 0
        in :data:`COVERAGE_PROFILE` mode and ``None`` in the other
        modes.  :data:`FRAGMENT_START` mode takes none, not even
        ``None``.  The field keeps what the caller gave, or a private
        marker when the caller gave nothing, so
        :func:`dataclasses.replace` derives the default of the new mode.
        Read :attr:`effective_uncovered_value`, the value a bin uses.

    Raises
    ------
    RunDefinitionError
        When the mode is unknown, the aggregator is not one that the
        mode accepts, or ``uncovered_value`` is not a finite number or
        ``None``.
    """

    mode: str = FRAGMENT_START
    aggregator: str = "sum"
    uncovered_value: float | _ModeDefault | None = _MODE_DEFAULT

    def __post_init__(self) -> None:
        mode = self.mode
        if mode not in MODES:
            raise RunDefinitionError(
                f"mode {mode!r} is not supported; use one of "
                f"{', '.join(MODES)}")
        aggregator = _text("aggregator", self.aggregator)
        if aggregator not in EMPTY_BIN_VALUES:
            raise RunDefinitionError(
                f"aggregator {aggregator!r} does not produce a number; "
                f"use one of {', '.join(sorted(EMPTY_BIN_VALUES))}")
        given = self.uncovered_value is not _MODE_DEFAULT
        if mode == FRAGMENT_START:
            if given:
                raise RunDefinitionError(
                    f"uncovered_value does not apply in {FRAGMENT_START} "
                    f"mode, where a bin reduces only the fragments "
                    f"starting in it; give mode: {FRAGMENT_LENGTH} or "
                    f"{COVERAGE_PROFILE}, or drop uncovered_value")
            return
        if aggregator == "count":
            raise RunDefinitionError(
                f"aggregator 'count' would count base pairs, not "
                f"fragments, in {mode} mode; for the covered base pairs "
                f"give aggregator: sum with value: {{value: 1}}")
        if aggregator not in LENGTH_AGGREGATORS:
            raise RunDefinitionError(
                f"aggregator {aggregator!r} would weigh a value by its "
                f"overlap as a power in {mode} mode; use one of "
                f"{', '.join(LENGTH_AGGREGATORS)}")
        _uncovered_value(self.uncovered_value if given else None)

    @property
    def effective_uncovered_value(self) -> float | None:
        """What an uncovered base adds: the given value, or the default."""
        if self.uncovered_value is not _MODE_DEFAULT:
            return _uncovered_value(self.uncovered_value)
        return 0.0 if self.mode == COVERAGE_PROFILE else None


def _uncovered_value(uncovered: Any) -> float | None:
    """``uncovered`` as a finite float, or None; anything else is refused.

    NaN is refused because it is the output's marker for no uncovered
    value, and an infinity or an integer too large for a float because
    neither fills a bin with a number.
    """
    if uncovered is None:
        return None
    value = math.nan
    if not isinstance(uncovered, bool) and isinstance(uncovered, int | float):
        try:
            value = float(uncovered)
        except OverflowError:
            value = math.nan
    if not math.isfinite(value):
        raise RunDefinitionError(
            f"uncovered_value must be a number or null, not {uncovered!r}")
    return value


def _score_type(
    label: str, resource: GenomicResource, score_id: Any,
) -> str:
    """The value type of ``resource``'s score ``score_id``, which must exist."""
    definitions = FragmentScore(resource).score_definitions
    if score_id not in definitions:
        raise RunDefinitionError(
            f"{label}: resource {resource.resource_id!r} has no score "
            f"{score_id!r}; its scores are {sorted(definitions)}")
    return definitions[score_id].value_type


def _text(key: str, value: Any) -> str:
    """``value``, which must be a non-empty string; ``key`` names it."""
    if not isinstance(value, str) or not value:
        raise RunDefinitionError(
            f"{key} must be a non-empty string, not {value!r}")
    return value


def _is_single_cell(resource: GenomicResource) -> bool:
    """Whether ``resource`` has the convention's cell and count scores."""
    definitions = FragmentScore(resource).score_definitions
    return CELL_SCORE in definitions and COUNT_SCORE in definitions


def _is_labelled(resource: GenomicResource) -> bool:
    """Whether ``resource`` names its cell metadata -- the label tier."""
    return resource.get_labels().get(CELL_META_RESOURCE_ID_LABEL) \
        is not None


@dataclass(frozen=True)
class ConstantGroup:
    """One track, with a constant group name.

    Attributes
    ----------
    name
        The group.

    Raises
    ------
    RunDefinitionError
        When ``name`` is not a non-empty string.
    """

    name: str

    def __post_init__(self) -> None:
        _text("group", self.name)


@dataclass(frozen=True)
class CellGrouping:
    """One track per group of a cell metadata table.

    The ``meta`` of the entry names the table.  Without a ``meta``, the
    table is the one of the convention.

    Attributes
    ----------
    cell_score_id
        The fragment score that carries the barcode of a fragment.
    cell_meta_column
        The column of the table that holds the barcode of a cell.
    group_meta_column
        The column of the table that holds the group of a cell.

    Raises
    ------
    RunDefinitionError
        When a field is not a non-empty string.
    """

    cell_score_id: str = CELL_SCORE
    cell_meta_column: str = BARCODE_COLUMN
    group_meta_column: str = CLASS_COLUMN

    def __post_init__(self) -> None:
        for key in CELL_GROUP_DEFAULTS:
            _text(key, getattr(self, key))


@dataclass(frozen=True)
class ValueGrouping:
    """One track per distinct value of a ``str`` fragment score.

    The values are read from the full categorical histogram of the
    score when the entry is resolved.  In a pooled entry, the group of a
    value is ``<sample_id>:<value>``.  In an unpooled entry, the group
    is the value.

    Attributes
    ----------
    score_id
        The ``str`` fragment score whose values are the groups.

    Raises
    ------
    RunDefinitionError
        When ``score_id`` is not a non-empty string.
    """

    score_id: str

    def __post_init__(self) -> None:
        _text(VALUE_GROUP_KEY, self.score_id)


def _check_meta_keys(given: Iterable[str]) -> None:
    """Refuse a ``meta`` that does not give exactly one source.

    ``given`` names the source and file keys that the ``meta`` gives.
    """
    keys = set(given)
    sources = sorted(META_SOURCE_KEYS & keys)
    if len(sources) != 1:
        raise RunDefinitionError(
            f"give exactly one of resource_id, resource_label or "
            f"file_name; got {', '.join(sources) or 'none'}")
    if sources != ["file_name"] and META_FILE_KEYS & keys:
        raise RunDefinitionError(
            "file_format and file_separator go only with file_name")


@dataclass(frozen=True)
class MetaSource:
    """The cell metadata table of a cell grouping, and its row filter.

    Give exactly one source: ``resource_id``, ``resource_label`` or
    ``file_name``.

    Attributes
    ----------
    resource_id
        A ``data_frame`` resource.
    resource_label
        A label of each fragment resource.  The value of the label is
        the id of the ``data_frame`` resource of that fragment resource.
    file_name
        A local table, as given.  A relative name is read from the
        ``base_dir`` that resolves the entry, or from the current
        directory without one.
    file_format
        The format of the local table: ``csv``, ``tsv`` or ``excel``.
        When it is not given, the suffix of ``file_name`` decides:
        ``.tsv`` is ``tsv``, ``.xls`` and ``.xlsx`` are ``excel``, and
        any other suffix is ``csv``.  The field keeps what the caller
        gave, so :func:`dataclasses.replace` derives the format of a new
        ``file_name``.  :attr:`effective_file_format` is the format read.
    file_separator
        The separator of a ``csv`` or ``tsv`` table; ``None`` is the
        separator of the format.
    filters
        The conjuncts that select the rows of a fragment resource.  No
        filter selects every row.

    Raises
    ------
    RunDefinitionError
        When the source is not exactly one, a file key goes with a
        source that is not a file, or a field is malformed.
    """

    resource_id: str | None = None
    resource_label: str | None = None
    file_name: str | None = None
    file_format: str | None = None
    file_separator: str | None = None
    filters: tuple[MetaFilter, ...] = ()

    def __post_init__(self) -> None:
        _check_meta_keys(
            key for key in sorted(META_SOURCE_KEYS | META_FILE_KEYS)
            if getattr(self, key) is not None)
        object.__setattr__(self, "filters", tuple(self.filters))
        if self.resource_id is not None:
            _text("resource_id", self.resource_id)
        elif self.resource_label is not None:
            _text("resource_label", self.resource_label)
        else:
            self._check_file()

    @property
    def effective_file_format(self) -> str | None:
        """The format of the local table: the given one, or the suffix's.

        ``None`` without ``file_name``.
        """
        if self.file_format is not None or self.file_name is None:
            return self.file_format
        suffix = os.path.splitext(os.path.normpath(
            os.path.expanduser(self.file_name)))[1].lower()
        return {".tsv": "tsv", ".xls": "excel", ".xlsx": "excel"}\
            .get(suffix, "csv")

    def _check_file(self) -> None:
        _text("file_name", self.file_name)
        file_format: Any = _text("file_format", self.effective_file_format)
        if file_format not in FILE_FORMATS:
            raise RunDefinitionError(
                f"file_format must be one of {', '.join(FILE_FORMATS)}, "
                f"not {file_format!r}")
        if self.file_separator is not None:
            if file_format == "excel":
                raise RunDefinitionError(
                    "file_separator does not apply to an excel file")
            _text("file_separator", self.file_separator)

    def table_of(
        self, label: str, resource: GenomicResource, base_dir: str | None,
    ) -> MetaTable:
        """The table whose rows map ``resource``'s barcodes.

        :meta private:
        """
        if self.resource_id is not None:
            return MetaTable(resource_id=self.resource_id)
        if self.file_name is not None:
            file_format = self.effective_file_format
            assert file_format is not None
            return MetaTable(
                path=os.path.abspath(os.path.join(
                    base_dir if base_dir is not None else os.getcwd(),
                    os.path.expanduser(self.file_name))),
                file_format=file_format,
                file_separator=self.file_separator)
        assert self.resource_label is not None
        value = resource.get_labels().get(self.resource_label)
        if value is None:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} has no label "
                f"{self.resource_label!r} naming its cell metadata table; "
                f"label it, or give meta a resource_id or a file_name")
        if not isinstance(value, str) or not value:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} label "
                f"{self.resource_label!r} must name one data_frame "
                f"resource, not {value!r}")
        return MetaTable(resource_id=value)


#: ``meta`` omitted under a metadata grouping: the convention's table.
CONVENTION_META = MetaSource(
    resource_label=CELL_META_RESOURCE_ID_LABEL,
    filters=(MetaFilter(SAMPLE_ID_COLUMN, label=SAMPLE_ID_LABEL),))


#: The refusal of a ``meta`` that goes with a constant group.
CONSTANT_GROUP_META = (
    f"meta maps cells to groups, and is given only with a group of "
    f"cell_score_id, cell_meta_column and group_meta_column, or with no "
    f"group on a resource labelled {CELL_META_RESOURCE_ID_LABEL!r}")


@dataclass(frozen=True)
class FragmentEntry:
    """A ``fragment_score_binner`` entry of a run definition.

    Attributes
    ----------
    resource_query
        The repository search for the ``fragment_score`` resources.
    search_term
        The filter of the full-text index, conjoined with the query.
        ``None``, or a blank string, filters nothing.
    pool
        ``True`` merges every match into one job.  ``False`` makes one
        job for each resource.
    name
        The base of the names of the tracks.  ``None`` names the tracks
        by the query, or by the resource ids when not pooled.
    value
        What one fragment adds.
    aggregate
        How a bin reduces the values of its fragments.
    group
        How a fragment becomes a group.  ``None`` lets the labels of the
        resources decide: a resource with the
        :data:`CELL_META_RESOURCE_ID_LABEL` label is grouped as by
        ``CellGrouping()``, and any other is the one group ``all``.
    meta
        The cell metadata table of a cell grouping.  ``None`` is the
        table of the convention.

    Raises
    ------
    RunDefinitionError
        When a field is malformed, or ``meta`` goes with a constant group
        or a value grouping.
    """

    resource_query: str
    search_term: str | None = None
    pool: bool = True
    name: str | None = None
    value: ScoreContribution | ConstantContribution = ConstantContribution()
    aggregate: Aggregate = Aggregate()
    group: ConstantGroup | CellGrouping | ValueGrouping | None = None
    meta: MetaSource | None = None

    def __post_init__(self) -> None:
        query: Any = self.resource_query
        if not isinstance(query, str) or not query:
            raise RunDefinitionError(
                "resource_query is required and must be a string")
        object.__setattr__(
            self, "search_term", _search_term(self.search_term))
        pool: Any = self.pool
        if not isinstance(pool, bool):
            raise RunDefinitionError(
                f"pool must be true or false, not {pool!r}")
        if self.name is not None:
            _text("name", self.name)
        if self.meta is None:
            return
        if isinstance(self.group, ValueGrouping):
            raise RunDefinitionError(
                f"meta maps cells to groups, and is not given with a group "
                f"of {VALUE_GROUP_KEY}, whose values are the groups")
        if isinstance(self.group, ConstantGroup):
            raise RunDefinitionError(CONSTANT_GROUP_META)


def _search_term(value: Any) -> str | None:
    """``value``, a ``search_term``; ``None`` when it is blank."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise RunDefinitionError(
            f"search_term must be a string, not {value!r}")
    # A blank term filters nothing, as None does.
    return value if value.strip() else None


def _located[Made](
    where: str, make: Callable[..., Made], /, *args: Any, **kwargs: Any,
) -> Made:
    """Call ``make``; prefix its refusal with ``where``, a label."""
    try:
        return make(*args, **kwargs)
    except RunDefinitionError as err:
        raise RunDefinitionError(f"{where}: {err}") from err


def _entry_from_config(label: str, config: Any) -> FragmentEntry:
    """Read the inner config of a ``fragment_score_binner`` entry."""
    check_keys(label, config, ENTRY_KEYS)
    name = entry_name(label, config)
    return _located(
        label, FragmentEntry,
        resource_query=config.get("resource_query"),
        search_term=config.get("search_term"),
        pool=config.get("pool", True),
        name=name,
        value=_value_from_config(f"{label}.value", config.get("value")),
        aggregate=_aggregate_from_config(
            f"{label}.aggregate", config.get("aggregate")),
        group=_group_from_config(f"{label}.group", config.get("group")),
        meta=_meta_from_config(f"{label}.meta", config.get("meta")))


def _value_from_config(
    label: str, config: Any,
) -> ScoreContribution | ConstantContribution:
    if config is None:
        return ConstantContribution()
    check_keys(label, config, VALUE_KEYS)
    if len(config) != 1:
        raise RunDefinitionError(
            f"{label}: give one of score_id or value"
            f"{', not both' if config else ''}")
    if "score_id" in config:
        return _located(label, ScoreContribution, config["score_id"])
    return _located(label, ConstantContribution, config["value"])


def _aggregate_from_config(label: str, config: Any) -> Aggregate:
    if config is None:
        return Aggregate()
    _refuse_moved_to_value(label, config)
    check_keys(label, config, AGGREGATE_KEYS)
    return _located(label, Aggregate, **config)


def _refuse_moved_to_value(label: str, config: Any) -> None:
    """Refuse an ``aggregate`` key that belongs in the entry's ``value``."""
    if isinstance(config, dict):
        for key, value_key in MOVED_TO_VALUE.items():
            if key in config:
                raise RunDefinitionError(
                    f"{label}: {key} is not an aggregate key; what one "
                    f"fragment adds is the entry's value key: give "
                    f"value: {{{value_key}: {config[key]}}}")


def _group_from_config(
    label: str, config: Any,
) -> ConstantGroup | CellGrouping | ValueGrouping | None:
    if config is None:
        return None
    check_keys(label, config, GROUP_KEYS)
    forms = [
        form for form, keys in (
            ("group", {"group"}),
            ("cell_score_id, cell_meta_column and group_meta_column",
             CELL_GROUP_KEYS),
            (VALUE_GROUP_KEY, {VALUE_GROUP_KEY}))
        if keys & config.keys()]
    if len(forms) > 1:
        given = "both" if len(forms) == 2 else "all of"
        raise RunDefinitionError(
            f"{label}: give one form of group -- group; cell_score_id, "
            f"cell_meta_column and group_meta_column; or "
            f"{VALUE_GROUP_KEY} -- not {given} {'; '.join(forms)}")
    if "group" in config:
        return _located(label, ConstantGroup, config["group"])
    if VALUE_GROUP_KEY in config:
        return _located(label, ValueGrouping, config[VALUE_GROUP_KEY])
    return _located(label, CellGrouping, **config)


def _meta_from_config(label: str, config: Any) -> MetaSource | None:
    if config is None:
        return None
    check_keys(label, config, META_KEYS)
    _located(label, _check_meta_keys, config.keys())
    for source in META_SOURCE_KEYS & config.keys():
        _located(label, _text, source, config[source])
    filters = _filters_from_config(f"{label}.filter", config.get("filter"))
    return _located(label, MetaSource, filters=filters, **{
        key: config[key] for key in META_SOURCE_KEYS | META_FILE_KEYS
        if key in config})


def _filters_from_config(label: str, config: Any) -> tuple[MetaFilter, ...]:
    if config is None:
        return ()
    if not isinstance(config, list):
        raise RunDefinitionError(
            f"{label}: expected a list of {{column, value | label}} "
            f"conjuncts")
    filters = []
    for index, conjunct in enumerate(config):
        item = f"{label}[{index}]"
        check_keys(item, conjunct, FILTER_KEYS)
        _located(item, _text, "column", conjunct.get("column"))
        if ("value" in conjunct) == ("label" in conjunct):
            raise RunDefinitionError(
                f"{item}: give exactly one of value or label")
        if "label" in conjunct:
            _located(item, _text, "label", conjunct["label"])
            filters.append(_located(
                item, MetaFilter, conjunct.get("column"),
                label=conjunct["label"]))
        else:
            # A table cell matches by its text, so a number is its text.
            value = _as_text(conjunct["value"])
            filters.append(_located(
                item, MetaFilter, conjunct.get("column"),
                value="" if value is None else value))
    return tuple(filters)


def _refuse_non_numeric(
    label: str, score_id: str, matches: list[GenomicResource],
) -> None:
    """Refuse a score that a match lacks, or that is not numeric there."""
    for resource in matches:
        value_type = _score_type(label, resource, score_id)
        if value_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} score "
                f"{score_id!r} is of type {value_type!r}; only a "
                f"numeric score (int or float) can be aggregated")


class _Tables:
    """The metadata tables an entry reads, each loaded and checked once.

    ``base_dir`` is the directory that a relative local table is read
    from; ``None`` is the current directory.
    """

    def __init__(
        self, label: str, grr: GenomicResourceRepo,
        base_dir: str | None = None,
    ) -> None:
        self.label = label
        self.grr = grr
        self.base_dir = base_dir
        self.loaded: dict[MetaTable, pd.DataFrame] = {}

    def load(
        self, table: MetaTable, resource_ids: Iterable[str],
    ) -> pd.DataFrame:
        """Read ``table`` for ``resource_ids``, named in any refusal."""
        if table in self.loaded:
            return self.loaded[table]
        whose = f"resource(s) {_quoted(resource_ids)}"
        if table.resource_id is not None:
            meta = self.grr.find_resource(table.resource_id)
            if meta is None:
                raise RunDefinitionError(
                    f"{self.label}: {whose} name the cell metadata table "
                    f"{table.resource_id!r}, which the repository does "
                    f"not have")
            if meta.get_type() != "data_frame":
                raise RunDefinitionError(
                    f"{self.label}: {whose} name the cell metadata table "
                    f"{table.resource_id!r}, a {meta.get_type()} resource, "
                    f"not a data_frame")
        try:
            frame = table.load(self.grr)
        # ImportError: a legacy .xls needs xlrd, which gain does not
        # depend on.
        except (OSError, ValueError, BadZipFile, ImportError) as err:
            what = (
                f"resource {table.resource_id!r} cannot be read as a "
                f"data_frame" if table.resource_id is not None
                else f"the local file {table.path!r} cannot be read")
            raise RunDefinitionError(
                f"{self.label}: the cell metadata table of {whose}: "
                f"{what}: {err}") from err
        self.loaded[table] = frame
        return frame


def _resolve_grouping(
    label: str, cell_keys: dict[str, str], meta: MetaSource,
    resources: list[GenomicResource], tables: _Tables,
) -> tuple[BoundCellGrouping, dict[str, set[str]]]:
    """Resolve one job's metadata grouping, and each resource's groups.

    A job reads one table, so resources pooled into it must name the
    same one.
    """
    meta_label = f"{label}.meta"
    table, resource_ids = _pooled_meta_table(
        meta_label, meta, resources, tables.base_dir)
    grouping = BoundCellGrouping(
        table=table, filters=meta.filters, **cell_keys,
    )
    frame = tables.load(table, resource_ids)
    _refuse_missing_columns(meta_label, grouping, frame, resource_ids)
    prepared = grouping.prepare_filters(frame)
    groups_of: dict[str, set[str]] = {}
    for resource in resources:
        _score_type(label, resource, grouping.cell_score_id)
        _refuse_bad_filter_labels(meta_label, grouping, resource)
        try:
            mapping = grouping.groups_of(frame, resource, prepared)
        except DuplicateBarcodeError as err:
            raise RunDefinitionError(
                f"{meta_label}: in the cell metadata table {table.name!r}, "
                f"the rows of resource {resource.resource_id!r}: "
                f"{err}") from err
        groups_of[resource.resource_id] = set(mapping.values())
    return grouping, groups_of


def _pooled_meta_table(
    meta_label: str, meta: MetaSource, resources: list[GenomicResource],
    base_dir: str | None,
) -> tuple[MetaTable, list[str]]:
    """The one cell metadata table of the pooled resources, and their ids."""
    named: dict[MetaTable, list[str]] = defaultdict(list)
    for resource in resources:
        named[meta.table_of(meta_label, resource, base_dir)].append(
            resource.resource_id)
    if len(named) > 1:
        sides = "; ".join(
            f"{table.name!r} for {_quoted(ids)}"
            for table, ids in named.items())
        raise RunDefinitionError(
            f"{meta_label}: the pooled resources name different cell "
            f"metadata tables: {sides}; a pooled entry reads one table -- "
            f"give pool: false, one entry per study, or an explicit "
            f"shared meta: {{resource_id: ...}}")
    ((table, resource_ids),) = named.items()
    return table, resource_ids


def _refuse_missing_columns(
    meta_label: str, grouping: BoundCellGrouping, frame: pd.DataFrame,
    resource_ids: list[str],
) -> None:
    """Refuse a cell metadata table without a column the grouping reads."""
    columns = [
        grouping.cell_meta_column, grouping.group_meta_column,
        *(conjunct.column for conjunct in grouping.filters)]
    for column in columns:
        if column not in frame.columns:
            raise RunDefinitionError(
                f"{meta_label}: the cell metadata table "
                f"{grouping.table.name!r} of "
                f"resource(s) {_quoted(resource_ids)} has no column "
                f"{column!r}; its columns are "
                f"{[str(c) for c in frame.columns]}")


def _refuse_bad_filter_labels(
    meta_label: str, grouping: BoundCellGrouping, resource: GenomicResource,
) -> None:
    """Refuse a resource whose label a filter compares by is unusable."""
    for conjunct in grouping.filters:
        if conjunct.label is None:
            continue
        value = resource.get_labels().get(conjunct.label)
        if isinstance(value, list):
            raise RunDefinitionError(
                f"{meta_label}.filter: resource "
                f"{resource.resource_id!r} label {conjunct.label!r} "
                f"is a list, {value!r}; a filter compares a column "
                f"to one value")
        if conjunct.operand(resource) is None:
            raise RunDefinitionError(
                f"{meta_label}.filter: resource "
                f"{resource.resource_id!r} has no label "
                f"{conjunct.label!r} to filter {conjunct.column!r} by")


def _stored_histogram_type(
    resource: GenomicResource, filename: str,
) -> str | None:
    """The ``config.type`` of a stored histogram file, as written.

    ``None`` when the file is absent or is not a histogram's JSON.
    """
    try:
        with resource.open_raw_file(filename, mode="rb") as infile:
            content = infile.read()
        if filename.endswith(".gz"):
            content = gzip.decompress(content)
        data = json.loads(content)
    except (OSError, ValueError):
        return None
    config = data.get("config") if isinstance(data, dict) else None
    kind = config.get("type") if isinstance(config, dict) else None
    return kind if isinstance(kind, str) else None


def _value_groups(
    label: str, resource: GenomicResource, score_id: str,
) -> list[str]:
    """The distinct values of ``resource``'s ``str`` score, sorted.

    Read from the score's full categorical histogram; a histogram that
    cannot give every value -- absent, unpulled, annulled or truncated --
    is refused, naming the resource and how to repair it.
    """
    whose = f"{label}: resource {resource.resource_id!r} score {score_id!r}"
    rebuild = (
        "pull its data (dvc pull), or build its statistics (grr_manage "
        "repo-stats or resource-stats)")
    score = FragmentScore(resource)
    config = score.get_histogram_config(score_id)
    if isinstance(config, NullHistogramConfig):
        raise RunDefinitionError(
            f"{whose}: its definition annuls its histogram "
            f"({config.reason!r}), so no statistics list its values; "
            f"declare histogram: {{type: categorical}} on the score and "
            f"rebuild its statistics")
    try:
        histogram = score.get_score_histogram(score_id)
    except HistogramError as err:
        raise RunDefinitionError(
            f"{whose}: its full histogram, which lists the groups, cannot "
            f"be read: {err}; {rebuild}") from err
    if isinstance(histogram, NullHistogram):
        # The statistics scan stores a null histogram for a categorical
        # config only when its values pass the cap, which only a config
        # without enforce_type has.  Anything else read back as null --
        # no stored file, or one the scan did not write -- is rebuilt.
        if isinstance(config, CategoricalHistogramConfig) \
                and not config.enforce_type \
                and _stored_histogram_type(
                    resource, score.get_histogram_filename(score_id),
                ) == "null":
            raise RunDefinitionError(
                f"{whose} has more distinct values than the default "
                f"categorical histogram keeps "
                f"({CategoricalHistogram.UNIQUE_VALUES_LIMIT}): "
                f"{histogram.reason!r}; declare histogram: {{type: "
                f"categorical}} on the score and rebuild its statistics")
        raise RunDefinitionError(
            f"{whose} has no histogram listing its values: "
            f"{histogram.reason!r}; {rebuild}")
    if not isinstance(histogram, CategoricalHistogram):
        raise RunDefinitionError(
            f"{whose} has a {histogram.type}, not the categorical "
            f"histogram listing its values")
    if histogram.truncated:
        raise RunDefinitionError(
            f"{whose}: its histogram is truncated and does not list every "
            f"value; {rebuild}")
    return sorted({
        text for text in map(_as_text, histogram.raw_values)
        if text is not None})


def _resolve_value_grouping(
    label: str, score_id: str, aggregated_score: str | None,
    resources: list[GenomicResource], *, pool: bool,
) -> tuple[BoundValueGrouping, list[str]]:
    """Resolve one job's raw-value grouping, and its sorted groups.

    Pooled, each resource's groups are prefixed with its
    :data:`SAMPLE_ID_LABEL` label, which each must carry, without a
    ``:``, so that a group name splits back one way only, and no two
    may share, so that two resources' cells never reach one track.
    """
    group_label = f"{label}.group"
    prefixes = [""] * len(resources)
    if pool:
        prefixes = _pooled_prefixes(group_label, resources)
    values = [
        _resource_value_groups(
            group_label, resource, score_id, aggregated_score)
        for resource in resources]
    groups = _union_groups(
        group_label, score_id, resources, prefixes, values)
    return (
        BoundValueGrouping(score_id, tuple(prefixes), tuple(values)),
        groups)


def _pooled_prefixes(
    group_label: str, resources: list[GenomicResource],
) -> list[str]:
    """Check the pooled resources' sample labels; prefix each with its own."""
    sample_ids = [
        None if isinstance(value, list) else _as_text(value)
        for value in (
            r.get_labels().get(SAMPLE_ID_LABEL) for r in resources)]
    _refuse_unlabelled_samples(group_label, resources, sample_ids)
    _refuse_coloned_samples(group_label, resources, sample_ids)
    _refuse_shared_samples(group_label, resources, sample_ids)
    return [f"{sample_id}:" for sample_id in sample_ids]


def _refuse_unlabelled_samples(
    group_label: str, resources: list[GenomicResource],
    sample_ids: list[str | None],
) -> None:
    unlabelled = [
        r.resource_id
        for r, sample_id in zip(resources, sample_ids, strict=True)
        if sample_id is None]
    if unlabelled:
        raise RunDefinitionError(
            f"{group_label}: a pooled {VALUE_GROUP_KEY} grouping "
            f"prefixes each group with the resource's "
            f"{SAMPLE_ID_LABEL!r} label, which {_quoted(unlabelled)} "
            f"do not carry as one value; label them, or give "
            f"pool: false")


def _refuse_coloned_samples(
    group_label: str, resources: list[GenomicResource],
    sample_ids: list[str | None],
) -> None:
    coloned = [
        f"{r.resource_id!r} ({sample_id!r})"
        for r, sample_id in zip(resources, sample_ids, strict=True)
        if sample_id is not None and ":" in sample_id]
    if coloned:
        raise RunDefinitionError(
            f"{group_label}: a pooled {VALUE_GROUP_KEY} grouping "
            f"names each group <{SAMPLE_ID_LABEL}>:<value>, so the "
            f"{SAMPLE_ID_LABEL!r} label may not contain ':'; "
            f"{', '.join(coloned)} does; relabel it, or give "
            f"pool: false")


def _refuse_shared_samples(
    group_label: str, resources: list[GenomicResource],
    sample_ids: list[str | None],
) -> None:
    sharing: dict[str, list[str]] = {}
    for resource, sample_id in zip(resources, sample_ids, strict=True):
        assert sample_id is not None
        sharing.setdefault(sample_id, []).append(resource.resource_id)
    shared = [
        f"{_quoted(ids)} ({sample_id!r})"
        for sample_id, ids in sharing.items() if len(ids) > 1]
    if shared:
        raise RunDefinitionError(
            f"{group_label}: a pooled {VALUE_GROUP_KEY} grouping "
            f"keeps samples apart by their {SAMPLE_ID_LABEL!r} label, "
            f"and {'; '.join(shared)} share one, which would merge "
            f"their cells into the same tracks; label each resource "
            f"with its own sample, or give pool: false")


def _resource_value_groups(
    group_label: str, resource: GenomicResource, score_id: str,
    aggregated_score: str | None,
) -> tuple[str, ...]:
    """Check one resource's grouping score; read the values it lists."""
    if score_id == aggregated_score:
        raise RunDefinitionError(
            f"{group_label}: resource {resource.resource_id!r} score "
            f"{score_id!r} is the aggregated score; group by another "
            f"score")
    value_type = _score_type(group_label, resource, score_id)
    if value_type != "str":
        raise RunDefinitionError(
            f"{group_label}: resource {resource.resource_id!r} score "
            f"{score_id!r} is of type {value_type!r}; only a str "
            f"score's values group")
    return tuple(_value_groups(group_label, resource, score_id))


def _union_groups(
    group_label: str, score_id: str, resources: list[GenomicResource],
    prefixes: list[str], values: list[tuple[str, ...]],
) -> list[str]:
    """Return the sorted union of every resource's prefixed groups."""
    groups = {
        prefix + value
        for prefix, of_resource in zip(prefixes, values, strict=True)
        for value in of_resource}
    if not groups:
        raise RunDefinitionError(
            f"{group_label}: the {score_id!r} histograms of "
            f"{_quoted(r.resource_id for r in resources)} list no value")
    return sorted(groups)
