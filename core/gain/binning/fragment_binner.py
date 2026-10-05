"""``fragment_score_binner``: fragments per bin, by group, pooled or not.

An entry names a query over ``fragment_score`` resources and produces,
per grid bin, an aggregate over the fragments that START in that bin --
the partition :meth:`FragmentScore.get_fragment_scores_starting_in_region`
reads, so adjacent regions see each fragment exactly once.

An entry's keys:

- ``resource_query`` (required) and ``search_term``: the matched
  ``fragment_score`` resources, ordered by resource id.
- ``pool`` (default ``true``): every match is merged by position into
  one set of group tracks, named ``<resource_query>:<group>``; with
  ``false`` each resource is its own job, its tracks named
  ``<resource id>:<group>``.
- ``group``: ``{group: NAME}``, one track with a constant name, or
  ``{cell_score_id, cell_meta_column, group_meta_column}``, one track
  per group of a metadata table, which ``meta`` names; each of the three
  keys is optional and defaults to the convention's (below), so
  ``group: {}`` groups by the convention throughout; or
  ``{group_score_id: S}``, one track per distinct value of the ``str``
  score ``S``, read when the entry is resolved from the score's full
  categorical histogram (its statistics must be built and pulled).
  Pooled, a value's group is ``<sample_id>:<value>``, by the resource's
  :data:`SAMPLE_ID_LABEL` label, which every pooled resource must carry,
  since one barcode recurs in every sample; unpooled, the bare value.
  The forms do not mix, and ``meta`` does not go with the last.
  Omitted, the resource's labels decide: a resource carrying
  :data:`CELL_META_RESOURCE_ID_LABEL` is grouped as with ``group: {}``,
  any other is the one group ``all``.
- ``aggregate``: ``{score: S, aggregator: A}``, the score's value per
  fragment, or ``{value: V, aggregator: A}``, a constant per fragment.
  ``aggregator`` omitted means ``sum``.  ``aggregate`` omitted sums the
  :data:`COUNT_SCORE` score when the resource has one of type ``int``,
  and otherwise counts fragments, ``{value: 1, aggregator: sum}``.
- ``meta``: exactly one source of the table mapping barcodes to groups
  -- ``resource_id: ID``, a ``data_frame`` resource; ``resource_label:
  LABEL``, the ``data_frame`` resource the fragment resource's label
  ``LABEL`` names; or ``file_name`` (with optional ``file_format``, one
  of ``csv``, ``tsv`` or ``excel``, and ``file_separator``), a local
  table, a relative name read from the run definition's directory --
  and ``filter: [{column, value | label}]``, the conjuncts selecting a
  sample's rows: a table column equal to a literal (``value``) or to the
  fragment resource's label of that name (``label``).  No filter selects
  every row.  Omitted under a metadata grouping, ``meta`` is the
  convention's: the table the :data:`CELL_META_RESOURCE_ID_LABEL` label
  names, its :data:`SAMPLE_ID_COLUMN` rows equal to the
  :data:`SAMPLE_ID_LABEL` label.

A pooled entry resolves to one job, so its resources must agree: on
whether they carry :data:`CELL_META_RESOURCE_ID_LABEL` (when ``group``
is omitted), on the one metadata table their ``meta`` names, and (when
``aggregate`` is omitted) on whether they have an ``int``
:data:`COUNT_SCORE` score.

A fragment whose barcode is not in the filtered rows, or whose row's
group is empty, or whose ``group_score_id`` value its resource's
histogram does not list, reaches no track; it is counted as dropped, per
resource and region.  An empty bin holds the fold's empty value -- 0 for
``count`` and ``sum``, NaN for the others -- and so does every bin of a
contig a resource lacks.
"""
from __future__ import annotations

import heapq
import json
import math
import operator
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, ClassVar
from zipfile import BadZipFile

import numpy as np
import numpy.typing as npt
import pandas as pd

from gain import logging
from gain.binning.binners import (
    NUMERIC_VALUE_TYPES,
    BinningJob,
    RunDefinitionError,
    Track,
    check_keys,
    match_resources,
)
from gain.genomic_resources.data_frame_resource import (
    load_data_frame_from_resource,
)
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.aggregation import (
    EMPTY_BIN_VALUES,
    fold_into_bins,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    HistogramError,
    NullHistogram,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.utils.regions import BedRegion

logger = logging.getLogger(__name__)

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
#: The fragment score summed by default, when it is an ``int``.
COUNT_SCORE = "count"

#: The group of an entry that names none.
DEFAULT_GROUP = "all"

ENTRY_KEYS = frozenset({
    "resource_query", "search_term", "pool", "group", "aggregate", "meta"})
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
AGGREGATE_KEYS = frozenset({"score", "value", "aggregator"})
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

    The operand is the literal ``value``, or -- when ``label`` is set --
    the value of that label on the fragment resource whose rows are
    being selected.
    """

    column: str
    value: str | None = None
    label: str | None = None

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
class CellGrouping:
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

    def groups_of(
        self, table: pd.DataFrame, resource: GenomicResource,
    ) -> dict[str, str]:
        """Map each barcode of ``resource``'s rows to its group.

        The rows are those every filter conjunct selects, as a boolean
        mask; a row whose group is empty names no group and is left out,
        so its barcode maps nowhere.  A barcode on more than one row is
        a :class:`DuplicateBarcodeError`.
        """
        mask = np.ones(len(table), dtype=bool)
        for conjunct in self.filters:
            operand = conjunct.operand(resource)
            mask &= np.array([
                _as_text(cell) == operand
                for cell in table[conjunct.column].tolist()], dtype=bool)
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
        return [
            {
                barcode: index_of[group]
                for barcode, group in self.groups_of(table, resource).items()
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
class ValueGrouping:
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
            f"their {self.group_score_id!r} value not among the score's "
            f"histogram values")

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
class FragmentBinningJob(BinningJob):
    """A fragment job: its tracks, and how each fragment reaches one.

    ``resource_ids`` are the resources read together, in resource-id
    order -- every match of a pooled entry, or one resource of an
    unpooled one.  A fragment contributes its ``score_id`` score's value
    when the entry aggregates a score, else the constant ``value``.  With
    a ``grouping`` a fragment reaches the track of its barcode's group,
    or none; without one, the job's one track.  ``warnings`` are what a
    dry run tells the user about how the job was resolved.
    """

    resource_ids: tuple[str, ...] = ()
    score_id: str | None = None
    value: float = 1
    grouping: CellGrouping | ValueGrouping | None = None
    warnings: tuple[str, ...] = ()

    @property
    def local_files(self) -> tuple[str, ...]:
        """The local metadata files the job reads, as absolute paths."""
        if not isinstance(self.grouping, CellGrouping) \
                or self.grouping.table.path is None:
            return ()
        return (self.grouping.table.path,)


@dataclass(frozen=True)
class _Aggregate:
    """What a fragment contributes, and how a bin reduces the contributions.

    Exactly one of a score or a constant ``value``; an omitted
    aggregator is ``sum``.
    """

    score_id: str | None
    value: float
    aggregator: str

    @classmethod
    def parse(
        cls, label: str, config: Any, matches: list[GenomicResource],
    ) -> _Aggregate:
        """Resolve an entry's ``aggregate`` block against its matches.

        A score must be numeric in every matched resource, and the
        aggregator one the binned fold accepts.
        """
        check_keys(label, config, AGGREGATE_KEYS)
        if "score" in config and "value" in config:
            raise RunDefinitionError(
                f"{label}: give one of score or value, not both")
        aggregator = _require_text(
            label, "aggregator", config.get("aggregator", "sum"))
        if aggregator not in EMPTY_BIN_VALUES:
            raise RunDefinitionError(
                f"{label}: aggregator {aggregator!r} does not produce a "
                f"number; use one of {', '.join(sorted(EMPTY_BIN_VALUES))}")
        if "score" not in config:
            value = config.get("value", 1)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise RunDefinitionError(
                    f"{label}: value must be a number, not {value!r}")
            return cls(score_id=None, value=value, aggregator=aggregator)
        score_id = _require_text(label, "score", config["score"])
        for resource in matches:
            value_type = _score_type(label, resource, score_id)
            if value_type not in NUMERIC_VALUE_TYPES:
                raise RunDefinitionError(
                    f"{label}: resource {resource.resource_id!r} score "
                    f"{score_id!r} is of type {value_type!r}; only a "
                    f"numeric score (int or float) can be aggregated")
        return cls(score_id=score_id, value=1, aggregator=aggregator)

    @classmethod
    def default(
        cls, label: str, resources: list[GenomicResource],
    ) -> _Aggregate:
        """The aggregate of an entry that states none (F8).

        The sum of :data:`COUNT_SCORE` when the resources have it as an
        ``int``, else the fragment count; resources pooled into one job
        must agree on which.
        """
        summed, counted = _partition(resources, _has_int_count)
        if not summed:
            return cls(score_id=None, value=1, aggregator="sum")
        if counted:
            raise RunDefinitionError(
                f"{label}: the pooled resources disagree on the default "
                f"aggregate: {_quoted(summed)} have an int "
                f"{COUNT_SCORE!r} score, summed by default, while "
                f"{_quoted(counted)} do not, and count fragments; state "
                f"the aggregate")
        return cls(score_id=COUNT_SCORE, value=1, aggregator="sum")


def _has_int_count(resource: GenomicResource) -> bool:
    definition = FragmentScore(resource).score_definitions.get(COUNT_SCORE)
    return definition is not None and definition.value_type == "int"


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


def _require_text(label: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise RunDefinitionError(
            f"{label}: {key} must be a non-empty string, not {value!r}")
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
class _Group:
    """An entry's ``group``: at most one of its three forms.

    The ``constant`` group name, the metadata grouping's three
    ``cell_keys``, or the ``value_score`` whose raw values group; all
    ``None`` when the entry gives no ``group``, so that the resources'
    labels decide.
    """

    constant: str | None = None
    cell_keys: dict[str, str] | None = None
    value_score: str | None = None

    @classmethod
    def parse(cls, label: str, config: Any) -> _Group:
        """Parse an entry's ``group`` block; ``None`` when it is omitted."""
        if config is None:
            return cls()
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
            return cls(constant=_require_text(label, "group", config["group"]))
        if VALUE_GROUP_KEY in config:
            return cls(value_score=_require_text(
                label, VALUE_GROUP_KEY, config[VALUE_GROUP_KEY]))
        return cls(cell_keys={
            key: _require_text(label, key, config.get(key, default))
            for key, default in CELL_GROUP_DEFAULTS.items()})


def _parse_filters(label: str, config: Any) -> tuple[MetaFilter, ...]:
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
        column = _require_text(item, "column", conjunct.get("column"))
        if ("value" in conjunct) == ("label" in conjunct):
            raise RunDefinitionError(
                f"{item}: give exactly one of value or label")
        if "label" in conjunct:
            filters.append(MetaFilter(column, label=_require_text(
                item, "label", conjunct["label"])))
        else:
            value = _as_text(conjunct["value"])
            if value is None:
                raise RunDefinitionError(
                    f"{item}: value must not be empty")
            filters.append(MetaFilter(column, value=value))
    return tuple(filters)


@dataclass(frozen=True)
class _MetaSpec:
    """A ``meta`` block: one table source and the filter of its rows.

    The source is a fixed ``table`` -- a resource id or a local file --
    or ``resource_label``, the label whose value on each fragment
    resource is the id of its table.
    """

    filters: tuple[MetaFilter, ...]
    table: MetaTable | None = None
    resource_label: str | None = None

    @classmethod
    def parse(
        cls, label: str, config: Any, base_dir: str | None,
    ) -> _MetaSpec:
        """Parse a ``meta`` block; a local file is read from ``base_dir``."""
        check_keys(label, config, META_KEYS)
        sources = sorted(META_SOURCE_KEYS & config.keys())
        if len(sources) != 1:
            raise RunDefinitionError(
                f"{label}: give exactly one of resource_id, resource_label "
                f"or file_name; got {', '.join(sources) or 'none'}")
        (source,) = sources
        if source != "file_name" and META_FILE_KEYS & config.keys():
            raise RunDefinitionError(
                f"{label}: file_format and file_separator go only with "
                f"file_name")
        filters = _parse_filters(f"{label}.filter", config.get("filter"))
        if source == "resource_id":
            return cls(filters, table=MetaTable(resource_id=_require_text(
                label, "resource_id", config["resource_id"])))
        if source == "resource_label":
            return cls(filters, resource_label=_require_text(
                label, "resource_label", config["resource_label"]))
        return cls(filters, table=_parse_local_table(label, config, base_dir))

    def table_of(self, label: str, resource: GenomicResource) -> MetaTable:
        """The table whose rows map ``resource``'s barcodes."""
        if self.table is not None:
            return self.table
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
CONVENTION_META = _MetaSpec(
    filters=(MetaFilter(SAMPLE_ID_COLUMN, label=SAMPLE_ID_LABEL),),
    resource_label=CELL_META_RESOURCE_ID_LABEL)


def _parse_local_table(
    label: str, config: dict[str, Any], base_dir: str | None,
) -> MetaTable:
    """A local table, its relative name read from ``base_dir``.

    Made absolute here, since the tasks run inside the work directory.
    """
    file_name = _require_text(label, "file_name", config["file_name"])
    path = os.path.abspath(os.path.join(
        base_dir if base_dir is not None else os.getcwd(),
        os.path.expanduser(file_name)))
    file_format = config.get("file_format")
    if file_format is None:
        suffix = os.path.splitext(path)[1].lower()
        file_format = {".tsv": "tsv", ".xls": "excel", ".xlsx": "excel"}\
            .get(suffix, "csv")
    file_format = _require_text(label, "file_format", file_format)
    if file_format not in FILE_FORMATS:
        raise RunDefinitionError(
            f"{label}: file_format must be one of "
            f"{', '.join(FILE_FORMATS)}, not {file_format!r}")
    separator = config.get("file_separator")
    if separator is not None:
        if file_format == "excel":
            raise RunDefinitionError(
                f"{label}: file_separator does not apply to an excel file")
        separator = _require_text(label, "file_separator", separator)
    return MetaTable(
        path=path, file_format=file_format, file_separator=separator)


class _Tables:
    """The metadata tables an entry reads, each loaded and checked once."""

    def __init__(self, label: str, grr: GenomicResourceRepo) -> None:
        self.label = label
        self.grr = grr
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
    label: str, cell_keys: dict[str, str], meta: _MetaSpec,
    resources: list[GenomicResource], tables: _Tables,
) -> tuple[CellGrouping, dict[str, set[str]]]:
    """Resolve one job's metadata grouping, and each resource's groups.

    A job reads one table, so resources pooled into it must name the
    same one.
    """
    meta_label = f"{label}.meta"
    named: dict[MetaTable, list[str]] = defaultdict(list)
    for resource in resources:
        named[meta.table_of(meta_label, resource)].append(
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
    grouping = CellGrouping(table=table, filters=meta.filters, **cell_keys)
    frame = tables.load(table, resource_ids)
    columns = [
        grouping.cell_meta_column, grouping.group_meta_column,
        *(conjunct.column for conjunct in grouping.filters)]
    for column in columns:
        if column not in frame.columns:
            raise RunDefinitionError(
                f"{meta_label}: the cell metadata table {table.name!r} of "
                f"resource(s) {_quoted(resource_ids)} has no column "
                f"{column!r}; its columns are "
                f"{[str(c) for c in frame.columns]}")
    groups_of: dict[str, set[str]] = {}
    for resource in resources:
        _score_type(label, resource, grouping.cell_score_id)
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
        try:
            mapping = grouping.groups_of(frame, resource)
        except DuplicateBarcodeError as err:
            raise RunDefinitionError(
                f"{meta_label}: in the cell metadata table {table.name!r}, "
                f"the rows of resource {resource.resource_id!r}: "
                f"{err}") from err
        groups_of[resource.resource_id] = set(mapping.values())
    return grouping, groups_of


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
    try:
        histogram = FragmentScore(resource).get_score_histogram(score_id)
    except HistogramError as err:
        raise RunDefinitionError(
            f"{whose}: its full histogram, which lists the groups, cannot "
            f"be read: {err}; {rebuild}") from err
    if isinstance(histogram, NullHistogram):
        if "Too many unique values" in histogram.reason:
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
    label: str, score_id: str, aggregate: _Aggregate,
    resources: list[GenomicResource], *, pool: bool,
) -> tuple[ValueGrouping, list[str]]:
    """Resolve one job's raw-value grouping, and its sorted groups.

    Pooled, each resource's groups are prefixed with its
    :data:`SAMPLE_ID_LABEL` label, which each must carry, without a
    ``:``, so that a group name splits back one way only.
    """
    group_label = f"{label}.group"
    prefixes = [""] * len(resources)
    if pool:
        sample_ids = [
            None if isinstance(value, list) else _as_text(value)
            for value in (
                r.get_labels().get(SAMPLE_ID_LABEL) for r in resources)]
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
        prefixes = [f"{sample_id}:" for sample_id in sample_ids]
    values = []
    for resource in resources:
        if score_id == aggregate.score_id:
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
        values.append(tuple(_value_groups(group_label, resource, score_id)))
    groups = {
        prefix + value
        for prefix, of_resource in zip(prefixes, values, strict=True)
        for value in of_resource}
    if not groups:
        raise RunDefinitionError(
            f"{group_label}: the {score_id!r} histograms of "
            f"{_quoted(r.resource_id for r in resources)} list no value")
    return (
        ValueGrouping(score_id, tuple(prefixes), tuple(values)),
        sorted(groups))


@dataclass(frozen=True)
class _Entry:
    """An entry's parsed keys, before the defaults its resources decide.

    ``aggregate`` and ``meta`` ``None`` mean none was given.
    """

    label: str
    pool: bool
    aggregate: _Aggregate | None
    group: _Group
    meta: _MetaSpec | None
    tables: _Tables


class FragmentScoreBinding:
    """A fragment job's resources, open for as long as the ``with`` lasts.

    Each resource is its own :class:`FragmentScore`, so the
    one-live-read-per-score limit holds however many are pooled.  Each
    resource's barcode-to-track map is built once, when bound.

    With a cell grouping, ``dropped`` maps each (resource id, region)
    binned so far -- the region as ``chrom:start-stop`` -- to the number
    of its fragments that reached no track: a barcode absent from the
    resource's filtered rows, or a row of no group.
    """

    def __init__(
        self, job: FragmentBinningJob, scores: list[FragmentScore],
        track_maps: list[dict[str, int]] | None,
    ) -> None:
        self.job = job
        self.scores = scores
        self.track_maps = track_maps
        self.dropped: dict[tuple[str, str], int] = {}

    def __enter__(self) -> FragmentScoreBinding:
        opened: list[FragmentScore] = []
        try:
            for score in self.scores:
                score.open()
                opened.append(score)
        except BaseException:
            for score in opened:
                score.close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        for score in self.scores:
            score.close()

    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """Fold the fragments starting in ``region`` into the job's tracks.

        One starting-in read per resource, merged by fragment start and
        folded through :func:`fold_into_bins`, one aggregator per track.
        A resource without the region's contig contributes nothing.
        Every read is drained, or closed on failure, before this returns,
        so no read outlives the call.  With a cell grouping, each
        resource's dropped fragments are counted into :attr:`dropped` and
        logged.
        """
        dropped = [0] * len(self.scores)
        reads = [
            self._records(index, score, region, dropped)
            for index, score in enumerate(self.scores)
            if score.has_chromosome(region.chrom)
        ]
        try:
            block = fold_into_bins(
                heapq.merge(*reads, key=operator.itemgetter(0)),
                start=region.start, end=region.stop, bin_size=bin_size,
                aggregators=[track.aggregator for track in self.job.tracks])
        finally:
            for read in reads:
                read.close()
        if self.job.grouping is not None:
            where = f"{region.chrom}:{region.start}-{region.stop}"
            for resource_id, count in zip(
                    self.job.resource_ids, dropped, strict=True):
                self.dropped[resource_id, where] = count
                logger.info(
                    "%s %s: %d fragments dropped, %s",
                    resource_id, where, count,
                    self.job.grouping.dropped_reason)
        return block

    def _records(
        self, index: int, score: FragmentScore, region: BedRegion,
        dropped: list[int],
    ) -> Generator[tuple[int, int, ScoreValue], None, None]:
        """``(start, track, value)`` per fragment of one resource.

        A fragment whose barcode maps to no track is dropped, and counted
        in ``dropped[index]``.
        """
        job = self.job
        scores = [] if job.score_id is None else [job.score_id]
        if job.grouping is not None:
            scores.append(job.grouping.score_id)
        rows = score.get_fragment_scores_starting_in_region(
            region.chrom, region.start, region.stop, scores=scores)
        track_of = None if self.track_maps is None \
            else self.track_maps[index]
        try:
            for begin, _, values in rows:
                track = 0 if track_of is None \
                    else track_of.get(_as_text(values[-1]) or "")
                if track is None:
                    dropped[index] += 1
                    continue
                yield begin, track, \
                    job.value if job.score_id is None else values[0]
        finally:
            rows.close()


class FragmentScoreBinner:
    """Bins ``fragment_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "fragment_score_binner"

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
        *, base_dir: str | None = None,
    ) -> list[BinningJob]:
        """Resolve one entry into a pooled job, or one job per resource.

        A job's tracks are its groups: the constant one, or every
        distinct non-empty group of its resources' filtered rows, in
        sorted order.  ``base_dir`` is the directory a relative local
        ``meta`` file is read from; unset, the current directory.
        """
        check_keys(label, config, ENTRY_KEYS)
        matches = match_resources(label, config, grr, "fragment_score")
        pool = config.get("pool", True)
        if not isinstance(pool, bool):
            raise RunDefinitionError(
                f"{label}: pool must be true or false, not {pool!r}")
        entry = _Entry(
            label=label,
            pool=pool,
            aggregate=None if config.get("aggregate") is None
            else _Aggregate.parse(
                f"{label}.aggregate", config["aggregate"], matches),
            group=_Group.parse(f"{label}.group", config.get("group")),
            meta=None if config.get("meta") is None
            else _MetaSpec.parse(f"{label}.meta", config["meta"], base_dir),
            tables=_Tables(f"{label}.meta", grr))
        return [
            cls._resolve_job(
                entry, resources,
                base=config["resource_query"] if pool
                else resources[0].resource_id)
            for resources in ([matches] if pool else [[r] for r in matches])
        ]

    @classmethod
    def _resolve_job(
        cls, entry: _Entry, resources: list[GenomicResource], *, base: str,
    ) -> FragmentBinningJob:
        """Resolve the defaults and the grouping of one job's resources."""
        label, aggregate, meta = entry.label, entry.aggregate, entry.meta
        constant, cell_keys = entry.group.constant, entry.group.cell_keys
        warnings: list[str] = []
        if entry.group.value_score is not None:
            if meta is not None:
                raise RunDefinitionError(
                    f"{label}: meta maps cells to groups, and is not given "
                    f"with a group of {VALUE_GROUP_KEY}, whose values are "
                    f"the groups, for "
                    f"{_quoted(r.resource_id for r in resources)}")
            if aggregate is None:
                aggregate = _Aggregate.default(
                    f"{label}.aggregate", resources)
            value_grouping, groups = _resolve_value_grouping(
                label, entry.group.value_score, aggregate, resources,
                pool=entry.pool)
            return cls._job_of(
                tuple(r.resource_id for r in resources), aggregate,
                value_grouping, groups, base=base, warnings=())
        if constant is None and cell_keys is None:
            # No group given: the label tier decides (F8).
            labelled, unlabelled = _partition(resources, _is_labelled)
            if labelled and unlabelled:
                raise RunDefinitionError(
                    f"{label}: the pooled resources disagree on the label "
                    f"{CELL_META_RESOURCE_ID_LABEL!r} that decides their "
                    f"grouping: {_quoted(labelled)} carry it and "
                    f"{_quoted(unlabelled)} do not; give group, or "
                    f"pool: false")
            if labelled:
                cell_keys = dict(CELL_GROUP_DEFAULTS)
            else:
                constant = DEFAULT_GROUP
                warnings.extend(
                    f"resource {r.resource_id!r} has {CELL_SCORE!r} and "
                    f"{COUNT_SCORE!r} scores but no "
                    f"{CELL_META_RESOURCE_ID_LABEL!r} label; it is binned "
                    f"as the single {DEFAULT_GROUP!r} track"
                    for r in resources if _is_single_cell(r))
        if aggregate is None:
            aggregate = _Aggregate.default(f"{label}.aggregate", resources)
        grouping = None
        if cell_keys is None:
            assert constant is not None
            if meta is not None:
                raise RunDefinitionError(
                    f"{label}: meta maps cells to groups, and is given only "
                    f"with a group of cell_score_id, cell_meta_column and "
                    f"group_meta_column, or with no group on a resource "
                    f"labelled {CELL_META_RESOURCE_ID_LABEL!r}")
            groups = [constant]
        else:
            grouping, groups_of = _resolve_grouping(
                label, cell_keys, meta or CONVENTION_META, resources,
                entry.tables)
            groups = sorted(set[str]().union(*groups_of.values()))
            if not groups:
                raise RunDefinitionError(
                    f"{label}: the meta rows selected for "
                    f"{', '.join(groups_of)} name no group")
            if grouping.table.is_local:
                warnings.append(
                    f"the cell metadata table {grouping.table.name!r} is a "
                    f"local file; the run is not reproducible elsewhere")
        return cls._job_of(
            tuple(r.resource_id for r in resources), aggregate, grouping,
            groups, base=base, warnings=tuple(warnings))

    @classmethod
    def _job_of(
        cls, resource_ids: tuple[str, ...], aggregate: _Aggregate,
        grouping: CellGrouping | ValueGrouping | None, groups: list[str],
        *, base: str,
        warnings: tuple[str, ...],
    ) -> FragmentBinningJob:
        parameters: dict[str, Any] = {}
        if aggregate.score_id is None:
            parameters["value"] = aggregate.value
        if grouping is not None:
            parameters.update(grouping.parameters)
        parameters_text = (
            json.dumps(parameters, sort_keys=True) if parameters else "")
        tracks = tuple(
            Track(
                name=f"{base}:{group}",
                resource_ids=resource_ids,
                group=group,
                score_id=aggregate.score_id or "",
                aggregator=aggregate.aggregator,
                none_value_replacement=None,
                binner=cls.kind,
                parameters=parameters_text,
            )
            for group in groups
        )
        return FragmentBinningJob(
            binner=cls.kind, tracks=tracks, resource_ids=resource_ids,
            score_id=aggregate.score_id, value=aggregate.value,
            grouping=grouping, warnings=warnings)

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo,
    ) -> FragmentScoreBinding:
        """Bind the job to one :class:`FragmentScore` per resource.

        With a cell grouping, the metadata table is read once and each
        resource's barcodes are mapped to the job's track indexes.
        """
        assert isinstance(job, FragmentBinningJob)
        resources = [
            grr.get_resource(resource_id) for resource_id in job.resource_ids]
        track_maps = None
        if job.grouping is not None:
            track_maps = job.grouping.track_maps(grr, resources, {
                track.group: index
                for index, track in enumerate(job.tracks)})
        return FragmentScoreBinding(
            job, [FragmentScore(resource) for resource in resources],
            track_maps)
