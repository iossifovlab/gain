from __future__ import annotations

import itertools
import json
from collections.abc import Generator, Iterable
from typing import Any, ClassVar, cast

import numpy as np
import pandas as pd

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    TabixGenomicPositionTable,
    VCFGenomicPositionTable,
)
from gain.genomic_resources.genomic_position_table.record import (
    PAYLOAD,
    POS_BEGIN,
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
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
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
        impl = build_score_implementation_from_resource(resource)
        all_min_max_scores, all_hist_confs = \
            impl._unpack_score_defs(resource)  # noqa: SLF001

        if all_min_max_scores:
            min_max_result = GenomicScoreImplementation._do_min_max(
                resource,
                all_min_max_scores,
                None,
                None,
                None,
            )
            all_hist_confs = \
                GenomicScoreImplementation._update_hist_confs(
                    all_hist_confs, min_max_result)
        hist_result = GenomicScoreImplementation._do_histogram(
            resource,
            all_hist_confs,
            None,
            None,
            None,
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
                        GenomicScoreImplementation._do_min_max,
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
        files = set()
        files.add(self.score.table.definition.filename)
        if isinstance(self.score.table, TabixGenomicPositionTable):
            files.add(f"{self.score.table.definition.filename}.tbi")
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

        if region_size <= 0:
            # Forcefully setting the chromosome to None is a bit hacky,
            # but is more elegant than properly supporting it in Region.
            return [Region(None, None, None)]  # type: ignore

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

    def _min_max_add_value(
        self, statistic: MinMaxValue,
        value: float,
    ) -> None:
        statistic.add_value(value)

    @staticmethod
    def _do_min_max(
        resource: GenomicResource,
        score_ids: list[str],
        chrom: str | None,
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
                    impl._min_max_add_value(  # noqa: SLF001
                        result[score_id],
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

    def _histogram_add_value(
        self, histogram: Histogram,
        value: Any,
        count: int,
    ) -> None:
        histogram.add_value(
            value,
            count,
        )

    @staticmethod
    def _do_histogram(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str | None,
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
            for left, right, rec in score.fetch_region_values(
                    chrom, start, end, score_ids):
                for scr_index, scr_id in enumerate(score_ids):

                    try:
                        impl._histogram_add_value(  # noqa: SLF001
                            result[scr_id],
                            rec[scr_index],  # type: ignore
                            right - left + 1,
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
        chrom: str | None,
        start: int | None,
        end: int | None,
    ) -> dict[str, Histogram]:
        """Vectorized equivalent of :meth:`_do_histogram`.

        Reads a region as batches of column arrays -- the tabix fast path
        pulls raw pysam rows directly (no ``Record`` built per row), bigWig
        goes through ``get_records_in_region`` -- and accumulates each score's
        histogram with :meth:`NumberHistogram.add_batch` rather than a
        per-record ``add_value``.  The clip/weight, overlap guard and value
        coercion are identical to the per-record path (pinned by the
        bulk-vs-per-record tests); the dispatch restricts this to float scores
        over tabix/bigWig tables -- everything else keeps :meth:`_do_histogram`.
        """
        result: dict[str, Histogram] = {}
        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            result[score_id] = build_empty_histogram(hist_conf)

        impl = build_score_implementation_from_resource(resource)
        with impl.score.open() as score:
            defs = {
                score_id: score.score_definitions[score_id]
                for score_id in result
            }
            # A bulk-eligible score addresses its column by integer index
            # (str keys are VCF INFO names, which the dispatch excludes).
            value_columns = {
                cast(int, defs[score_id].score_index) for score_id in result}
            prev_right: int | None = None
            for arrays in GenomicScoreImplementation._region_value_arrays(
                    score.table, chrom, start, end, value_columns):
                prev_right = GenomicScoreImplementation._accumulate_arrays(
                    arrays, result, defs, (chrom, start, end), prev_right)
        del impl
        return result

    @staticmethod
    def _region_value_arrays(
        table: Any,
        chrom: str | None,
        start: int | None,
        end: int | None,
        value_columns: Iterable[int],
    ) -> Generator[
            tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]], None, None]:
        """Yield ``(pos_begin, pos_end, {col: raw_cells})`` batches.

        Tabix and bigWig each take their ``get_region_value_arrays`` fast path,
        which never builds a ``Record``; any other backend goes through
        ``get_records_in_region`` and is unpacked into the same array shape
        here, so the accumulator does not care which backend produced them.
        """
        batch_size = GenomicScoreImplementation._SCAN_BATCH_SIZE
        columns = list(value_columns)
        has_array_fastpath = isinstance(table, BigWigTable) or (
            isinstance(table, TabixGenomicPositionTable)
            and not isinstance(table, VCFGenomicPositionTable))
        if has_array_fastpath \
                and chrom is not None and start is not None and end is not None:
            yield from table.get_region_value_arrays(
                chrom, start, end, columns, batch_size)
            return

        records = table.get_records_in_region(chrom, start, end)
        while True:
            batch = list(itertools.islice(records, batch_size))
            if not batch:
                return
            count = len(batch)
            pos_begin = np.fromiter(
                (rec[POS_BEGIN] for rec in batch),
                dtype=np.int64, count=count)
            pos_end = np.fromiter(
                (rec[POS_END] for rec in batch), dtype=np.int64, count=count)
            cols = {
                col: np.array(
                    [rec[PAYLOAD][col] for rec in batch], dtype=object)
                for col in columns
            }
            yield pos_begin, pos_end, cols

    @staticmethod
    def _accumulate_arrays(
        arrays: tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]],
        result: dict[str, Histogram],
        defs: dict[str, Any],
        region: tuple[str | None, int | None, int | None],
        prev_right: int | None,
    ) -> int | None:
        """Fold one batch of column arrays into the per-score histograms.

        ``arrays`` is one ``(pos_begin, pos_end, {col: raw_cells})`` batch as
        produced by :meth:`_region_value_arrays`.  Clips each record to
        ``[start, end]`` exactly as ``_fetch_region_lines`` does (drop records
        ending before ``start``;
        ``weight = min(end, pos_end) - max(start, pos_begin) + 1``), enforces
        the same overlapping-position guard across the batch boundary, and
        adds each float score's values vectorized.  Returns the last clipped
        right edge so the next batch can continue the overlap check.
        """
        pos_begin, pos_end, value_cells = arrays
        chrom, start, end = region
        count = pos_begin.shape[0]

        left = pos_begin if start is None else np.maximum(pos_begin, start)
        right = pos_end if end is None else np.minimum(pos_end, end)
        # ``_fetch_region_lines`` skips a record that ends before the query.
        keep = np.ones(count, dtype=bool) if start is None \
            else (pos_end >= start)

        kleft = left[keep]
        kright = right[keep]
        if kleft.size:
            overlaps_within = kleft.size > 1 and bool(
                np.any(kleft[1:] <= kright[:-1]))
            overlaps_carry = prev_right is not None \
                and int(kleft[0]) <= prev_right
            if overlaps_within or overlaps_carry:
                raise ValueError(
                    f"multiple values for positions on {chrom}")
            prev_right = int(kright[-1])
        weights = (kright - kleft + 1).astype(np.int64)

        for score_id, hist in result.items():
            score_def = defs[score_id]
            raw = pd.Series(value_cells[score_def.score_index])
            na_mask = raw.isin(score_def.na_values).to_numpy()
            # NA is tested on the raw value BEFORE parse (matching
            # ``_extract_value``); an unparseable value coerces to nan, which
            # ``add_batch`` skips exactly as ``add_value`` skips a None.
            values = pd.to_numeric(raw, errors="coerce").to_numpy(
                dtype=np.float64, copy=True)
            # ``pd.to_numeric`` is stricter than Python ``float()`` -- the
            # per-record parser -- so it drops tokens ``float()`` accepts
            # (PEP-515 underscores, Unicode digits).  Re-parse just the non-NA
            # cells it turned to nan with ``float()`` so the two agree exactly;
            # clean numeric data leaves this set empty and pays nothing.
            retry = np.flatnonzero(np.isnan(values) & ~na_mask)
            if retry.size:
                raw_cells = raw.to_numpy()
                for idx in retry:
                    try:
                        values[idx] = float(raw_cells[idx])
                    except (TypeError, ValueError):
                        values[idx] = np.nan
            values[na_mask] = np.nan
            assert isinstance(hist, NumberHistogram)
            hist.add_batch(values[keep], weights)

        return prev_right

    @staticmethod
    def _can_bulk_histogram(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
    ) -> bool:
        """Whether the vectorized scan may serve this histogram build.

        Restricted to the common fast case whose bit-exactness the bulk path
        guarantees: a **position score** whose float columns feed a number
        histogram, over a tabix or bigWig table.  The bulk path imposes
        position-score semantics -- a span weight ``pos_end - pos_begin + 1``
        and the one-value-per-position overlap guard -- so it must NOT serve
        the score types that read differently: an ``allele_score`` or
        ``np_score`` carries several weight-1 records (distinct ref/alt) at a
        single position, which the overlap guard would reject; a
        ``cnv_collection`` weights every record 1.  A VCF-backed table (its
        record payload is not a raw row), an int/str/bool score
        (``int()``/``str()`` parsing does not match ``pd.to_numeric``), or a
        categorical/null histogram likewise keep the per-record
        :meth:`_do_histogram`.
        """
        if resource.get_type() != "position_score":
            return False
        impl = build_score_implementation_from_resource(resource)
        with impl.score.open() as score:
            table = score.table
            if isinstance(table, VCFGenomicPositionTable):
                return False
            if not isinstance(
                    table, (TabixGenomicPositionTable, BigWigTable)):
                return False
            for score_id, hist_conf in all_hist_confs.items():
                if isinstance(hist_conf, NullHistogramConfig):
                    continue
                if not isinstance(hist_conf, NumberHistogramConfig):
                    return False
                score_def = score.score_definitions.get(score_id)
                if score_def is None or score_def.value_type != "float":
                    return False
        return True

    @staticmethod
    def _do_histogram_task(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str | None,
        start: int | None,
        end: int | None,
    ) -> dict[str, Histogram]:
        """Compute a region's histograms, bulk-vectorized where eligible.

        The bulk path needs a concrete contig (its overlap guard runs along a
        single chromosome's records); a ``chrom is None`` whole-table scan
        keeps the per-record path.
        """
        if chrom is not None and \
                GenomicScoreImplementation._can_bulk_histogram(
                    resource, all_hist_confs):
            return GenomicScoreImplementation._do_histogram_bulk(
                resource, all_hist_confs, chrom, start, end)
        return GenomicScoreImplementation._do_histogram(
            resource, all_hist_confs, chrom, start, end)

    @staticmethod
    def _merge_histograms(
        resource: GenomicResource,  # noqa: ARG004
        *calculated_histograms: dict[str, Any],
    ) -> dict[str, Histogram]:
        result: dict[str, Histogram] = {}

        for histogram_region in calculated_histograms:
            for score_id, hist in histogram_region.items():
                if result.get(score_id) is None:
                    result[score_id] = hist
                    continue
                if isinstance(result[score_id], NullHistogram):
                    continue
                if isinstance(hist, NullHistogram):
                    result[score_id] = NullHistogram(NullHistogramConfig(
                        f"Empty histogram for {score_id} in a region: "
                        f"{hist.reason}"))
                else:
                    result[score_id].merge(hist)

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


class CnvCollectionImplementation(GenomicScoreImplementation):
    """Assists in the management of resource of type cnv_collection."""
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

    def _histogram_add_value(
        self, histogram: Histogram,
        value: Any,
        count: int,  # noqa: ARG002
    ) -> None:
        histogram.add_value(
            value,
            1,
        )

    def _min_max_add_value(
        self, statistic: MinMaxValue,
        value: Any,
    ) -> None:
        statistic.add_value(value)
        statistic.add_count()


def build_score_implementation_from_resource(
    resource: GenomicResource,
) -> GenomicScoreImplementation | CnvCollectionImplementation:
    """Builds score implementation based on resource type"""
    if resource.get_type() == "cnv_collection":
        return CnvCollectionImplementation(resource)
    return GenomicScoreImplementation(resource)
