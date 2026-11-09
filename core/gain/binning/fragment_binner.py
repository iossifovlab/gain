"""``fragment_score_binner``: fragments per bin, by group, pooled or not.

An entry names a query over ``fragment_score`` resources and produces,
per grid bin, an aggregate over the fragments that START in that bin --
the partition :meth:`FragmentScore.get_fragment_scores_starting_in_region`
reads, so adjacent regions see each fragment exactly once -- or, in
:data:`FRAGMENT_LENGTH` mode, over the fragments that OVERLAP it, each
weighted by its overlap in base pairs.  That mode reads
:meth:`FragmentScore.get_fragment_scores_overlapping_region` and clips
each fragment to the region, so a base pair belongs to one region and no
fragment's base pair is counted twice.  In :data:`COVERAGE_PROFILE`
mode the clipped fragments of each track make its coverage profile --
per base, the sum of the values of the fragments over it -- and each bin
reduces the profile, weighted by base pairs; a base the profile leaves
at 0 is uncovered.

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
  each its own, since one barcode recurs in every sample; unpooled, the
  bare value.
  The forms do not mix, and ``meta`` does not go with the last.
  Omitted, the resource's labels decide: a resource carrying
  :data:`CELL_META_RESOURCE_ID_LABEL` is grouped as with ``group: {}``,
  any other is the one group ``all``.
- ``value``: what one fragment adds, ``{score_id: S}``, the value of the
  numeric score ``S``, or ``{value: V}``, a constant; omitted, ``{value:
  1}``.
- ``aggregate``: how a bin reduces the values, ``{mode, aggregator,
  uncovered_value}``; ``mode`` is :data:`FRAGMENT_START` (the default),
  :data:`FRAGMENT_LENGTH` or :data:`COVERAGE_PROFILE`, and
  ``aggregator`` omitted means ``sum``.  The last two take only
  :data:`LENGTH_AGGREGATORS`, and an ``uncovered_value``: a number each
  uncovered base of the region adds, with a weight of 1 bp, or ``null``,
  nothing -- omitted, ``null`` in :data:`FRAGMENT_LENGTH` mode and ``0``
  in :data:`COVERAGE_PROFILE` mode; :data:`FRAGMENT_START` refuses it.  The keys
  ``score`` and ``value`` are refused here, naming the ``value`` key.
  Without ``value`` and ``aggregate`` an entry counts the fragments that
  start in each bin, whatever its resources' scores.
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
is omitted), and on the one metadata table their ``meta`` names.

A fragment whose barcode is not in the filtered rows, or whose row's
group is empty, or whose ``group_score_id`` value is missing or is one
its resource's histogram does not list, reaches no track; it is counted
as dropped, per resource and region (in :data:`FRAGMENT_LENGTH` and
:data:`COVERAGE_PROFILE` modes, in every region it reaches).  An empty
bin holds the fold's empty value -- 0 for ``count`` and ``sum``, NaN for
the others -- and so does every bin of a contig a resource lacks; with
an ``uncovered_value`` no bin is empty, and a contig a resource lacks is
uncovered throughout.
"""
from __future__ import annotations

import heapq
import json
import operator
from collections.abc import Callable, Generator
from dataclasses import dataclass
from functools import partial
from types import TracebackType
from typing import Any, ClassVar, TypeVar

import numpy as np
import numpy.typing as npt

from gain import logging
from gain.binning.binners import (
    BinningJob,
    RunDefinitionError,
    Track,
    binner_type_of,
    check_keys,
    entry_name,
    match_resources,
    named_base,
)
from gain.binning.fragment_array_folds import (
    CoverageRead,
    InexactLevelsError,
    LengthRead,
    StartRead,
    fold_coverage_profiles,
    fold_fragment_lengths,
    fold_fragment_starts,
)
from gain.binning.fragment_def import FragmentScoreDef
from gain.binning.fragment_folds import (
    BinFragmentAggregator,
    BinFragmentCoverageAggregator,
    BinFragmentLengthAggregator,
    BinFragmentStartAggregator,
    BinValue,
    FragmentValue,
)
from gain.binning.fragment_grouping import (
    AGGREGATE_KEYS,
    BARCODE_COLUMN,
    CELL_GROUP_DEFAULTS,
    CELL_GROUP_KEYS,
    CELL_META_RESOURCE_ID_LABEL,
    CELL_SCORE,
    CLASS_COLUMN,
    CONVENTION_META,
    COUNT_SCORE,
    COVERAGE_PROFILE,
    DEFAULT_GROUP,
    ENTRY_KEYS,
    FILE_FORMATS,
    FILTER_KEYS,
    FRAGMENT_LENGTH,
    FRAGMENT_START,
    GROUP_KEYS,
    META_FILE_KEYS,
    META_KEYS,
    META_SOURCE_KEYS,
    MODES,
    MOVED_TO_VALUE,
    SAMPLE_ID_COLUMN,
    SAMPLE_ID_LABEL,
    VALUE_GROUP_KEY,
    VALUE_KEYS,
    CellGrouping,
    DuplicateBarcodeError,
    MetaFilter,
    MetaTable,
    ValueGrouping,
    _Aggregate,
    _as_text,
    _FragmentValue,
    _Group,
    _is_labelled,
    _is_single_cell,
    _MetaSpec,
    _partition,
    _quoted,
    _resolve_grouping,
    _resolve_value_grouping,
    _Tables,
)
from gain.genomic_resources.genomic_scores import FragmentScore
from gain.genomic_resources.genomic_scores.records import RecordArrays
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.utils.regions import BedRegion, calc_bin_index

logger = logging.getLogger(__name__)

_Read = TypeVar("_Read", bound=StartRead)

__all__ = [
    "AGGREGATE_KEYS",
    "BARCODE_COLUMN",
    "CELL_GROUP_DEFAULTS",
    "CELL_GROUP_KEYS",
    "CELL_META_RESOURCE_ID_LABEL",
    "CELL_SCORE",
    "CLASS_COLUMN",
    "CONVENTION_META",
    "COUNT_SCORE",
    "COVERAGE_PROFILE",
    "DEFAULT_GROUP",
    "ENTRY_KEYS",
    "FILE_FORMATS",
    "FILTER_KEYS",
    "FRAGMENT_LENGTH",
    "FRAGMENT_START",
    "GROUP_KEYS",
    "META_FILE_KEYS",
    "META_KEYS",
    "META_SOURCE_KEYS",
    "MODES",
    "MOVED_TO_VALUE",
    "SAMPLE_ID_COLUMN",
    "SAMPLE_ID_LABEL",
    "VALUE_GROUP_KEY",
    "VALUE_KEYS",
    "CellGrouping",
    "DuplicateBarcodeError",
    "FragmentBinningJob",
    "FragmentScoreBinding",
    "FragmentScoreBinner",
    "MetaFilter",
    "MetaTable",
    "ValueGrouping",
]


def _fragment_def(track: Track) -> FragmentScoreDef:
    definition = track.definition
    assert isinstance(definition, FragmentScoreDef)
    return definition


@dataclass(frozen=True)
class FragmentBinningJob(BinningJob):
    """A fragment job: its tracks, and how each fragment reaches one.

    ``resource_ids`` are the resources read together, in resource-id
    order -- every match of a pooled entry, or one resource of an
    unpooled one.  A fragment contributes its ``score_id`` score's value
    when the entry's value is a score, else the constant ``value``.  With
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

    @property
    def read_resource_ids(self) -> tuple[str, ...]:
        """Every resource the job reads: the fragments, then the metadata.

        A cell grouping whose table is a ``data_frame`` resource adds the
        id of that resource to ``resource_ids``.  A value grouping and a
        table from a local file add nothing.
        """
        if isinstance(self.grouping, CellGrouping) \
                and self.grouping.table.resource_id is not None:
            return (*self.resource_ids, self.grouping.table.resource_id)
        return self.resource_ids


@dataclass(frozen=True)
class _Entry:
    """An entry's parsed keys, before the defaults its resources decide.

    ``meta`` ``None`` means none was given.
    """

    label: str
    pool: bool
    resource_query: str
    name: str | None
    value: _FragmentValue
    aggregate: _Aggregate
    group: _Group
    meta: _MetaSpec | None
    tables: _Tables


class FragmentScoreBinding:
    """A fragment job's resources, open for as long as the ``with`` lasts.

    Each resource is its own :class:`FragmentScore`, so the
    one-live-read-per-score limit holds however many are pooled.  Each
    resource's barcode-to-track map is built once, when bound.

    With a grouping, ``dropped`` maps each (resource id, region) binned
    so far -- the region as ``chrom:start-stop`` -- to the number of its
    fragments that reached no track: a barcode absent from the
    resource's filtered rows, a row of no group, or a grouping-score
    value that is missing or that the score's histogram does not list.
    A ``quiet`` binding keeps ``dropped`` empty and logs no line.
    """

    def __init__(
        self, job: FragmentBinningJob, scores: list[FragmentScore],
        track_maps: list[dict[str, int]] | None, *, quiet: bool = False,
    ) -> None:
        self.job = job
        self.scores = scores
        self.track_maps = track_maps
        self.quiet = quiet
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
        """Fold the fragments of ``region`` into the job's tracks.

        A :data:`FRAGMENT_START` job on scores with column-array reads
        folds the array batches of each resource with
        :func:`~.fragment_array_folds.fold_fragment_starts`, a
        :data:`FRAGMENT_LENGTH` job with
        :func:`~.fragment_array_folds.fold_fragment_lengths`, and a
        :data:`COVERAGE_PROFILE` job of ``int`` values with
        :func:`~.fragment_array_folds.fold_coverage_profiles`.  The
        declared type of the score, or the type of the constant value,
        decides what is ``int``.  A region whose levels can be inexact in
        ``int64`` takes the streaming fold.  Otherwise
        one streaming fold per track, of the track's mode, is fed the
        merge by start of one read per resource -- starting-in, or
        overlapping with each fragment clipped to the region -- and each
        bin a fold answers goes to its row of the block.  A resource
        without the region's contig contributes nothing.  Every read is
        drained, or closed on failure, before this returns.  With a
        grouping, each resource's dropped fragments are counted into
        :attr:`dropped` and logged, unless the binding is quiet.
        """
        dropped = [0] * len(self.scores)
        mode = _fragment_def(self.job.tracks[0]).mode
        if not self._reads_arrays():
            block = self._bin_records(region, bin_size, dropped)
        elif mode == FRAGMENT_START:
            block = self._bin_starts(region, bin_size, dropped)
        elif mode == FRAGMENT_LENGTH:
            block = self._bin_lengths(region, bin_size, dropped)
        else:
            try:
                block = self._bin_coverage(region, bin_size, dropped)
            except InexactLevelsError:
                block = self._bin_records(region, bin_size, dropped)
        if self.job.grouping is not None and not self.quiet:
            where = f"{region.chrom}:{region.start}-{region.stop}"
            for resource_id, count in zip(
                    self.job.resource_ids, dropped, strict=True):
                self.dropped[resource_id, where] = count
                logger.info(
                    "%s %s: %d fragments dropped, %s",
                    resource_id, where, count,
                    self.job.grouping.dropped_reason)
        return block

    @property
    def _read_scores(self) -> list[str]:
        """The value score, if any, then the grouping score, if any."""
        job = self.job
        scores = [] if job.score_id is None else [job.score_id]
        return scores if job.grouping is None \
            else [*scores, job.grouping.score_id]

    def _reads_arrays(self) -> bool:
        mode = _fragment_def(self.job.tracks[0]).mode
        return (mode != COVERAGE_PROFILE or self._int_values()) \
            and all(score.supports_region_value_arrays(self._read_scores)
                    for score in self.scores)

    def _int_values(self) -> bool:
        """Whether the value of the job is an ``int`` in each resource.

        The declared type of the score decides, because the array read
        gives an ``int`` score as ``float64``.
        """
        job = self.job
        if job.score_id is None:
            return isinstance(job.value, int) \
                and not isinstance(job.value, bool)
        return all(
            score.score_definitions[job.score_id].value_type == "int"
            for score in self.scores)

    def _bin_starts(
        self, region: BedRegion, bin_size: int, dropped: list[int],
    ) -> npt.NDArray[np.float64]:
        """The array fold of a :data:`FRAGMENT_START` job."""
        return self._fold_arrays(
            region, dropped, StartRead,
            FragmentScore.get_fragment_arrays_starting_in_region,
            lambda reads: fold_fragment_starts(
                reads, start=region.start, end=region.stop,
                bin_size=bin_size, tracks=len(self.job.tracks),
                aggregator=_fragment_def(self.job.tracks[0]).aggregator))

    def _bin_lengths(
        self, region: BedRegion, bin_size: int, dropped: list[int],
    ) -> npt.NDArray[np.float64]:
        """The array fold of a :data:`FRAGMENT_LENGTH` job."""
        return self._bin_overlapping(
            region, bin_size, dropped, LengthRead, fold_fragment_lengths)

    def _bin_coverage(
        self, region: BedRegion, bin_size: int, dropped: list[int],
    ) -> npt.NDArray[np.float64]:
        """The array fold of a :data:`COVERAGE_PROFILE` job of ints."""
        return self._bin_overlapping(
            region, bin_size, dropped, CoverageRead, fold_coverage_profiles)

    def _bin_overlapping(
        self, region: BedRegion, bin_size: int, dropped: list[int],
        read_type: type[LengthRead],
        fold: Callable[..., npt.NDArray[np.float64]],
    ) -> npt.NDArray[np.float64]:
        """Fold the fragments that overlap ``region`` with ``fold``."""
        definition = _fragment_def(self.job.tracks[0])
        return self._fold_arrays(
            region, dropped,
            partial(read_type, start=region.start, end=region.stop),
            FragmentScore.get_fragment_arrays_overlapping_region,
            lambda reads: fold(
                reads, start=region.start, end=region.stop,
                bin_size=bin_size, tracks=len(self.job.tracks),
                aggregator=definition.aggregator,
                uncovered_value=definition.uncovered_value))

    def _fold_arrays(
        self, region: BedRegion, dropped: list[int],
        make_read: Callable[..., _Read],
        batches: Callable[..., Generator[RecordArrays, None, None]],
        fold: Callable[[list[_Read]], npt.NDArray[np.float64]],
    ) -> npt.NDArray[np.float64]:
        """Fold one ``make_read`` of the ``batches`` of each resource.

        The reads are closed after the fold, and the fragments that each
        read dropped go to ``dropped``.
        """
        job, track_maps = self.job, self.track_maps
        reads = {
            index: make_read(
                batches(
                    score, region.chrom, region.start, region.stop,
                    scores=self._read_scores),
                value_score=job.score_id, constant=job.value,
                group_score=None if job.grouping is None
                else job.grouping.score_id,
                track_of=None if track_maps is None else partial(
                    _track_of, track_maps[index]))
            for index, score in enumerate(self.scores)
            if score.has_chromosome(region.chrom)
        }
        try:
            block = fold(list(reads.values()))
        finally:
            for read in reads.values():
                read.batches.close()
        for index, read in reads.items():
            dropped[index] = read.dropped
        return block

    def _bin_records(
        self, region: BedRegion, bin_size: int, dropped: list[int],
    ) -> npt.NDArray[np.float64]:
        """The streaming folds, one per track."""
        first_bin = calc_bin_index(bin_size, region.start)
        block = np.empty(
            (calc_bin_index(bin_size, region.stop) - first_bin + 1,
             len(self.job.tracks)),
            dtype=np.float64)
        folds = [
            _fold_of(track, region, bin_size) for track in self.job.tracks]

        def place(column: int, bins: list[BinValue]) -> None:
            for bin_value in bins:
                block[
                    calc_bin_index(bin_size, bin_value.start) - first_bin,
                    column] = bin_value.value

        reads = [
            self._records(index, score, region, dropped)
            for index, score in enumerate(self.scores)
            if score.has_chromosome(region.chrom)
        ]
        try:
            for begin, end, track, value in heapq.merge(
                    *reads, key=operator.itemgetter(0)):
                place(track, folds[track].feed(
                    FragmentValue(begin, end, value)))
        finally:
            for read in reads:
                read.close()
        for column, fold in enumerate(folds):
            place(column, fold.flush())
        return block

    def _records(
        self, index: int, score: FragmentScore, region: BedRegion,
        dropped: list[int],
    ) -> Generator[tuple[int, int, int, ScoreValue], None, None]:
        """``(start, end, track, value)`` per fragment of one resource.

        The fragments starting in ``region``, or in a length mode those
        overlapping it, clipped to it.  A fragment whose barcode maps to
        no track is dropped, and counted in ``dropped[index]``.
        """
        job = self.job
        starting = _fragment_def(job.tracks[0]).mode == FRAGMENT_START
        read = score.get_fragment_scores_starting_in_region if starting \
            else score.get_fragment_scores_overlapping_region
        rows = read(
            region.chrom, region.start, region.stop,
            scores=self._read_scores)
        track_of = None if self.track_maps is None \
            else self.track_maps[index]
        try:
            for begin, end, values in rows:
                track = 0 if track_of is None \
                    else _track_of(track_of, values[-1])
                if track is None:
                    dropped[index] += 1
                    continue
                value = job.value if job.score_id is None else values[0]
                if starting:
                    yield begin, end, track, value
                else:
                    # A base pair belongs to one region: each adds only
                    # the part of a fragment inside it.
                    yield max(begin, region.start), \
                        min(end, region.stop), track, value
        finally:
            rows.close()


def _track_of(track_map: dict[str, int], value: Any) -> int | None:
    """The track of a grouping-score value, ``None`` for none."""
    return track_map.get(_as_text(value) or "")


def _fold_of(
    track: Track, region: BedRegion, bin_size: int,
) -> BinFragmentAggregator:
    """The streaming fold of ``track``'s mode over ``region``."""
    definition = _fragment_def(track)
    if definition.mode == FRAGMENT_START:
        return BinFragmentStartAggregator(
            start=region.start, end=region.stop, bin_size=bin_size,
            aggregator=definition.aggregator)
    fold = (
        BinFragmentCoverageAggregator if definition.mode == COVERAGE_PROFILE
        else BinFragmentLengthAggregator)
    return fold(
        start=region.start, end=region.stop, bin_size=bin_size,
        aggregator=definition.aggregator,
        uncovered_value=definition.uncovered_value)


class FragmentScoreBinner:
    """Bins ``fragment_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "fragment_score_binner"

    @staticmethod
    def load_def(text: str) -> FragmentScoreDef:
        """Read a ``binner_def`` of this kind."""
        return FragmentScoreDef.from_json(text)

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
        name = entry_name(label, config)
        matches = match_resources(label, config, grr, "fragment_score")
        pool = config.get("pool", True)
        if not isinstance(pool, bool):
            raise RunDefinitionError(
                f"{label}: pool must be true or false, not {pool!r}")
        entry = _Entry(
            label=label,
            pool=pool,
            resource_query=config["resource_query"],
            name=name,
            value=_FragmentValue.default() if config.get("value") is None
            else _FragmentValue.parse(
                f"{label}.value", config["value"], matches),
            aggregate=_Aggregate.default()
            if config.get("aggregate") is None
            else _Aggregate.parse(f"{label}.aggregate", config["aggregate"]),
            group=_Group.parse(f"{label}.group", config.get("group")),
            meta=None if config.get("meta") is None
            else _MetaSpec.parse(f"{label}.meta", config["meta"], base_dir),
            tables=_Tables(f"{label}.meta", grr))
        pooled_base = config["resource_query"] if name is None else name
        return [
            cls._resolve_job(
                entry, resources,
                base=pooled_base if pool
                else named_base(name, resources[0].resource_id))
            for resources in ([matches] if pool else [[r] for r in matches])
        ]

    @classmethod
    def _resolve_job(
        cls, entry: _Entry, resources: list[GenomicResource], *, base: str,
    ) -> FragmentBinningJob:
        """Resolve the defaults and the grouping of one job's resources."""
        resource_ids = tuple(r.resource_id for r in resources)
        if entry.group.value_score is not None:
            value_grouping, value_groups = _value_score_grouping(
                entry, resources)
            return cls._job_of(
                resource_ids, entry, value_grouping, value_groups,
                base=base, warnings=())
        constant, cell_keys = entry.group.constant, entry.group.cell_keys
        warnings: list[str] = []
        if constant is None and cell_keys is None:
            constant, cell_keys = _label_tier(
                entry.label, resources, warnings)
        grouping: CellGrouping | None = None
        if cell_keys is None:
            assert constant is not None
            groups = _constant_groups(entry, constant)
        else:
            grouping, groups = _cell_grouping(
                entry, cell_keys, resources, warnings)
        return cls._job_of(
            resource_ids, entry, grouping, groups, base=base,
            warnings=tuple(warnings))

    @classmethod
    def _job_of(
        cls, resource_ids: tuple[str, ...], entry: _Entry,
        grouping: CellGrouping | ValueGrouping | None, groups: list[str],
        *, base: str,
        warnings: tuple[str, ...],
    ) -> FragmentBinningJob:
        value, aggregate = entry.value, entry.aggregate
        parameters: dict[str, Any] = {
            "mode": aggregate.mode,
            "uncovered_value": aggregate.uncovered_value,
        }
        if value.score_id is None:
            parameters["value"] = value.value
        if grouping is not None:
            parameters.update(grouping.parameters)
        parameters_text = json.dumps(parameters, sort_keys=True)
        tracks = tuple(
            Track(
                name=f"{base}:{group}",
                resource_ids=resource_ids,
                group=group,
                selector=group,
                binner_type=binner_type_of(cls.kind),
                definition=FragmentScoreDef(
                    aggregator=aggregate.aggregator,
                    mode=aggregate.mode,
                    uncovered_value=aggregate.uncovered_value,
                    score_id=value.score_id,
                    resource_query=entry.resource_query,
                    pool=entry.pool,
                    name=entry.name),
                parameters=parameters_text,
            )
            for group in groups
        )
        return FragmentBinningJob(
            binner=cls.kind, tracks=tracks, resource_ids=resource_ids,
            score_id=value.score_id, value=value.value,
            grouping=grouping, warnings=warnings)

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo, *, quiet: bool = False,
    ) -> FragmentScoreBinding:
        """Bind the job to one :class:`FragmentScore` per resource.

        With a cell grouping, the metadata table is read once and each
        resource's barcodes are mapped to the job's track indexes.  A
        ``quiet`` binding keeps no record of dropped fragments and logs
        no line for each region.
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
            track_maps, quiet=quiet)


def _value_score_grouping(
    entry: _Entry, resources: list[GenomicResource],
) -> tuple[ValueGrouping, list[str]]:
    """Resolve a job grouped by the values of a score."""
    assert entry.group.value_score is not None
    if entry.meta is not None:
        raise RunDefinitionError(
            f"{entry.label}: meta maps cells to groups, and is not given "
            f"with a group of {VALUE_GROUP_KEY}, whose values are "
            f"the groups, for "
            f"{_quoted(r.resource_id for r in resources)}")
    return _resolve_value_grouping(
        entry.label, entry.group.value_score, entry.value, resources,
        pool=entry.pool)


def _label_tier(
    label: str, resources: list[GenomicResource], warnings: list[str],
) -> tuple[str | None, dict[str, str] | None]:
    """Decide a job's grouping by its resources' label tier (F8).

    Returns the constant group or the cell keys, and adds a warning for
    each single-cell resource without the label.
    """
    labelled, unlabelled = _partition(resources, _is_labelled)
    if labelled and unlabelled:
        raise RunDefinitionError(
            f"{label}: the pooled resources disagree on the label "
            f"{CELL_META_RESOURCE_ID_LABEL!r} that decides their "
            f"grouping: {_quoted(labelled)} carry it and "
            f"{_quoted(unlabelled)} do not; give group, or "
            f"pool: false")
    if labelled:
        return None, dict(CELL_GROUP_DEFAULTS)
    warnings.extend(
        f"resource {r.resource_id!r} has {CELL_SCORE!r} and "
        f"{COUNT_SCORE!r} scores but no "
        f"{CELL_META_RESOURCE_ID_LABEL!r} label; it is binned "
        f"as the single {DEFAULT_GROUP!r} track"
        for r in resources if _is_single_cell(r))
    return DEFAULT_GROUP, None


def _constant_groups(entry: _Entry, constant: str) -> list[str]:
    """Return the one group of a constant grouping."""
    if entry.meta is not None:
        raise RunDefinitionError(
            f"{entry.label}: meta maps cells to groups, and is given only "
            f"with a group of cell_score_id, cell_meta_column and "
            f"group_meta_column, or with no group on a resource "
            f"labelled {CELL_META_RESOURCE_ID_LABEL!r}")
    return [constant]


def _cell_grouping(
    entry: _Entry, cell_keys: dict[str, str],
    resources: list[GenomicResource], warnings: list[str],
) -> tuple[CellGrouping, list[str]]:
    """Resolve a cell grouping and its sorted groups.

    Adds a warning when the cell metadata table is a local file.
    """
    grouping, groups_of = _resolve_grouping(
        entry.label, cell_keys, entry.meta or CONVENTION_META, resources,
        entry.tables)
    groups = sorted(set[str]().union(*groups_of.values()))
    if not groups:
        raise RunDefinitionError(
            f"{entry.label}: the meta rows selected for "
            f"{', '.join(groups_of)} name no group")
    if grouping.table.is_local:
        warnings.append(
            f"the cell metadata table {grouping.table.name!r} is a "
            f"local file; the run is not reproducible elsewhere")
    return grouping, groups
