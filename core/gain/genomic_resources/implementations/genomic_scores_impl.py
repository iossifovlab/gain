from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, ClassVar, TypeVar, cast

import numpy as np

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.record import (
    POS_END,
)
from gain.genomic_resources.genomic_position_table.table_bigwig import (
    BigWigTable,
)
from gain.genomic_resources.genomic_position_table.table_inmemory import (
    InmemoryGenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    RecordOrdering,
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    Histogram,
    HistogramConfig,
    HistogramError,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    build_default_histogram_conf,
    build_empty_histogram,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    resolve_tabix_index_filename,
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    equivalent_resource_types,
)
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.task_graph.graph import Task, TaskDesc, TaskGraph
from gain.utils.regions import (
    Region,
    get_chromosome_length_tabix,
    split_into_regions,
)

logger = logging.getLogger(__name__)

# The per-batch accumulator target of a bulk region scan -- a Histogram for the
# histogram pass, a MinMaxValue for the min/max pass.  A TypeVar keeps
# ``_bulk_region_scan`` generic without losing either caller's dict type.
_AccT = TypeVar("_AccT")

# The resource kinds whose statistics the vectorized scan may serve, in every
# spelling of each.  Expanded through ``equivalent_resource_types`` rather than
# written out: a fragment score has TWO permanent type strings
# (``fragment_score`` and ``cnv_collection``, gain#471), and a literal set
# naming only one of them would send the other silently back to the per-record
# path -- no error, no failing test, just the slow path forever.  ``np_score``
# is deliberately absent: no production GRR has one, so the bulk path is not
# exercised against it (gain#421).
_BULK_SCAN_RESOURCE_TYPES = frozenset(
    spelling
    for resource_type in ("position_score", "allele_score", "fragment_score")
    for spelling in equivalent_resource_types(resource_type)
)


class GenomicScoreImplementation(ScoreImplementationBase):
    # pylint: disable=too-many-public-methods
    """Genomic scores base class."""

    def __init__(self, resource: GenomicResource):
        super().__init__(resource)
        self.score: GenomicScore = build_score_from_resource(resource)

    def get_config_histograms(self) -> dict[str, Any]:
        """Collect all configurations of histograms for the genomic score."""
        result: dict[str, Any] = {}
        for score_id, score_def in self.score.score_definitions.items():
            result[score_id] = score_def.hist_conf

        return result

    template_name: ClassVar[str] = "genomic_score.jinja"
    styles_template_name: ClassVar[str] = "genomic_score_styles.jinja"

    def _get_template_data(self) -> dict[str, Any]:
        return {"genomic_scores": self}

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    @staticmethod
    def _do_noregion_histograms(
        resource: GenomicResource,
    ) -> None:
        """Compute a resource's statistics in one task, contig by contig.

        The ``--region-size 0`` path: one task, no splitting *within* a
        contig.  It used to be one scan of the whole table, expressed by
        handing ``chrom=None`` down to the score -- and ``_get_chrom_regions``
        manufactured a ``Region(None, None, None)`` to say so, with a
        ``# type: ignore`` and a comment conceding it was "a bit hacky".  The
        region-read family now requires a contig, so the iteration is stated
        here instead of smuggled through as a null.

        Each contig is scanned by the very functions the per-region tasks use
        and folded together by the very functions that fold those tasks'
        results, so this produces what a split run produces -- not something
        merely believed to match.  An empty contig (an unscored alt, of which
        hg38 has hundreds) contributes an empty histogram, which merges
        cleanly; ``_merge_histograms`` only nullifies on a genuine error.
        """
        impl = build_score_implementation_from_resource(resource)
        all_min_max_scores, all_hist_confs = \
            impl._unpack_score_defs(resource)  # noqa: SLF001

        with impl.score.open() as score:
            chroms = list(score.get_all_chromosomes())

        if all_min_max_scores:
            all_hist_confs = GenomicScoreImplementation._merge_min_max(
                all_min_max_scores,
                all_hist_confs,
                *(GenomicScoreImplementation._do_min_max(
                    resource, all_min_max_scores, chrom, None, None)
                  for chrom in chroms),
            )
        hist_result = GenomicScoreImplementation._merge_histograms(
            resource,
            *(GenomicScoreImplementation._do_histogram(
                resource, all_hist_confs, chrom, None, None)
              for chrom in chroms),
        )
        GenomicScoreImplementation._save_histograms(
            resource,
            hist_result,
        )

    def create_statistics_build_tasks(
        self, **kwargs: Any,
    ) -> list[TaskDesc]:
        region_size = kwargs.get("region_size", 3_000_000_000)
        grr = kwargs.get("grr")

        if region_size <= 0:
            # No regions; compute histograms directly.
            return [
                TaskGraph.make_task(
                    f"{self.resource.get_full_id()}_noregion_histograms",
                    GenomicScoreImplementation._do_noregion_histograms,
                    args=[self.resource],
                    deps=[],
                ),
            ]

        with self.score.open():
            regions = self._get_chrom_regions(region_size, grr)
            all_min_max_scores, all_hist_confs = \
                self._unpack_score_defs(self.resource)

            tasks: list[TaskDesc] = []
            merge_min_max_task: Task | dict[str, Any] = all_hist_confs
            if all_min_max_scores:
                min_max_tasks = []
                for region in regions:
                    chrom = region.chrom
                    start = region.start
                    end = region.stop
                    task = TaskGraph.make_task(
                        f"{self.resource.get_full_id()}_calculate_min_max"
                        f"_{chrom}_{start}_{end}",
                        GenomicScoreImplementation._do_min_max_task,
                        args=[
                            self.resource,
                            all_min_max_scores,
                            chrom, start, end],
                        deps=[],
                    )
                    min_max_tasks.append(task.task)
                    tasks.append(task)
                merge_task = TaskGraph.make_task(
                    f"{self.resource.get_full_id()}_merge_min_max",
                    GenomicScoreImplementation._merge_min_max,
                    args=[
                        all_min_max_scores,
                        all_hist_confs,
                        *min_max_tasks,
                    ],
                    deps=[],
                )
                tasks.append(merge_task)
                merge_min_max_task = merge_task.task

            histogram_tasks = []
            for region in regions:
                chrom = region.chrom
                start = region.start
                end = region.stop
                task = TaskGraph.make_task(
                    f"{self.resource.get_full_id()}_calculate_histogram_"
                    f"{chrom}_{start}_{end}",
                    GenomicScoreImplementation._do_histogram_task,
                    args=[
                        self.resource,
                        merge_min_max_task,
                        chrom, start, end],
                    deps=[],
                )
                histogram_tasks.append(task.task)
                tasks.append(task)
            save_task = TaskGraph.make_task(
                f"{self.resource.get_full_id()}_merge_and_save_histograms",
                GenomicScoreImplementation._merge_and_save_histograms,
                args=[self.resource, *histogram_tasks],
                deps=[],
            )
            tasks.append(save_task)

            return tasks

    _REF_GENOME_CACHE: ClassVar[dict[str, Any]] = {}

    @property
    def files(self) -> set[str]:
        filename = self.score.table.definition.filename
        files = {filename}
        if isinstance(self.score.table, TabixGenomicPositionTable):
            # The statistics hash is computed against the same manifest, so
            # resolving from it here is free and keeps the two consistent.
            index_filename = resolve_tabix_index_filename(
                self.resource.get_manifest(), filename)
            if index_filename is None:
                logger.warning(
                    "resource <%s>: tabix table %s has no index "
                    "(neither %s.tbi nor %s.csi) in the resource manifest; "
                    "the index is left out of the resource file set",
                    self.resource.resource_id, filename, filename, filename)
            else:
                files.add(index_filename)
        return files

    @staticmethod
    def _unpack_score_defs(
        resource: GenomicResource,
    ) -> tuple[list[str], dict[str, HistogramConfig]]:
        """Extracts scores with min/max and histogram configs for a score."""
        impl = build_score_implementation_from_resource(resource)
        all_min_max_scores = []
        all_hist_confs: dict[str, HistogramConfig] = {}
        with impl.score.open():
            for score_id, score_def in impl.score.score_definitions.items():
                if score_def.hist_conf is not None:
                    hist_conf = score_def.hist_conf
                else:
                    hist_conf = build_default_histogram_conf(
                        score_def.value_type)
                if isinstance(hist_conf, NullHistogramConfig):
                    all_hist_confs[score_id] = hist_conf
                    continue

                if isinstance(hist_conf, CategoricalHistogramConfig):
                    all_hist_confs[score_id] = hist_conf
                    continue

                assert isinstance(hist_conf, NumberHistogramConfig)
                if not hist_conf.has_view_range():
                    all_min_max_scores.append(score_id)
                all_hist_confs[score_id] = hist_conf
        return all_min_max_scores, all_hist_confs

    @staticmethod
    def _get_reference_genome_cached(
        grr: GenomicResourceRepo | None, genome_id: str | None,
    ) -> ReferenceGenome | None:
        if genome_id is None or grr is None:
            return None
        if genome_id in GenomicScoreImplementation._REF_GENOME_CACHE:
            return cast(
                ReferenceGenome,
                GenomicScoreImplementation._REF_GENOME_CACHE[genome_id],
            )
        try:
            ref_genome = build_reference_genome_from_resource(
                grr.get_resource(genome_id),
            )
            logger.info(
                "Using reference genome label <%s> ",
                genome_id,
            )
        except FileNotFoundError:
            logger.warning(
                "Couldn't find reference genome %s",
                genome_id,
            )
            return None
        GenomicScoreImplementation._REF_GENOME_CACHE[genome_id] = ref_genome
        return ref_genome

    def _get_chrom_regions(
        self, region_size: int, grr: GenomicResourceRepo | None = None,
    ) -> list[Region]:

        regions = []
        ref_genome_id = cast(
            str,
            self.resource.get_labels().get("reference_genome"),
        )
        ref_genome = self._get_reference_genome_cached(grr, ref_genome_id)
        for chrom in self.score.get_all_chromosomes():
            # Resolved afresh for every contig: a contig whose length cannot be
            # determined must be skipped, never inherit the previous contig's.
            chrom_length: int | None = None
            if ref_genome is not None and chrom in ref_genome.chromosomes:
                chrom_length = ref_genome.get_chrom_length(chrom)
            else:
                if isinstance(self.score.table, InmemoryGenomicPositionTable):
                    # The in-memory backend yields record tuples; read the end
                    # position from its named slot rather than an adapter attr.
                    # A known-but-empty contig (e.g. a chrom_mapping onto a file
                    # contig with no data rows) yields no records at all: it has
                    # no maximum end position, so ``default=None`` hands it to
                    # the warn-and-skip below instead of raising out of max().
                    chrom_length = \
                        max((record[POS_END]
                             for record in
                             self.score.table.get_records_in_region(chrom)),
                            default=None)
                elif isinstance(self.score.table, BigWigTable):
                    chrom_length = \
                        self.score.table.get_chromosome_length(chrom)
                else:
                    assert isinstance(self.score.table,
                                      TabixGenomicPositionTable)
                    assert self.score.table.pysam_file is not None
                    fchrom = self.score.table.unmap_chromosome(chrom)
                    if fchrom is not None:
                        chrom_length = get_chromosome_length_tabix(
                            self.score.table.pysam_file, fchrom)
            if chrom_length is None:
                logger.warning(
                    "unable to find chromosome length for %s", chrom)
                continue

            regions.extend(
                split_into_regions(
                    chrom,
                    chrom_length,
                    region_size,
                ),
            )
        return regions

    @property
    def resource_id(self) -> str:
        return self.score.resource_id

    @staticmethod
    def _do_min_max(
        resource: GenomicResource,
        score_ids: list[str],
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> dict[str, MinMaxValue]:
        impl = build_score_implementation_from_resource(resource)
        result = {
            scr_id: MinMaxValue(scr_id)
            for scr_id in score_ids
        }
        with impl.score.open() as score:
            for _left, _right, rec in score.fetch_region_values(
                    chrom, start, end, score_ids):
                for score_index, score_id in enumerate(score_ids):
                    result[score_id].add_value(
                        rec[score_index],  # type: ignore
                    )
        return result

    @staticmethod
    def _merge_min_max(
        score_ids: list[str],
        all_hist_confs: dict[str, HistogramConfig],
        *calculate_tasks: dict[str, MinMaxValue],
    ) -> dict[str, HistogramConfig]:
        res: dict[str, MinMaxValue] = {}
        for score_id in score_ids:
            for min_max_region in calculate_tasks:
                if res.get(score_id) is None:
                    res[score_id] = min_max_region[score_id]
                else:
                    assert res[score_id] is not None
                    res[score_id].merge(
                        min_max_region[score_id])
        return GenomicScoreImplementation._update_hist_confs(
            all_hist_confs, res)

    @staticmethod
    def _update_hist_confs(
        all_hist_confs: dict[str, HistogramConfig],
        minmax_task: dict[str, MinMaxValue] | None,
    ) -> dict[str, HistogramConfig]:

        if minmax_task is None:
            return all_hist_confs

        for score_id, min_max in minmax_task.items():
            hist_conf = all_hist_confs[score_id]
            assert isinstance(hist_conf, NumberHistogramConfig)
            assert not hist_conf.has_view_range()
            if np.isnan(min_max.min) or np.isnan(min_max.max):
                logger.warning(
                    "min/max value for %s not found; "
                    "nullify the histogram", score_id)
                all_hist_confs[score_id] = NullHistogramConfig(
                    f"min/max for {score_id} not found")
            else:
                hist_conf.view_range = (min_max.min, min_max.max)
        logger.info("histogram configs updated: %s", all_hist_confs)
        return all_hist_confs

    @staticmethod
    def _do_histogram(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> dict[str, Histogram]:
        impl = build_score_implementation_from_resource(resource)
        result: dict[str, Histogram] = {}

        logger.info("updated hist confs: %s", all_hist_confs)

        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            result[score_id] = build_empty_histogram(hist_conf)

        score_ids = list(result.keys())
        with impl.score.open() as score:
            # One statement of the rule, read by this path and by the bulk
            # one: only a position score weighs a record by its span.
            weight_is_span = score.RECORD_WEIGHT_IS_SPAN
            for left, right, rec in score.fetch_region_values(
                    chrom, start, end, score_ids):
                weight = right - left + 1 if weight_is_span else 1
                for scr_index, scr_id in enumerate(score_ids):

                    try:
                        result[scr_id].add_value(
                            rec[scr_index],  # type: ignore
                            weight,
                        )
                    except TypeError as err:
                        logger.exception(
                            "Failed adding value %s to histogram of %s; "
                            "%s:%s-%s", rec[scr_index] if rec else None,
                            resource.resource_id,
                            chrom, start, end)
                        result[scr_id] = NullHistogram(
                            NullHistogramConfig(str(err)),
                        )
                    except HistogramError as err:
                        logger.warning(
                            "Histogram for %s nullified",
                            scr_id,
                        )
                        result[scr_id] = NullHistogram(
                            NullHistogramConfig(str(err)),
                        )
        return result

    _SCAN_BATCH_SIZE: ClassVar[int] = 100_000

    @staticmethod
    def _do_histogram_bulk(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str,
        start: int,
        end: int,
    ) -> dict[str, Histogram]:
        """Vectorized equivalent of :meth:`_do_histogram`.

        Reads a region as batches of column arrays -- tabix pulls raw pysam
        rows directly and bigWig converts each fetched interval chunk in one
        shot, neither building a ``Record`` per row -- and accumulates each
        score's histogram with :meth:`NumberHistogram.add_batch` rather than a
        per-record ``add_value``.  The clip, the weight, the overlap rule and
        the value coercion are identical to the per-record path (pinned by the
        bulk-vs-per-record tests, and by both paths reading one statement of
        the per-kind rules); the dispatch restricts this to float scores over
        tabix/bigWig tables -- everything else keeps :meth:`_do_histogram`.
        """
        result: dict[str, Histogram] = {}
        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            result[score_id] = build_empty_histogram(hist_conf)
        return GenomicScoreImplementation._bulk_region_scan(
            resource, result, chrom, start, end,
            GenomicScoreImplementation._accumulate_arrays)

    @staticmethod
    def _bulk_region_scan(
        resource: GenomicResource,
        result: dict[str, _AccT],
        chrom: str,
        start: int,
        end: int,
        accumulate: Callable[
            [tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]],
             dict[str, _AccT],
             tuple[str, int | None, int | None], int | None,
             GenomicScore],
            int | None],
    ) -> dict[str, _AccT]:
        """Drive a bulk region scan, folding each batch into ``result``.

        The shared skeleton of :meth:`_do_histogram_bulk` and
        :meth:`_do_min_max_bulk`: open the score and stream the region's
        column-array batches through ``accumulate`` (which mutates ``result``
        and carries the overlap guard's ``prev_right``).  The caller supplies
        the pre-built ``result`` -- empty histograms or seeded ``MinMaxValue``
        -- and the matching accumulator.  The opened score travels with each
        batch because it is what states this resource kind's record semantics
        (``RECORD_ORDERING``, ``RECORD_WEIGHT_IS_SPAN``) -- read here and by
        the per-record path from that one place.  Batches are keyed by SCORE
        ID: the
        score resolves each id to its payload column itself (gain#398), so
        nothing here handles column indices.
        """
        impl = build_score_implementation_from_resource(resource)
        with impl.score.open() as score:
            prev_right: int | None = None
            batches = score.fetch_region_value_arrays(
                chrom, start, end, list(result),
                batch_size=GenomicScoreImplementation._SCAN_BATCH_SIZE)
            for arrays in batches:
                prev_right = accumulate(
                    arrays, result, (chrom, start, end), prev_right, score)
        return result

    @staticmethod
    def _accumulate_arrays(
        arrays: tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]],
        result: dict[str, Histogram],
        region: tuple[str, int | None, int | None],
        prev_right: int | None,
        score: GenomicScore,
    ) -> int | None:
        """Fold one batch of column arrays into the per-score histograms.

        ``arrays`` is one ``(pos_begin, pos_end, {score_id: cells})`` batch as
        produced by :meth:`_region_value_arrays`.  Clips each record to
        ``[start, end]`` exactly as ``_fetch_region_lines`` does (dropping
        records ending before ``start``), weights it as ``score``'s kind
        weights it, enforces whatever overlap rule that kind states across the
        batch boundary, and adds each float score's values vectorized.
        Returns the last clipped right edge so the next batch can continue the
        overlap check.
        """
        pos_begin, pos_end, value_cells = arrays
        keep, weights, prev_right = \
            GenomicScoreImplementation._clip_keep_guard(
                pos_begin, pos_end, region, prev_right, score)

        for score_id, hist in result.items():
            values = value_cells[score_id]
            assert isinstance(hist, NumberHistogram)
            hist.add_batch(values[keep], weights)

        return prev_right

    @staticmethod
    def _clip_keep_guard(
        pos_begin: np.ndarray,
        pos_end: np.ndarray,
        region: tuple[str, int | None, int | None],
        prev_right: int | None,
        score: GenomicScore,
    ) -> tuple[np.ndarray, np.ndarray, int | None]:
        """Clip a batch to the region, per ``score``'s record semantics.

        Returns ``(keep, weights, prev_right)``: the mask of records surviving
        the ``pos_end >= start`` skip (as ``_fetch_region_lines`` drops records
        ending before the query, for EVERY resource kind), their weights, and
        the carry for the next batch's overlap check.

        Both the weight and the guard are read off the score class, which
        states them once for this path and for the per-record one:

        * ``RECORD_WEIGHT_IS_SPAN`` -- a position-score record counts once per
          base pair of the queried region it covers
          (``min(end, pos_end) - max(start, pos_begin) + 1``); an allele record
          and a fragment count 1, however wide they are.
        * ``RECORD_ORDERING`` -- ``DISJOINT`` raises ``ValueError`` on two
          records that touch, exactly as ``PositionScore.fetch_region_values``
          does, and across the batch boundary via ``prev_right``; ``SHARED``
          has nothing to reject, because several records at one position are
          what an allele or fragment score is made of.
        """
        chrom, start, end = region
        count = pos_begin.shape[0]
        left = pos_begin if start is None else np.maximum(pos_begin, start)
        right = pos_end if end is None else np.minimum(pos_end, end)
        keep = np.ones(count, dtype=bool) if start is None \
            else (pos_end >= start)

        kleft = left[keep]
        kright = right[keep]
        if score.RECORD_ORDERING is RecordOrdering.DISJOINT and kleft.size:
            overlaps_within = kleft.size > 1 and bool(
                np.any(kleft[1:] <= kright[:-1]))
            overlaps_carry = prev_right is not None \
                and int(kleft[0]) <= prev_right
            if overlaps_within or overlaps_carry:
                raise ValueError(
                    f"multiple values for positions on {chrom}")
            prev_right = int(kright[-1])
        weights = (kright - kleft + 1).astype(np.int64) \
            if score.RECORD_WEIGHT_IS_SPAN \
            else np.ones(kleft.size, dtype=np.int64)
        return keep, weights, prev_right

    @staticmethod
    def _can_bulk_histogram(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
    ) -> bool:
        """Whether the vectorized scan may serve this histogram build.

        :meth:`_bulk_scan_eligible` plus the one condition that is this
        caller's alone: every score must feed a NUMBER histogram.  A
        categorical or null histogram keeps the per-record
        :meth:`_do_histogram` -- ``add_batch`` is a number-histogram method,
        and a null histogram has nothing to accumulate.
        """
        number_score_ids = []
        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            if not isinstance(hist_conf, NumberHistogramConfig):
                return False
            number_score_ids.append(score_id)
        return GenomicScoreImplementation._bulk_scan_eligible(
            resource, number_score_ids)

    @staticmethod
    def _bulk_scan_eligible(
        resource: GenomicResource,
        score_ids: list[str],
    ) -> bool:
        """Whether a vectorized region scan may serve these float scores.

        The shared gate for the histogram and min/max bulk paths, and the place
        the conditions that are THIS caller's live -- as opposed to the one
        condition that is the backend's, which the score now answers itself:

        * a resource kind the bulk path is exercised against
          (:data:`_BULK_SCAN_RESOURCE_TYPES`): a position, allele or fragment
          score.  Its record semantics no longer have to be assumed -- the
          score class states them (``RECORD_ORDERING``,
          ``RECORD_WEIGHT_IS_SPAN``) and both scan paths read them from there
          -- so what this test now excludes is only ``np_score``, of which no
          production GRR has one;
        * every score a ``float``: ``int()`` / ``str()`` parsing is not the
          float parse the bulk path does;
        * and the backend serves the bulk read at all -- asked of the score,
          not tested on the table's class.  This is what keeps a VCF-backed
          allele score on the per-record path: its record payload is not a raw
          row, so its table declares no column-array support.

        Answered WITHOUT opening the score: the table and the score definitions
        are both built in ``GenomicScore.__init__``, so nothing here needs a
        file handle.
        """
        if resource.get_type() not in _BULK_SCAN_RESOURCE_TYPES:
            return False
        score = build_score_implementation_from_resource(resource).score
        return score.supports_region_value_arrays(score_ids)

    @staticmethod
    def _do_min_max_task(
        resource: GenomicResource,
        score_ids: list[str],
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> dict[str, MinMaxValue]:
        """Compute a region's min/max, bulk-vectorized where eligible.

        Mirrors :meth:`_do_histogram_task`: the bulk path needs a bounded
        region -- a concrete contig for its overlap guard, and concrete bounds
        because that is what the score's bulk read takes -- so any unbounded
        scan keeps the per-record :meth:`_do_min_max`.
        """
        if chrom is not None and start is not None and end is not None \
                and GenomicScoreImplementation._bulk_scan_eligible(
                    resource, score_ids):
            return GenomicScoreImplementation._do_min_max_bulk(
                resource, score_ids, chrom, start, end)
        return GenomicScoreImplementation._do_min_max(
            resource, score_ids, chrom, start, end)

    @staticmethod
    def _do_histogram_task(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> dict[str, Histogram]:
        """Compute a region's histograms, bulk-vectorized where eligible.

        The bulk path needs a bounded region: a concrete contig, because its
        overlap guard runs along a single chromosome's records, and concrete
        bounds, because that is what the score's bulk read takes.  Any
        unbounded scan keeps the per-record path.
        """
        if chrom is not None and start is not None and end is not None \
                and GenomicScoreImplementation._can_bulk_histogram(
                    resource, all_hist_confs):
            return GenomicScoreImplementation._do_histogram_bulk(
                resource, all_hist_confs, chrom, start, end)
        return GenomicScoreImplementation._do_histogram(
            resource, all_hist_confs, chrom, start, end)

    @staticmethod
    def _do_min_max_bulk(
        resource: GenomicResource,
        score_ids: list[str],
        chrom: str,
        start: int,
        end: int,
    ) -> dict[str, MinMaxValue]:
        """Vectorized equivalent of :meth:`_do_min_max`.

        Reads the region as column-array batches of already-parsed values
        (the same producer the histogram bulk path uses) and reduces each score
        with ``min()``/``max()`` over the batch's non-nan subset, rather than a
        per-record ``MinMaxValue.add_value``.  The parse, the region clip/skip,
        the overlap rule and the record count are identical to the per-record
        path -- both read the same per-kind facts off the score class.
        """
        result: dict[str, MinMaxValue] = {
            score_id: MinMaxValue(score_id) for score_id in score_ids}
        return GenomicScoreImplementation._bulk_region_scan(
            resource, result, chrom, start, end,
            GenomicScoreImplementation._accumulate_min_max)

    @staticmethod
    def _accumulate_min_max(
        arrays: tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]],
        result: dict[str, MinMaxValue],
        region: tuple[str, int | None, int | None],
        prev_right: int | None,
        score: GenomicScore,
    ) -> int | None:
        """Fold one batch of column arrays into the per-score min/max.

        Shares the clip/skip and the per-kind overlap rule with the histogram
        path; the reduction takes ``min()``/``max()`` over the kept values
        with the nans dropped first -- an empty remainder contributes nothing --
        folded into the running ``MinMaxValue`` exactly as ``add_value`` seeds
        and combines them.
        """
        pos_begin, pos_end, value_cells = arrays
        keep, _weights, prev_right = \
            GenomicScoreImplementation._clip_keep_guard(
                pos_begin, pos_end, region, prev_right, score)

        for score_id, min_max in result.items():
            values = value_cells[score_id][keep]
            finite = values[~np.isnan(values)]
            if finite.size:
                low = float(finite.min())
                high = float(finite.max())
                min_max.min = low if np.isnan(min_max.min) \
                    else min(min_max.min, low)
                min_max.max = high if np.isnan(min_max.max) \
                    else max(min_max.max, high)
        return prev_right

    @staticmethod
    def _merge_histograms(
        resource: GenomicResource,  # noqa: ARG004
        *calculated_histograms: dict[str, Any],
    ) -> dict[str, Histogram]:
        """Fold each region's histograms into one histogram per score.

        A score that cannot be histogrammed is nullified on its own, exactly
        as the per-region accumulation path nullifies its own overflow: the
        merge of a resource's scores is per score, so one un-histogrammable
        score must not cost the rest of the resource its histograms
        (gain#465).  A categorical score can stay within
        ``UNIQUE_VALUES_LIMIT`` in every single region and exceed it only in
        their union, so the merge is the first place its failure appears.
        """
        result: dict[str, Histogram] = {}

        for histogram_region in calculated_histograms:
            for score_id, hist in histogram_region.items():
                if result.get(score_id) is None:
                    # The accumulator aliases the first region's histogram
                    # and later regions are merged into it in place, so that
                    # region's dict ends up holding the merged -- or, when
                    # the merge raises, the partially merged -- object.
                    # Safe as written: the per-region dicts are dependency
                    # task results, handed to this merge once and never read
                    # again, and under a distributed executor they arrive
                    # deserialized, so nothing observes the mutation.
                    result[score_id] = hist
                    continue
                if isinstance(result[score_id], NullHistogram):
                    continue
                if isinstance(hist, NullHistogram):
                    result[score_id] = NullHistogram(NullHistogramConfig(
                        f"Empty histogram for {score_id} in a region: "
                        f"{hist.reason}"))
                else:
                    try:
                        result[score_id].merge(hist)
                    except HistogramError as err:
                        logger.warning(
                            "Histogram for %s nullified while merging "
                            "regions: %s", score_id, err)
                        result[score_id] = NullHistogram(
                            NullHistogramConfig(str(err)),
                        )

        return result

    @staticmethod
    def _save_histograms(
        resource: GenomicResource, merged_histograms: dict[str, Histogram],
    ) -> dict[str, Histogram]:
        impl = build_score_implementation_from_resource(resource)
        GenomicScoreImplementation._save_and_plot_histograms(
            resource, impl.score, merged_histograms)
        return merged_histograms

    @staticmethod
    def _merge_and_save_histograms(
        resource: GenomicResource,
        *calculated_histograms: dict[str, Any],
    ) -> dict[str, Histogram]:
        merged_histograms = GenomicScoreImplementation._merge_histograms(
            resource, *calculated_histograms)
        return GenomicScoreImplementation._save_histograms(
            resource, merged_histograms)

    def calc_info_hash(self) -> bytes:
        """Compute and return the info hash."""
        return b"infohash"

    def calc_statistics_hash(self) -> bytes:
        """
        Compute the statistics hash.

        This hash is used to decide whether the resource statistics should be
        recomputed.
        """
        manifest = self.resource.get_manifest()
        return json.dumps({
            "config": {
                "histograms": [
                    hist_conf.to_dict()
                    for hist_conf in self.get_config_histograms().values()
                    if hist_conf is not None
                ],
                "table": {
                    "config": self.score.table.definition,
                    "files_md5": {file_name: manifest[file_name].md5
                                  for file_name in sorted(self.files)},
                },
            },
            "score_config": [
                {
                    "id": score_def.score_id,
                    "type": score_def.value_type,
                    "name": score_def.col_name,
                    "index": score_def.col_index,
                    "na_values": str(sorted(
                        str(na) for na in score_def.na_values))
                    if score_def.na_values is not None else "",
                }
                for score_def in self.score.score_definitions.values()],
        }, indent=2).encode()


class FragmentScoreImplementation(GenomicScoreImplementation):
    """Assists in the management of a fragment score resource.

    Carries no statistics behaviour of its own.  It used to override the
    per-record histogram add, pinning a fragment's weight to 1 -- an
    independent second statement of a rule the bulk scan had to restate for
    itself, and could only restate by assuming position-score semantics.  It
    is now declared once on ``FragmentScore`` (``RECORD_WEIGHT_IS_SPAN``) and
    read by both scan paths (gain#421).

    Its sibling override added a record count to the min/max statistic; that
    count had no consumer anywhere in the stack and no deployed GRR ever
    carried it, so gain#421 removed it outright rather than teaching a second
    path to reproduce it.
    """
    # pylint: disable=useless-parent-delegation

    def create_statistics_build_tasks(
        self, **kwargs: Any,
    ) -> list[TaskDesc]:
        return super().create_statistics_build_tasks(**kwargs)

    def calc_info_hash(self) -> bytes:
        return super().calc_info_hash()

    def calc_statistics_hash(self) -> bytes:
        return super().calc_statistics_hash()

    def get_info(self, **kwargs: Any) -> str:
        return super().get_info(**kwargs)

    def get_statistics_info(self, **kwargs: Any) -> str:
        return super().get_statistics_info(**kwargs)


def build_score_implementation_from_resource(
    resource: GenomicResource,
) -> GenomicScoreImplementation | FragmentScoreImplementation:
    """Builds score implementation based on resource type"""
    if resource.get_type() in FRAGMENT_SCORE_TYPES:
        return FragmentScoreImplementation(resource)
    return GenomicScoreImplementation(resource)
