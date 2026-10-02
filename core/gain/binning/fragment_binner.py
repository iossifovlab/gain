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
  per group of a metadata table, which ``meta`` names.  Absent, the one
  group is ``all``.
- ``aggregate``: ``{score: S, aggregator: A}``, the score's value per
  fragment, or ``{value: V, aggregator: A}``, a constant per fragment.
  ``aggregator`` omitted means ``sum``; ``aggregate`` omitted counts
  fragments, ``{value: 1, aggregator: sum}``.
- ``meta``: ``{resource_id: ID, filter: [{column, value | label}]}``, the
  ``data_frame`` resource mapping barcodes to groups and the conjuncts
  selecting a sample's rows: a table column equal to a literal
  (``value``) or to the fragment resource's label of that name
  (``label``).

A fragment whose barcode is not in the filtered rows, or whose row's
group is empty, reaches no track.  An empty bin holds the fold's empty
value -- 0 for ``count`` and ``sum``, NaN for the others -- and so does
every bin of a contig a resource lacks.
"""
from __future__ import annotations

import heapq
import json
import math
import operator
from collections.abc import Generator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pandas as pd

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
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.utils.regions import BedRegion

#: The group of an entry that names none.
DEFAULT_GROUP = "all"

ENTRY_KEYS = frozenset({
    "resource_query", "search_term", "pool", "group", "aggregate", "meta"})
CELL_GROUP_KEYS = frozenset({
    "cell_score_id", "cell_meta_column", "group_meta_column"})
GROUP_KEYS = frozenset({"group"}) | CELL_GROUP_KEYS
AGGREGATE_KEYS = frozenset({"score", "value", "aggregator"})
META_KEYS = frozenset({"resource_id", "filter"})
FILTER_KEYS = frozenset({"column", "value", "label"})


def _as_text(value: Any) -> str | None:
    """A table cell, a label or a score value as the text it is matched by.

    ``None`` for a missing value -- ``None``, NaN or the empty string --
    which matches nothing and names no group.  An integral float is
    written as an int, since a numeric column read with a missing cell
    is a float column, and its ``2`` must still match a label ``2``.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value)
    return text or None


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
class CellGrouping:
    """How a fragment's barcode becomes a group: a filtered table lookup.

    ``cell_score_id`` is the fragment score carrying the barcode; the
    ``data_frame`` resource ``meta_resource_id``, its rows selected per
    fragment resource by ``filters``, maps the barcode in
    ``cell_meta_column`` to the group in ``group_meta_column``.
    """

    cell_score_id: str
    cell_meta_column: str
    group_meta_column: str
    meta_resource_id: str
    filters: tuple[MetaFilter, ...]

    def load_table(self, grr: GenomicResourceRepo) -> pd.DataFrame:
        return load_data_frame_from_resource(
            grr.get_resource(self.meta_resource_id))

    def groups_of(
        self, table: pd.DataFrame, resource: GenomicResource,
    ) -> dict[str, str]:
        """Map each barcode of ``resource``'s rows to its group.

        The rows are those every filter conjunct selects, as a boolean
        mask; a row whose group is empty names no group and is left out,
        so its barcode maps nowhere.
        """
        mask = np.ones(len(table), dtype=bool)
        for conjunct in self.filters:
            operand = conjunct.operand(resource)
            mask &= np.array([
                _as_text(cell) == operand
                for cell in table[conjunct.column].tolist()], dtype=bool)
        rows = table[mask]
        mapping: dict[str, str] = {}
        for barcode, group in zip(
                rows[self.cell_meta_column].tolist(),
                rows[self.group_meta_column].tolist(), strict=True):
            barcode_text, group_text = _as_text(barcode), _as_text(group)
            if barcode_text is not None and group_text is not None:
                mapping[barcode_text] = group_text
        return mapping

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "cell_score_id": self.cell_score_id,
            "cell_meta_column": self.cell_meta_column,
            "group_meta_column": self.group_meta_column,
            "meta_resource_id": self.meta_resource_id,
            "filter": [
                {"column": f.column, "value": f.value, "label": f.label}
                for f in self.filters
            ],
        }


@dataclass(frozen=True)
class FragmentBinningJob(BinningJob):
    """A fragment job: its tracks, and how each fragment reaches one.

    ``resource_ids`` are the resources read together, in resource-id
    order -- every match of a pooled entry, or one resource of an
    unpooled one.  A fragment contributes its ``score_id`` score's value
    when the entry aggregates a score, else the constant ``value``.  With
    a ``grouping`` a fragment reaches the track of its barcode's group,
    or none; without one, the job's one track.
    """

    resource_ids: tuple[str, ...] = ()
    score_id: str | None = None
    value: float = 1
    grouping: CellGrouping | None = None


@dataclass(frozen=True)
class _Aggregate:
    """What a fragment contributes, and how a bin reduces the contributions.

    Exactly one of a score or a constant ``value``; an entry that names
    neither counts fragments, ``{value: 1, aggregator: sum}``.  An
    omitted aggregator is ``sum``.
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
        if config is None:
            return cls(score_id=None, value=1, aggregator="sum")
        check_keys(label, config, AGGREGATE_KEYS)
        if "score" in config and "value" in config:
            raise RunDefinitionError(
                f"{label}: give one of score or value, not both")
        aggregator = config.get("aggregator", "sum")
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
        score_id = config["score"]
        for resource in matches:
            value_type = _score_type(label, resource, score_id)
            if value_type not in NUMERIC_VALUE_TYPES:
                raise RunDefinitionError(
                    f"{label}: resource {resource.resource_id!r} score "
                    f"{score_id!r} is of type {value_type!r}; only a "
                    f"numeric score (int or float) can be aggregated")
        return cls(score_id=score_id, value=1, aggregator=aggregator)


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


def _parse_group(
    label: str, config: Any,
) -> tuple[str | None, dict[str, str] | None]:
    """The constant group name, or the cell grouping's three keys."""
    if config is None:
        return DEFAULT_GROUP, None
    check_keys(label, config, GROUP_KEYS)
    cell_keys = CELL_GROUP_KEYS & config.keys()
    if "group" in config:
        if cell_keys:
            raise RunDefinitionError(
                f"{label}: give either group, or cell_score_id, "
                f"cell_meta_column and group_meta_column, not both forms")
        return _require_text(label, "group", config["group"]), None
    missing = sorted(CELL_GROUP_KEYS - cell_keys)
    if missing:
        raise RunDefinitionError(
            f"{label}: grouping by metadata needs cell_score_id, "
            f"cell_meta_column and group_meta_column; missing "
            f"{', '.join(missing)}")
    return None, {
        key: _require_text(label, key, config[key])
        for key in sorted(CELL_GROUP_KEYS)}


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


def _parse_grouping(
    label: str, cell_keys: dict[str, str], meta: Any,
    matches: list[GenomicResource], grr: GenomicResourceRepo,
) -> tuple[CellGrouping, pd.DataFrame]:
    """Resolve a metadata grouping against its table and the matches."""
    if meta is None:
        raise RunDefinitionError(
            f"{label}: grouping by metadata needs a meta block naming the "
            f"data_frame resource that maps cells to groups")
    meta_label = f"{label}.meta"
    check_keys(meta_label, meta, META_KEYS)
    grouping = CellGrouping(
        meta_resource_id=_require_text(
            meta_label, "resource_id", meta.get("resource_id")),
        filters=_parse_filters(f"{meta_label}.filter", meta.get("filter")),
        **cell_keys)
    try:
        table = grouping.load_table(grr)
    except (ValueError, FileNotFoundError) as err:
        raise RunDefinitionError(
            f"{meta_label}: resource {grouping.meta_resource_id!r} cannot "
            f"be read as a data_frame: {err}") from err
    columns = [
        grouping.cell_meta_column, grouping.group_meta_column,
        *(conjunct.column for conjunct in grouping.filters)]
    for column in columns:
        if column not in table.columns:
            raise RunDefinitionError(
                f"{meta_label}: resource {grouping.meta_resource_id!r} has "
                f"no column {column!r}; its columns are "
                f"{[str(c) for c in table.columns]}")
    for resource in matches:
        _score_type(label, resource, grouping.cell_score_id)
        for conjunct in grouping.filters:
            if conjunct.label is not None and \
                    conjunct.operand(resource) is None:
                raise RunDefinitionError(
                    f"{meta_label}.filter: resource "
                    f"{resource.resource_id!r} has no label "
                    f"{conjunct.label!r} to filter {conjunct.column!r} by")
    return grouping, table


class FragmentScoreBinding:
    """A fragment job's resources, open for as long as the ``with`` lasts.

    Each resource is its own :class:`FragmentScore`, so the
    one-live-read-per-score limit holds however many are pooled.  Each
    resource's barcode-to-track map is built once, when bound.
    """

    def __init__(
        self, job: FragmentBinningJob, scores: list[FragmentScore],
        track_maps: list[dict[str, int]] | None,
    ) -> None:
        self.job = job
        self.scores = scores
        self.track_maps = track_maps

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
        so no read outlives the call.
        """
        reads = [
            self._records(index, score, region)
            for index, score in enumerate(self.scores)
            if score.has_chromosome(region.chrom)
        ]
        try:
            return fold_into_bins(
                heapq.merge(*reads, key=operator.itemgetter(0)),
                start=region.start, end=region.stop, bin_size=bin_size,
                aggregators=[track.aggregator for track in self.job.tracks])
        finally:
            for read in reads:
                read.close()

    def _records(
        self, index: int, score: FragmentScore, region: BedRegion,
    ) -> Generator[tuple[int, int, ScoreValue], None, None]:
        """``(start, track, value)`` per fragment of one resource.

        A fragment whose barcode maps to no track is dropped.
        """
        job = self.job
        scores = [] if job.score_id is None else [job.score_id]
        if job.grouping is not None:
            scores.append(job.grouping.cell_score_id)
        rows = score.get_fragment_scores_starting_in_region(
            region.chrom, region.start, region.stop, scores=scores)
        track_of = None if self.track_maps is None \
            else self.track_maps[index]
        try:
            for begin, _, values in rows:
                track = 0 if track_of is None \
                    else track_of.get(_as_text(values[-1]) or "")
                if track is None:
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
    ) -> list[BinningJob]:
        """Resolve one entry into a pooled job, or one job per resource.

        A job's tracks are its groups: the constant one, or every
        distinct non-empty group of its resources' filtered rows, in
        sorted order.
        """
        check_keys(label, config, ENTRY_KEYS)
        matches = match_resources(label, config, grr, "fragment_score")
        pool = config.get("pool", True)
        if not isinstance(pool, bool):
            raise RunDefinitionError(
                f"{label}: pool must be true or false, not {pool!r}")
        aggregate = _Aggregate.parse(
            f"{label}.aggregate", config.get("aggregate"), matches)
        constant, cell_keys = _parse_group(
            f"{label}.group", config.get("group"))
        grouping = None
        if cell_keys is None:
            assert constant is not None
            if "meta" in config:
                raise RunDefinitionError(
                    f"{label}: meta maps cells to groups, and is given only "
                    f"with a group of cell_score_id, cell_meta_column and "
                    f"group_meta_column")
            groups_of = {
                resource.resource_id: {constant} for resource in matches}
        else:
            grouping, table = _parse_grouping(
                label, cell_keys, config.get("meta"), matches, grr)
            groups_of = {
                resource.resource_id:
                    set(grouping.groups_of(table, resource).values())
                for resource in matches
            }
        jobs: list[BinningJob] = []
        for resources in [matches] if pool else [[r] for r in matches]:
            resource_ids = tuple(r.resource_id for r in resources)
            groups = sorted(set[str]().union(
                *(groups_of[resource_id] for resource_id in resource_ids)))
            if not groups:
                raise RunDefinitionError(
                    f"{label}: the meta rows selected for "
                    f"{', '.join(resource_ids)} name no group")
            jobs.append(cls._job_of(
                resource_ids, aggregate, grouping, groups,
                base=config["resource_query"] if pool else resource_ids[0]))
        return jobs

    @classmethod
    def _job_of(
        cls, resource_ids: tuple[str, ...], aggregate: _Aggregate,
        grouping: CellGrouping | None, groups: list[str], *, base: str,
    ) -> FragmentBinningJob:
        parameters: dict[str, Any] = {}
        if aggregate.score_id is None:
            parameters["value"] = aggregate.value
        if grouping is not None:
            parameters.update(grouping.parameters)
        tracks = tuple(
            Track(
                name=f"{base}:{group}",
                resource_ids=resource_ids,
                group=group,
                score_id=aggregate.score_id or "",
                aggregator=aggregate.aggregator,
                none_value_replacement=None,
                binner=cls.kind,
                parameters=json.dumps(parameters, sort_keys=True)
                if parameters else "",
            )
            for group in groups
        )
        return FragmentBinningJob(
            binner=cls.kind, tracks=tracks, resource_ids=resource_ids,
            score_id=aggregate.score_id, value=aggregate.value,
            grouping=grouping)

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
            table = job.grouping.load_table(grr)
            index_of = {
                track.group: index for index, track in enumerate(job.tracks)}
            track_maps = [
                {
                    barcode: index_of[group]
                    for barcode, group in job.grouping.groups_of(
                        table, resource).items()
                    if group in index_of
                }
                for resource in resources
            ]
        return FragmentScoreBinding(
            job, [FragmentScore(resource) for resource in resources],
            track_maps)
