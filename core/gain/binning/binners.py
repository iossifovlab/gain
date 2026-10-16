"""Binner kinds: how a run-definition entry becomes tracks and values.

Kinds are discovered through the ``gain.binning.binners`` entry-point
group, so a kind -- the position-score binner here, the fragment-score
binner in :mod:`gain.binning.fragment_binner`, an external plugin --
registers the way every other gain plugin does, without editing the tool.
"""
from __future__ import annotations

import json
import math
from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib.metadata import entry_points
from types import TracebackType
from typing import Any, ClassVar, Protocol, Self

import numpy as np
import numpy.typing as npt

from gain.binning.position_fold import (
    ARRAY_FOLD_AGGREGATORS,
    ArrayBinFold,
    replacement_is_exact,
)
from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    NUMERIC_ONLY_AGGREGATORS,
    AggregatorDefinition,
    PositionScoreAggregationQuery,
    get_aggregator_class,
    to_float64,
    validate_aggregator,
)
from gain.genomic_resources.genomic_scores import (
    DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
)
from gain.genomic_resources.genomic_scores.position import PositionScore
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    SearchIndexUnavailableError,
    SearchTermError,
)
from gain.genomic_resources.resource_query import ResourceQueryParseError
from gain.genomic_resources.score_def import GenomicScoreDef
from gain.utils.regions import BedRegion

BINNERS_ENTRY_POINT_GROUP = "gain.binning.binners"

NUMERIC_VALUE_TYPES = {"int", "float"}


def _output_type(name: str, value_type: str) -> str | None:
    """The type the registered aggregator ``name`` answers for a score.

    An aggregator's declared output type, or -- for one that declares
    none and answers in its input's type -- the score's own
    ``value_type``, but only when the aggregator is numeric-only, so that
    the input is known to be a number.  ``mode`` also answers in its
    input's type yet accepts any type, so it answers ``None`` here.
    """
    declared = get_aggregator_class(name).output_value_type
    if declared is None and name in NUMERIC_ONLY_AGGREGATORS:
        return value_type
    return declared


def numeric_aggregators() -> list[str]:
    """The registered aggregators whose result is a number (D11).

    Those that declare a numeric output type, and the numeric-only ones
    that answer in their input's type (``sum``, ``product``) -- a binned
    score is numeric, so they answer a number too.  Read off the
    registry, so a numeric aggregator added to it is accepted here
    without a list to keep in step.
    """
    return sorted(
        name for name in AGGREGATOR_CLASS_DICT
        if _output_type(name, "float") in NUMERIC_VALUE_TYPES)


class RunDefinitionError(ValueError):
    """A run definition that cannot be resolved into a run."""


BINNER_SUFFIX = "_binner"


def binner_type_of(kind: str) -> str:
    """The ``binner_type`` the file records for a registered ``kind``.

    The kind without its ``_binner`` suffix: ``position_score_binner``
    is ``position_score``.  The one place the two names are mapped.
    """
    return kind.removesuffix(BINNER_SUFFIX)


def canonical_json(fields: dict[str, Any]) -> str:
    """Sorted keys, no extra whitespace; NaN and infinities are refused.

    Raises :class:`ValueError` for a NaN or an infinite number, which
    JSON cannot carry.
    """
    return json.dumps(
        fields, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False)


class BinnerDef(Protocol):
    """The typed definition of a track: what the other columns do not say."""

    def to_json(self) -> str:
        """The definition as canonical JSON."""


@dataclass(frozen=True)
class PositionScoreDef:
    """A position-score track's definition.

    ``aggregator`` and ``none_value_replacement`` are the resolved ones
    (``None`` for no replacement); ``name`` is the entry's optional name.
    The score id is the track's selector, not part of the definition.
    """

    aggregator: str
    none_value_replacement: float | None = None
    name: str | None = None

    def to_json(self) -> str:
        """The definition as canonical JSON."""
        return canonical_json({
            "aggregator": self.aggregator,
            "none_value_replacement": self.none_value_replacement,
            "name": self.name,
        })

    @classmethod
    def from_json(cls, text: str) -> Self:
        """The definition ``to_json`` wrote."""
        fields = json.loads(text)
        return cls(
            aggregator=fields["aggregator"],
            none_value_replacement=fields["none_value_replacement"],
            name=fields["name"])


@dataclass(frozen=True)
class Track:
    """One column of the output: a score of resources, reduced one way.

    ``resource_ids`` are the resources the column is computed from, in
    resource-id order: one for a position-score track or an unpooled
    fragment track, every matched resource for a pooled one.  ``group``
    is the column's group of a grouped kind -- a fragment track's cell
    class, or ``all`` -- and empty for a position-score track.
    ``selector`` is what picks the track within its resources: the score
    id of a position-score track, the group of a fragment track.

    ``binner_type`` names the kind that produces the column
    (:func:`binner_type_of`).  ``binner_index`` is the entry's position
    in the run definition's ``binners`` list and ``track_index`` the
    track's position among all tracks of that entry, in output order.
    ``definition`` holds what is specific to the kind; it is written to
    the file as ``binner_def``.

    ``name`` is not written to the file; it keeps the uniqueness check,
    the chunk file names and the plan output.  ``parameters`` is
    whatever else a kind needs to say decides the column's values -- a
    constant contribution, the metadata table and filter that map cells
    to groups -- as canonical text, empty when the other fields say it
    all.  It is not written to the file either; it keys the column's
    chunks, so two run definitions sharing a work directory never share
    a chunk they compute differently.
    """

    name: str
    resource_ids: tuple[str, ...]
    group: str
    selector: str
    binner_type: str
    definition: BinnerDef
    binner_index: int = 0
    track_index: int = 0
    parameters: str = ""


@dataclass(frozen=True)
class BinningJob:
    """What one task binds to: tracks read together from one binding.

    ``binner`` names the kind that binds it, which is how the task graph
    finds the binner.  ``tracks`` are the job's columns, in order; the
    block a binding returns for a region has one column per track.
    ``entry`` labels the run-definition entry the job was resolved from
    (``binners[2]``), set when the run definition is parsed.
    """

    binner: str
    tracks: tuple[Track, ...]
    entry: str = ""


class BoundBinner(Protocol):
    """A job bound to its resources: bins them one region at a time."""

    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """Reduce the job's tracks over ``region`` on the global grid.

        Returns a float64 block of shape ``(n_bins, n_tracks)``: one row
        per grid bin of ``region``, one column per track of the job, in
        the job's order.
        """


class Binner(Protocol):
    """What a registered binner kind provides."""

    kind: ClassVar[str]

    # Optional: ``load_def(text) -> BinnerDef`` reads a ``binner_def`` of
    # this kind back.  A kind without it is read as the plain ``dict``.

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
        *, base_dir: str | None = None,
    ) -> list[BinningJob]:
        """Resolve one run-definition entry into jobs.

        The entry's tracks are the jobs' tracks, in order.
        ``label`` names the entry in error messages (``binners[2]``).
        ``base_dir`` is the run definition's directory, passed only when
        the run definition was read from a file; a kind that reads a
        local file resolves a relative path against it.
        Raises :class:`RunDefinitionError` for an entry that cannot be
        resolved, an entry matching nothing included.
        """

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo,
    ) -> AbstractContextManager[BoundBinner]:
        """Bind ``job`` to its resources for as long as the ``with`` lasts.

        A task binds once and asks for every region of its bundle through
        the bound value, so a resource is opened once per task however
        many regions the bundle holds, and is released when the ``with``
        ends, whether the task succeeded or not.
        """


def check_keys(label: str, config: Any, known: frozenset[str]) -> None:
    """Refuse a mapping with keys outside ``known``.

    A mistyped key is refused rather than dropped, so what the user wrote
    never silently changes what the run does.
    """
    if not isinstance(config, dict):
        raise RunDefinitionError(f"{label}: expected a mapping")
    for key in config:
        if key not in known:
            raise RunDefinitionError(
                f"{label}: unknown key {key!r}; known keys: "
                f"{', '.join(sorted(known))}")


def entry_name(label: str, config: dict[str, Any]) -> str | None:
    """An entry's ``name``, the base of its tracks' names (F30).

    Absent, the entry has none and its kind names its tracks.  Given, it
    is a non-empty string; anything else -- ``null`` included -- is
    refused rather than read as absent.
    """
    if "name" not in config:
        return None
    name = config["name"]
    if not isinstance(name, str) or not name:
        raise RunDefinitionError(
            f"{label}: name must be a non-empty string, not {name!r}")
    return name


def named_base(name: str | None, resource_id: str) -> str:
    """The base of a per-resource track's name: ``name/<resource id>``.

    An entry without ``name`` names the track by the resource id alone.
    """
    return resource_id if name is None else f"{name}/{resource_id}"


def match_resources(
    label: str, config: dict[str, Any], grr: GenomicResourceRepo,
    resource_type: str,
) -> list[GenomicResource]:
    """Resolve an entry's ``resource_query`` into its matches, by id.

    The query is always a repository search -- an exact id is the
    search that matches one resource -- restricted to ``resource_type``
    by the search's own type filter, and ordered by resource id, so the
    track order is deterministic whatever the repository yields.  That
    filter needs no index of its own (gain#1212).  A ``search_term`` is
    the full-text index's filter, conjoined with the query (D7), and the
    one key that needs the index.  An entry matching nothing is an
    error, never an empty contribution.
    """
    query = config.get("resource_query")
    if not isinstance(query, str) or not query:
        raise RunDefinitionError(
            f"{label}: resource_query is required and must be a string")
    search_term = config.get("search_term")
    if search_term is not None and not isinstance(search_term, str):
        raise RunDefinitionError(
            f"{label}: search_term must be a string, "
            f"not {search_term!r}")
    # A blank term is an unset one, as the repository reads it (what a
    # shell substitutes for a variable never set); settled once here
    # so the search and the messages below agree.
    if search_term is not None and not search_term.strip():
        search_term = None
    # The search is a generator: the query is checked when it is
    # made, but the term and the index are checked on the first
    # draw, so the consumption sits inside the same try.
    try:
        found = grr.search_resources(
            search_term=search_term, resource_query=query,
            resource_type=resource_type)
        matches = sorted(
            found, key=lambda resource: resource.resource_id)
    except (ResourceQueryParseError, SearchTermError) as err:
        raise RunDefinitionError(f"{label}: {err}") from err
    except SearchIndexUnavailableError as err:
        # The repository's own message carries the remedy; only
        # which key needed the index is the entry's to add.
        raise RunDefinitionError(
            f"{label}: search_term {search_term!r} needs the "
            f"repository's full-text index: {err}") from err
    if not matches:
        # The one deliberate departure from the prototype, which
        # silently produced no column for a query matching nothing.
        narrowed = (
            f" with search_term {search_term!r}" if search_term else "")
        raise RunDefinitionError(
            f"{label}: resource_query {query!r}{narrowed} matches no "
            f"{resource_type} resource")
    return matches


def _missing_mask(
    score_def: GenomicScoreDef, values: npt.NDArray[np.float64],
    cells: npt.NDArray[Any],
) -> npt.NDArray[np.bool_]:
    """Which parsed values are missing, as the per-run fold reads them.

    A ``nan`` value is missing when
    :meth:`~gain.genomic_resources.score_def.GenomicScoreDef.parse_value`
    gives ``None`` for its cell: an NA sentinel or a cell that does not
    parse.  A cell that parses to NaN is a value.
    """
    missing = np.isnan(values)
    for index in np.flatnonzero(missing):
        missing[index] = score_def.parse_value(cells[index]) is None
    return missing


class PositionScoreBinding:
    """One track's score, open for as long as the ``with`` lasts.

    Entering opens the score once, whatever number of regions are then
    binned through it; leaving closes it, on success or failure alike.
    ``value_arrays_batch_size`` is the rows hint of each batch that the
    array fold of :meth:`bin_region` reads.
    """

    def __init__(
        self, track: Track, score: PositionScore, *,
        value_arrays_batch_size: int = DEFAULT_VALUE_ARRAYS_BATCH_SIZE,
    ) -> None:
        self.track = track
        self.score = score
        self.value_arrays_batch_size = value_arrays_batch_size

    def __enter__(self) -> PositionScoreBinding:
        self.score.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.score.close()

    def bin_region(
        self, region: BedRegion, bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """Reduce the track over ``region`` to one float64 per grid bin.

        The bins are those of :meth:`PositionScore.get_score_in_bins`, the
        semantic reference for the global grid, the boundary split and
        first-record-wins.  A ``float`` score under ``count``, ``sum``,
        ``mean``, ``max`` or ``min``, on a backend that serves
        :meth:`~gain.genomic_resources.genomic_scores.base.GenomicScore.fetch_region_value_arrays`,
        is folded from those arrays by
        :class:`~gain.binning.position_fold.ArrayBinFold`, with the same
        bins bit for bit.  Every other track is folded from
        ``get_score_in_bins`` itself.

        A bin no record covers comes back ``None`` and is stored as NaN,
        unless the track's replacement made it count.  An exact int past
        the float range (an int ``sum`` or ``product``) is stored as a
        signed infinity: see
        :func:`~gain.genomic_resources.aggregators.to_float64`.

        A chromosome the score never mentions is one uncovered run
        (gain#1211), so the chromosome's bins are uncovered like any
        other's.

        Returned as a block of one column, the job's one track.
        """
        definition = self.track.definition
        assert isinstance(definition, PositionScoreDef)
        if self._folds_arrays(definition, bin_size):
            column = self._array_column(definition, region, bin_size)
        else:
            column = self._per_run_column(definition, region, bin_size)
        return column.reshape(-1, 1)

    def _folds_arrays(
        self, definition: PositionScoreDef, bin_size: int,
    ) -> bool:
        """Whether the array fold gives this track's bins."""
        selector = self.track.selector
        score_def = self.score.score_definitions.get(selector)
        return (
            score_def is not None
            and score_def.value_type == "float"
            and definition.aggregator in ARRAY_FOLD_AGGREGATORS
            and replacement_is_exact(
                definition.none_value_replacement, bin_size)
            and self.score.supports_region_value_arrays([selector]))

    def _array_column(
        self, definition: PositionScoreDef, region: BedRegion,
        bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """The bins folded from the value arrays of the region."""
        if region.start < 1 or region.stop < region.start:
            raise ValueError(
                f"genomic score <{self.score.resource_id}> asked for the "
                f"region {region.chrom}:{region.start}-{region.stop}; "
                f"positions are 1-based and the end does not precede "
                f"the start")
        if bin_size < 1:
            raise ValueError(
                f"genomic score <{self.score.resource_id}> asked for bins "
                f"of size {bin_size}; a bin holds at least one position")
        fold = ArrayBinFold(
            definition.aggregator, definition.none_value_replacement,
            region.start, region.stop, bin_size)
        # The table refuses a contig it does not hold, and such a contig
        # is one uncovered run: no batch reaches the fold.
        if self.score.has_chromosome(region.chrom):
            selector = self.track.selector
            score_def = self.score.score_definitions[selector]
            # ``fetch_region_value_arrays`` gives ``nan`` both for a
            # missing value and for a NaN value of the score, and the fold
            # must tell the two apart.  The parse that it is made of also
            # gives the raw cells, which tell them apart.
            # pylint: disable=protected-access
            score = self.score
            columns = score._score_column_indexes(  # ruff: ignore[private-member-access]
                [selector])
            batches = score._parsed_column_batches(  # ruff: ignore[private-member-access]
                columns, (region.chrom, region.start, region.stop),
                batch_size=self.value_arrays_batch_size)
            for pos_begin, pos_end, values, cells in batches:
                fold.add_batch(
                    pos_begin, pos_end, values[selector],
                    _missing_mask(
                        score_def, values[selector],
                        cells[columns[selector]]))
        return fold.finish()

    def _per_run_column(
        self, definition: PositionScoreDef, region: BedRegion,
        bin_size: int,
    ) -> npt.NDArray[np.float64]:
        """The bins of ``get_score_in_bins``, as float64."""
        return np.fromiter(
            (np.nan if value is None else to_float64(value)
             for _, _, value in self.score.get_score_in_bins(
                 region.chrom, region.start, region.stop, bin_size,
                 score=self.track.selector,
                 aggregator=definition.aggregator,
                 none_value_replacement=definition.none_value_replacement)),
            dtype=np.float64)


class PositionScoreBinner:
    """Bins ``position_score`` resources matched by a ``resource_query``."""

    kind: ClassVar[str] = "position_score_binner"

    @staticmethod
    def load_def(text: str) -> PositionScoreDef:
        """Read a ``binner_def`` of this kind."""
        return PositionScoreDef.from_json(text)

    ENTRY_KEYS: ClassVar[frozenset[str]] = frozenset({
        "resource_query", "search_term", "aggregator",
        "none_value_replacement", "name"})

    @classmethod
    def parse_entry(
        cls, label: str, config: dict[str, Any], grr: GenomicResourceRepo,
        *,
        base_dir: str | None = None,  # ruff: ignore[unused-class-method-argument]
    ) -> list[BinningJob]:
        """Resolve one entry's ``resource_query`` into one job per track.

        The matches are :func:`match_resources`'s, restricted to position
        scores: one track each, in resource-id order.
        """
        check_keys(label, config, cls.ENTRY_KEYS)
        name = entry_name(label, config)
        matches = match_resources(label, config, grr, "position_score")
        return [
            BinningJob(binner=cls.kind, tracks=(cls._track_of(
                label, resource,
                name=name,
                aggregator=config.get("aggregator"),
                none_value_replacement=config.get("none_value_replacement"),
            ),))
            for resource in matches
        ]

    @staticmethod
    def bind(
        job: BinningJob, grr: GenomicResourceRepo,
    ) -> PositionScoreBinding:
        """Bind the job's one track to its score.

        The score is opened when the binding is entered and closed when
        it is left.
        """
        (track,) = job.tracks
        (resource_id,) = track.resource_ids
        return PositionScoreBinding(
            track, PositionScore(grr.get_resource(resource_id)))

    @classmethod
    def _track_of(
        cls, label: str, resource: GenomicResource, *,
        name: str | None,
        aggregator: str | None,
        none_value_replacement: Any,
    ) -> Track:
        """One track per matched resource, validated the score's own way.

        The score resolves the aggregator default and judges the
        replacement against its value type; the resolver stops at the
        aggregator's NAME, so that the name builds is asked separately.
        Only the rules that are the tool's own are checked here: a track
        is exactly one score, and every cell of ``/values`` is a float64
        (D11), so the score must be numeric and the aggregator must
        produce a number.
        """
        score = PositionScore(resource)
        if len(score.score_definitions) != 1:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} defines "
                f"{len(score.score_definitions)} scores, "
                f"{sorted(score.score_definitions)}; a track is one score, "
                f"and this binner takes a resource with exactly one")
        (score_id, score_def), = score.score_definitions.items()
        if score_def.value_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r} score "
                f"{score_id!r} is of type {score_def.value_type!r}; "
                f"only a numeric score (int or float) can be binned")
        try:
            resolved = score.resolve_aggregation_queries([
                PositionScoreAggregationQuery(
                    score_id, aggregator, none_value_replacement),
            ])
            _, aggregator_name, replacement = resolved[0]
            validate_aggregator(aggregator_name, score_def.value_type)
        except ValueError as err:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r}: "
                f"{err.args[0]}") from err
        output_type = _output_type(
            AggregatorDefinition.from_string(aggregator_name)
            .aggregator_type,
            score_def.value_type)
        if output_type not in NUMERIC_VALUE_TYPES:
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r}: aggregator "
                f"{aggregator_name!r} does not produce a number; use one "
                f"of {', '.join(numeric_aggregators())}")
        assert replacement is None or isinstance(replacement, int | float)
        if replacement is not None and not _is_finite_number(replacement):
            raise RunDefinitionError(
                f"{label}: resource {resource.resource_id!r}: "
                f"none_value_replacement {replacement!r} must be finite; "
                f"the binner_def cannot hold NaN or infinity")
        return Track(
            name=named_base(name, resource.resource_id),
            resource_ids=(resource.resource_id,),
            group="",
            selector=score_id,
            binner_type=binner_type_of(cls.kind),
            definition=PositionScoreDef(
                aggregator=aggregator_name,
                none_value_replacement=replacement, name=name),
        )


def _is_finite_number(value: float) -> bool:
    """Whether value converts to a finite float; a huge int does not."""
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def aggregator_of(track: Track) -> str:
    """The aggregator a track's definition names, empty if it has none."""
    return str(getattr(track.definition, "aggregator", ""))


def load_track_def(
    binner_type: str, text: str,
    kinds: dict[str, type[Binner]] | None = None,
) -> BinnerDef | dict[str, Any]:
    """Read a ``binner_def`` of the file back into its typed definition.

    A ``binner_type`` with no installed kind, or a kind without
    ``load_def``, gives the plain ``dict`` of ``json.loads``, so a file
    stays readable without its plugin.
    """
    if kinds is None:
        kinds = discover_binner_kinds()
    for kind, binner in kinds.items():
        if binner_type_of(kind) == binner_type:
            load_def = getattr(binner, "load_def", None)
            if load_def is not None:
                loaded: BinnerDef = load_def(text)
                return loaded
            break
    plain: dict[str, Any] = json.loads(text)
    return plain


def discover_binner_kinds() -> dict[str, type[Binner]]:
    """Map every registered binner kind to its class, by the class's kind."""
    kinds: dict[str, type[Binner]] = {}
    for entry in entry_points(group=BINNERS_ENTRY_POINT_GROUP):
        binner = entry.load()
        kinds[binner.kind] = binner
    return kinds
