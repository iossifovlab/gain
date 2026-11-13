from __future__ import annotations

import importlib
import sys
import types
import warnings
from collections.abc import Callable, Generator, Iterable
from typing import Any, NamedTuple, cast

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    owned_records_mask,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    Histogram,
    HistogramConfig,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.resource_errors import (
    HistogramError,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.statistics.alleles import (
    RegionAlleles,
    allele_arrays_folded_into,
    records_folded_into,
)
from gain.genomic_resources.statistics.coverage import (
    RegionCoverage,
)
from gain.genomic_resources.statistics.fragments import (
    RegionFragments,
)
from gain.genomic_resources.statistics.record_validation import (
    validate_record_arrays,
    validate_records,
)

logger = logging.getLogger(__name__)

#: What this module publishes.  Stated rather than left to be inferred
#: from which names happen to lack a leading underscore.
#:
#: Three kinds of thing, sorted here but worth naming: the per-record read
#: (``scan_region``) and the result a region's task hands to the merge
#: (``RegionScanResult``); the shared driver of the vectorized passes
#: (``bulk_region_scan``) and the gates that choose it
#: (``bulk_scan_eligible``, ``can_bulk_*``); and the config step
#: (``unpack_score_defs``).  The passes themselves and their task bodies
#: live in ``scan_min_max``, ``scan_histograms`` and ``scan_noregion``,
#: which import this module, never the reverse.
#:
#: Everything absent is a per-batch inner step (``_accumulate_arrays``,
#: ``_select_and_weigh``, ...) that no caller outside the scan modules
#: has any business reaching for.
__all__ = [
    "RegionScanResult",
    "bulk_region_scan",
    "bulk_scan_eligible",
    "can_bulk_histogram",
    "can_bulk_min_max",
    "scan_region",
    "unpack_score_defs",
]

#: The public names that moved out of this module (gain#2269), each with
#: its new sibling module.  They stay importable from here for one release,
#: with a ``DeprecationWarning`` that names the new module.  The siblings
#: are named relative to this package, so this module names no module of
#: its own package (see ``tests/test_architecture.py``).
_MOVED: dict[str, str] = {
    **dict.fromkeys((
        "do_min_max",
        "do_min_max_bulk",
        "do_min_max_task",
        "merge_min_max",
    ), "scan_min_max"),
    **dict.fromkeys((
        "do_histogram",
        "do_histogram_bulk",
        "do_histogram_task",
        "merge_and_save_histograms",
        "merge_histograms",
        "update_hist_confs",
    ), "scan_histograms"),
    "do_noregion_histograms": "scan_noregion",
}


class _MovedNamesModule(types.ModuleType):
    """This module, warning when a caller reads a name that moved out.

    The moved names are not defined here, so a read of one finds its
    sibling module and returns the object from there.  An import statement
    and ``module.name`` both read through here.  The sibling is imported
    only when a caller reads a moved name: the siblings import this
    module, and this module imports none of them when it loads.
    """

    def __getattribute__(self, name: str) -> Any:
        moved_to = _MOVED.get(name)
        if moved_to is None:
            return super().__getattribute__(name)
        package = super().__getattribute__("__package__")
        new_module = f"{package}.{moved_to}"
        warnings.warn(
            f"{name} moved to {new_module}", DeprecationWarning, stacklevel=2)
        return getattr(importlib.import_module(new_module), name)


sys.modules[__name__].__class__ = _MovedNamesModule

#: Which score value types each histogram kind can accumulate a whole BATCH
#: of -- the pairing :func:`can_bulk_histogram` gates the vectorized
#: histogram scan on.  Narrower than
#: :data:`~gain.genomic_resources.histogram.NUMBER_HISTOGRAM_VALUE_TYPES`,
#: which says what a number histogram folds one value at a time:
#: the bulk read yields a number histogram's column as ``float64``, which a
#: ``bool`` score's column is not, and a categorical's as ``str`` objects.
#: A NULL histogram accumulates nothing and so appears in neither.
#:
#: :func:`can_bulk_min_max` deliberately does NOT read this, though its own
#: tuple matches the number row: a min/max pass accumulates no histogram,
#: and sharing the literal would let a future widening of one widen the
#: other by accident.  Its docstring says so at the point of the copy.
_BULK_HISTOGRAM_VALUE_TYPES: dict[type[HistogramConfig], tuple[str, ...]] = {
    NumberHistogramConfig: ("float", "int"),
    CategoricalHistogramConfig: ("str",),
}


def _score_for(
    resource: GenomicResource,
    score: GenomicScore | None,
) -> GenomicScore:
    """The score this call reads: the caller's, or one built for it.

    The one place the ``score`` parameter threaded through this module is
    honoured, so that "a caller may hand its own score down" is stated
    once instead of at each of the six sites that would otherwise write
    the same conditional.

    **Why the parameter exists.** ``GenomicScore.__init__`` is ~98% the
    cerberus normalize-and-validate pass over the resource's config, and
    that pass scales with the score count -- milliseconds for a wide
    resource.  A scan task used to pay it three or four times per region
    (gain#1038): once for the allele probe, once in each gate it asks,
    and once more in the scan that serves the region.  The config does
    not change between those calls, so they all built the same thing.

    **Why not memoize the factory instead.** ``build_score_from_resource``
    documents the opposite contract -- every call yields a fresh instance
    the caller owns -- and it is called from the annotation pipeline and
    the web tier, not just here.  Sharing is therefore made explicit at
    the call site, and bounded by it.

    **Why the sharing stops at the task frame.** Not because the score
    could not survive the trip: ``scan_noregion.do_noregion_histograms``
    calls the two task functions in-process, with no task graph between
    them, and a score would reach them intact.  It stops because a
    ``do_*_task`` signature is a SCHEDULING CONTRACT -- ``impl`` hands
    those arguments to ``TaskGraph.make_task``, and under a distributed
    executor they are serialized.  A parameter that cannot appear in that
    list does not belong in that signature, whoever the caller happens to
    be.

    **The pair must match.** ``resource`` is ignored when a ``score`` is
    given, so the two must describe the same resource; handing a score
    built from another one would scan that other resource silently.  Not
    asserted, because identity is too strong a test -- a caller may
    legitimately hold an equivalent resource re-fetched from the
    repository -- and every call site is in this module.

    **Who closes it.** A score handed to one of the scans is OPENED by
    that scan and CLOSED on return, which is what the freshly built one
    always got: a scan is the last thing a task does with its score, and
    leaving a pysam handle open for the caller to remember would be the
    surprise.  It holds for a score handed in already open too -- the
    scan does not adopt the caller's lifetime, it ends it.  The gates
    open nothing and so close nothing.
    """
    if score is not None:
        return score
    return build_score_from_resource(resource)


def _allele_batches(
    score: GenomicScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
    *,
    alleles: RegionAlleles,
    batch_size: int,
) -> Iterable[RecordArrays]:
    """The bulk producer for a region carrying an allele statistic.

    Only the score kind differs from the shared producer, and only the
    statistics module knows that; this adapter is what lets
    ``bulk_region_scan`` stay a driver that reads whatever producer it
    was handed.
    """
    return allele_arrays_folded_into(
        cast(AlleleScore, score), chrom, start, end, score_ids,
        batch_size=batch_size, alleles=alleles)


class RegionScanResult(NamedTuple):
    """What one region's statistics task hands to the merge step."""

    histograms: dict[str, Histogram]
    coverage: RegionCoverage | None
    fragments: RegionFragments | None
    alleles: RegionAlleles | None


def unpack_score_defs(
    resource: GenomicResource,
) -> tuple[list[str], dict[str, HistogramConfig]]:
    """Extracts scores with min/max and histogram configs for a score.

    The configs are the histograms this build will attempt: a score whose
    definition resolves to a null histogram (a stated ``type: null``, or a
    value type with no default) is left out, so a ``NullHistogramConfig``
    among them is always one the build itself produced
    (:func:`.scan_histograms.update_hist_confs`), and is recorded as
    such.  The definition's null stays unrecorded, decided from the config
    alone (gain#305); the save stage drops what an earlier build left for
    it.

    It refuses nothing.  A configured NUMBER histogram over a value type no
    number histogram can fold used to be nullified here (gain#1285), which
    left the resource building with one score silently histogram-less and
    the reason only in the log (gain#1307); gain#1336 moved that to score
    CONSTRUCTION, so a resource whose config states the pairing never
    reaches a statistics build at all.  What is still left to the per-value
    catch in :func:`.scan_histograms.do_histogram` is the other direction
    -- a CATEGORICAL histogram meeting a value it cannot fold -- because
    that is a fact about a value rather than about the config.
    """
    score = build_score_from_resource(resource)
    all_min_max_scores = []
    all_hist_confs: dict[str, HistogramConfig] = {}
    with score.open():
        for score_id in score.score_definitions:
            # The same resolution the histogram ADDRESS refuses on first:
            # a histogram annulled by definition is neither plotted nor
            # addressed -- nor attempted.  (One nullified further down,
            # by its statistics, loses its address the other way -- no
            # image reaches the manifest, and only a listed image is
            # addressed.)
            hist_conf = score.get_histogram_config(score_id)
            if isinstance(hist_conf, NullHistogramConfig):
                continue

            if isinstance(hist_conf, CategoricalHistogramConfig):
                all_hist_confs[score_id] = hist_conf
                continue

            # A NUMBER histogram here can only be over a value type one can
            # accumulate: the pairing is refused when the score is
            # CONSTRUCTED (gain#1336), and ``build_score_from_resource``
            # above is what constructs it, so an unfoldable one never
            # reaches this loop.
            assert isinstance(hist_conf, NumberHistogramConfig)
            if not hist_conf.has_view_range():
                all_min_max_scores.append(score_id)
            all_hist_confs[score_id] = hist_conf
    return all_min_max_scores, all_hist_confs


def scan_region(
    score: GenomicScore,
    chrom: str,
    start: int | None,
    end: int | None,
    score_ids: list[str],
    *,
    alleles: RegionAlleles | None = None,
) -> Generator[
        tuple[int, int, list[ScoreValue]], None, None]:
    """Read a region the way the statistics scan reads it.

    Every per-record pass reads here, so validation is composed in the
    open -- one visible extra link over the record stream -- rather than
    each pass being trusted to remember it (ADR 0008).  What the pass
    then sees is exactly what a reader sees: the same transform, over the
    same records, in the same order.

    One read.  ``validate_records`` is a transducer over the very stream
    the transform consumes, not a second pass over the region -- and so
    is the optional allele fold, which reads the nucleotides off the RAW
    records the transform is about to collapse to points (gain#777).
    """
    records = validate_records(
        score, score.fetch_records(chrom, start, end))
    if alleles is not None:
        records = records_folded_into(records, alleles)
    yield from score.values_from_records(records, chrom, score_ids)


#: The most rows one bulk read pulls per batch.  The score's read caps it
#: further by its cell budget (``genomic_scores.batch_budget``), so a wide
#: score gets fewer rows per batch than a narrow one.
_SCAN_BATCH_SIZE = 100_000


# ``AccT`` is the per-batch accumulator target of a bulk region scan -- a
# Histogram for the histogram pass, a MinMaxValue for the min/max pass.  It
# keeps ``bulk_region_scan`` generic without losing either caller's dict type.
def bulk_region_scan[AccT](
    resource: GenomicResource,
    result: dict[str, AccT],
    chrom: str,
    start: int,
    end: int,
    accumulate: Callable[
        [RecordArrays, dict[str, AccT],
         tuple[str, int | None, int | None], GenomicScore],
        None],
    *,
    batches: Callable[
        [GenomicScore, str, int, int, list[str]],
        Iterable[RecordArrays]] | None = None,
    score: GenomicScore | None = None,
) -> dict[str, AccT]:
    """Drive a bulk region scan, folding each batch into ``result``.

    Public, and called by the sibling pass modules: ADR 0001 names it as
    the shared driver behind both bulk passes, so it is part of what
    ``scan`` states about itself rather than a helper.

    The shared skeleton of :func:`.scan_histograms.do_histogram_bulk` and
    :func:`.scan_min_max.do_min_max_bulk`: open the score and stream the
    region's column-array batches through ``accumulate`` (which mutates
    ``result``).  The caller supplies the pre-built ``result`` -- empty
    histograms or seeded ``MinMaxValue`` -- and the matching accumulator.
    Batches are keyed by SCORE ID: the score resolves each id to its
    payload column itself (gain#398), so nothing here handles column
    indices.

    This is the scan's vectorized door, and the counterpart of
    :func:`scan_region`: the batches are read through
    ``validate_record_arrays``, one visible extra link over the stream
    the scan is already pulling, so that a pass cannot be added that
    quietly reads unvalidated (ADR 0008).  That function dispatches on the
    score's class to the rule registered for its kind (ADR 0027); nothing
    here knows what the rule is.

    The validator is per REGION, and a region lies within one contig, so
    the ordering carry never spans a contig boundary -- the same reason
    the per-record validators reset on a change of chromosome.

    A ``score`` handed in is opened here and closed on return; see
    :func:`_score_for`.
    """
    with _score_for(resource, score).open() as opened:
        if batches is None:
            batches = _validated_batches
        for arrays in batches(opened, chrom, start, end, list(result)):
            accumulate(arrays, result, (chrom, start, end), opened)
    return result


def _validated_batches(
    score: GenomicScore,
    chrom: str,
    start: int,
    end: int,
    score_ids: list[str],
) -> Iterable[RecordArrays]:
    """The default producer: the shared read through the shared door."""
    return validate_record_arrays(
        score,
        score.fetch_region_value_arrays(
            chrom, start, end, score_ids,
            batch_size=_SCAN_BATCH_SIZE),
        chrom)


def _accumulate_arrays(
    arrays: RecordArrays,
    result: dict[str, Histogram],
    region: tuple[str, int | None, int | None],
    score: GenomicScore,
) -> None:
    """Fold one batch of column arrays into the per-score histograms.

    ``arrays`` is one ``(pos_begin, pos_end, {score_id: cells})`` batch as
    produced by :meth:`_region_value_arrays`.  Selects the records this
    region OWNS exactly as the per-record read does, weighs each at
    its full span as ``score``'s kind weighs it, and adds each
    score's values vectorized.  Whether the
    batch is one this kind's records may form was settled before it got
    here, by the door's ``validate_record_arrays``.

    A histogram that refuses its batch is nullified and the rest of the
    resource's scores carry on, exactly as
    :func:`.scan_histograms.do_histogram` nullifies one that refuses a
    value: a categorical histogram raises once its values outgrow
    ``UNIQUE_VALUES_LIMIT``, and the score it belongs to must not cost
    the others their statistics.  A nullified score is
    skipped by every later batch, which is what the per-record path gets
    from ``NullHistogram.add_value`` being a no-op.
    """
    pos_begin, pos_end, value_cells = arrays
    keep, weights = _select_and_weigh(
        pos_begin, pos_end, region, score)

    for score_id, hist in result.items():
        if not isinstance(hist, (NumberHistogram, CategoricalHistogram)):
            continue
        values = value_cells[score_id][keep]
        try:
            hist.add_batch(values, weights)
        except TypeError as err:
            logger.exception(
                "Failed adding a batch of %s values to the histogram of "
                "%s; %s:%s-%s", values.size, score.resource_id, *region)
            result[score_id] = NullHistogram(NullHistogramConfig(str(err)))
        except HistogramError as err:
            logger.warning(
                "Histogram for %s nullified: %s", score_id, err)
            result[score_id] = NullHistogram(NullHistogramConfig(str(err)))


def _select_and_weigh(
    pos_begin: np.ndarray,
    pos_end: np.ndarray,
    region: tuple[str, int | None, int | None],
    score: GenomicScore,
) -> tuple[np.ndarray, np.ndarray]:
    """Select a batch's owned records and weigh them, per ``score``'s kind.

    Returns ``(keep, weights)``:
    :func:`~gain.genomic_resources.genomic_scores.records.owned_records_mask`,
    and the owned records' weights measured at their FULL span --
    never clipped to the region.  Selecting instead of clipping is
    what makes a statistic independent of ``--region-size``
    (gain#816): a record straddling a boundary used to be measured by
    both regions, which summed to the right answer only for a
    span-weighted kind and double-counted every count-weighted one.

    Because the span is the record's own, an inverted span can no
    longer be MADE here by clipping -- the gain#636 edge is
    unrepresentable on this path rather than merely guarded.

    Measuring only.  Whether the batch is one this kind's records may
    form is settled upstream, by the door's ``validate_record_arrays``,
    against the RAW columns -- which is why no rule is stated here.

    The weight is read off the score class, which states it once for this
    path and for the per-record one: ``record_weight`` -- a position-score
    record counts once per base pair it spans; an allele record and a
    fragment count 1, however wide they are.  This path never calls it
    directly; ``record_weights`` broadcasts it over the owned records'
    position columns, which is the only weight read this scan makes.
    """
    _chrom, start, end = region
    keep = owned_records_mask(pos_begin, start, end)
    return keep, score.record_weights(pos_begin[keep], pos_end[keep])


def can_bulk_histogram(
    resource: GenomicResource,
    all_hist_confs: dict[str, HistogramConfig],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether the vectorized scan may serve this histogram build.

    :func:`bulk_scan_eligible` plus the conditions that are this caller's
    alone -- every score must feed a histogram that can accumulate a whole
    batch, and be handed the batch shape that histogram accepts:

    * a NUMBER histogram takes a ``float`` or an ``int`` score, whose
      columns the bulk read yields as the ``float64``
      ``NumberHistogram.add_batch`` accumulates;
    * a CATEGORICAL histogram takes a ``str`` score, whose column the bulk
      read yields as the ``str`` objects
      ``CategoricalHistogram.add_batch`` counts;
    * a NULL histogram has nothing to accumulate and is skipped by both
      paths, so it constrains neither.

    The two pairings are the whole rule, and the mismatches are what it
    exists to keep out: the per-record path meets a value its histogram
    refuses one at a time, catches the ``TypeError`` and nullifies that
    one score, whereas a batch of the wrong shape is not a value the
    histogram can refuse -- it is a coercion failure inside ``add_batch``.
    So a categorical histogram over an ``int`` score keeps
    :func:`.scan_histograms.do_histogram`, which handles it as it always
    has.

    A number histogram over a ``str`` score never reaches either path: such
    a config is refused when the score is CONSTRUCTED
    (:func:`~gain.genomic_resources.score_resource.refuse_unfoldable_histograms`),
    so no definition carrying it survives to be scanned.  The condition
    stays stated here because it is this gate's own -- the batch shapes it
    admits are not a consequence of that refusal, and a ``bool`` score
    separates them: the per-value rule keeps it, this one does not.
    """
    bulk_score_ids = []
    score = _score_for(resource, score)
    score_defs = score.score_definitions
    for score_id, hist_conf in all_hist_confs.items():
        if isinstance(hist_conf, NullHistogramConfig):
            continue
        value_types = _BULK_HISTOGRAM_VALUE_TYPES.get(type(hist_conf))
        score_def = score_defs.get(score_id)
        if value_types is None or score_def is None \
                or score_def.value_type not in value_types:
            return False
        bulk_score_ids.append(score_id)
    return bulk_scan_eligible(
        resource, bulk_score_ids, score=score)


def can_bulk_min_max(
    resource: GenomicResource,
    score_ids: list[str],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether the vectorized scan may serve this min/max pass.

    :func:`bulk_scan_eligible` plus the one condition that is this
    caller's alone: every score must be a NUMBER.  The reduction is
    ``min()``/``max()`` over the non-nan values of a float64 column, and a
    ``str`` score's column is an object array, which ``np.isnan`` refuses
    outright.

    A str score is never SCHEDULED for a min/max pass at all: the only
    thing that schedules one is a number histogram without a view range,
    and a score construction carrying that pairing raises
    (:func:`~gain.genomic_resources.score_resource.refuse_unfoldable_histograms`),
    so no such resource reaches a scan.  The condition stays stated
    rather than assumed from that one, because it is this pass's own: left
    ungated, a column of nothing but NA sentinels would raise here, out of
    a generator and past every nullify handler, where the per-record path
    yields an empty min/max and nullifies that one histogram.

    Written out rather than read off :data:`_BULK_HISTOGRAM_VALUE_TYPES`,
    which the tuple happens to match: that constant says what a HISTOGRAM
    can accumulate a batch of, and this pass accumulates no histogram.
    Sharing the literal would make a future widening of the number
    histogram's batch types widen this gate as a side effect.
    """
    score = _score_for(resource, score)
    score_defs = score.score_definitions
    for score_id in score_ids:
        score_def = score_defs.get(score_id)
        if score_def is None \
                or score_def.value_type not in ("float", "int"):
            return False
    return bulk_scan_eligible(
        resource, score_ids, score=score)


def bulk_scan_eligible(
    resource: GenomicResource,
    score_ids: list[str],
    *,
    score: GenomicScore | None = None,
) -> bool:
    """Whether a vectorized region scan may serve these scores.

    The shared gate for the histogram and min/max bulk paths.  Both
    conditions belong to the score, so both are asked of it:

    * every score of a value type the column parse defines
      (``float``, ``int``, ``str``) -- asked of the score, which owns
      that parse;
    * and the backend serves the bulk read at all -- asked of the score,
      not tested on the table's class.  This is what keeps a VCF-backed
      allele score on the per-record path: its record payload is not a raw
      row, so its table declares no column-array support.

    **No resource kind is tested.**  Eligibility is a claim about a score,
    so it is asked of one: handed a resource that cannot become a score,
    and no ``score`` to use instead, this raises ``ValueError`` out of the
    factory rather than answering ``False``, which would read as a fact
    about a score and send a caller holding the wrong resource quietly
    down the per-record path.  With a ``score`` given the factory is not
    called, and the pair is the caller's to match (see :func:`_score_for`).

    A new kind needs no guard here either: ``record_weight`` is abstract on
    ``GenomicScore``, a kind absent from ``record_validation``'s registry is
    refused by the array door below, and a backend serving no column arrays
    is refused above.  ADR 0001 records why the kind condition was retired,
    and ADR 0027 why the second of those three refusals is now a run-time one
    rather than something mypy and pylint catch first.

    Answered WITHOUT opening the score: the table and the score definitions
    are both built in ``GenomicScore.__init__``, so nothing here needs a
    file handle.  That is also why ``score`` may be handed in already
    closed -- a caller that has finished reading through it can still ask
    this (see :func:`_score_for` for why it would want to).
    """
    return _score_for(resource, score).supports_region_value_arrays(score_ids)
