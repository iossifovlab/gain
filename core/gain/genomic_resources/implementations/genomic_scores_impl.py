from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterable
from typing import Any, ClassVar, NamedTuple, TypeVar, cast

import numpy as np

from gain import logging
from gain.genomic_resources.cli_errors import report_resource_failure
from gain.genomic_resources.genomic_position_table import (
    ContigExtent,
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    clip_span,
)
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
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
from gain.genomic_resources.resource_errors import (
    MalformedResourceError,
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
)
from gain.genomic_resources.resource_types import (
    FRAGMENT_SCORE_TYPES,
    equivalent_resource_types,
)
from gain.genomic_resources.score_def import ScoreValue
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_STATISTICS_FILE,
    CoverageStatistics,
    RegionCoverage,
    normalize_values,
)
from gain.genomic_resources.statistics.min_max import MinMaxValue
from gain.task_graph.graph import Task, TaskDesc, TaskGraph
from gain.utils.regions import (
    Region,
    split_into_regions,
)

logger = logging.getLogger(__name__)

# The per-batch accumulator target of a bulk region scan -- a Histogram for the
# histogram pass, a MinMaxValue for the min/max pass.  A TypeVar keeps
# ``_bulk_region_scan`` generic without losing either caller's dict type.
_AccT = TypeVar("_AccT")

# The resource kinds whose statistics the vectorized scan may serve, in every
# spelling of each.  Expanded through ``equivalent_resource_types`` rather than
# written out: a fragment score has TWO accepted type strings
# (``fragment_score`` and the deprecated ``cnv_collection``, gain#471), and
# a literal set
# naming only one of them would send the other silently back to the per-record
# path -- no error, no failing test, just the slow path forever.  ``np_score``
# is deliberately absent: no production GRR has one, so the bulk path is not
# exercised against it (gain#421).
_BULK_SCAN_RESOURCE_TYPES = frozenset(
    spelling
    for resource_type in ("position_score", "allele_score", "fragment_score")
    for spelling in equivalent_resource_types(resource_type)
)

# The kinds whose rows have a span coverage can union: position scores and
# fragment scores (in both spellings).  Allele scores are deliberately out:
# their rows collapse to points and their coverage is a DISTINCT-position
# count with its own slice (gain#777), not this union.
_COVERAGE_SCAN_RESOURCE_TYPES = frozenset(
    spelling
    for resource_type in ("position_score", "fragment_score")
    for spelling in equivalent_resource_types(resource_type)
)


class RegionScanResult(NamedTuple):
    """What one region's statistics task hands to the merge step."""

    histograms: dict[str, Histogram]
    coverage: RegionCoverage | None


class CoverageRow(NamedTuple):
    """One chromosome's rendered coverage: raw count plus optional fraction.

    ``fraction`` is ``None`` when no denominator resolved for this
    chromosome -- the row renders its raw count only.
    """

    chrom: str
    covered: int
    fraction: float | None


class CoverageDisplay(NamedTuple):
    """The Coverage section's render payload, fractions resolved.

    Raw counts come from the stored statistic; fractions are computed at
    render time and never stored.  ``global_fraction`` is ``None`` unless
    every chromosome resolved a length -- a global percent over a partial
    denominator would be misleading.
    """

    rows: list[CoverageRow]
    global_fraction: float | None

    @property
    def global_covered(self) -> int:
        return sum(row.covered for row in self.rows)

    @property
    def has_fractions(self) -> bool:
        return any(row.fraction is not None for row in self.rows)


class GenomicScoreImplementation(ScoreImplementationBase):
    # pylint: disable=too-many-public-methods
    """Genomic scores base class."""

    def __init__(self, resource: GenomicResource):
        super().__init__(resource)
        self.score: GenomicScore = build_score_from_resource(resource)
        self._render_repo: GenomicResourceRepo | None = None

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

    def get_info(self, **kwargs: Any) -> str:
        self._render_repo = kwargs.get("repo")
        try:
            return InfoImplementationMixin.get_info(self)
        finally:
            self._render_repo = None

    def get_statistics_info(self, **kwargs: Any) -> str:
        self._render_repo = kwargs.get("repo")
        try:
            return InfoImplementationMixin.get_statistics_info(self)
        finally:
            self._render_repo = None

    def get_coverage_statistics(self) -> CoverageStatistics | None:
        """The resource's coverage statistics, or ``None`` if not built.

        Absence is an expected state, not an error: statistics roll out
        lazily as resources are rebuilt (``calc_statistics_hash`` does
        not know about this file), so a resource built before the
        statistic existed simply has nothing to show yet.
        """
        try:
            content = self.resource.get_file_content(
                COVERAGE_STATISTICS_FILE)
        except FileNotFoundError:
            return None
        return CoverageStatistics.deserialize(content)

    def get_coverage_display(self) -> CoverageDisplay | None:
        """The Coverage section's payload: raw counts plus fractions.

        Fractions are computed here, at render time, against a resolved
        denominator; the stored statistic stays raw counts (see
        :class:`CoverageStatistics`).  ``None`` when the statistic is not
        built.  Any failure to resolve a denominator degrades to
        raw-counts-only rendering, never a page-build failure.

        The genome rung of the denominator ladder resolves against the
        repository handed to the enclosing :meth:`get_info` /
        :meth:`get_statistics_info` call; invoked outside a page build,
        no repository is available and that rung resolves nothing.
        """
        coverage = self.get_coverage_statistics()
        if coverage is None:
            return None
        covered = coverage.covered_by_chromosome()
        lengths = self._resolve_chrom_lengths(covered)
        for chrom, length in list(lengths.items()):
            if length <= 0 or covered[chrom] > length:
                # A denominator smaller than what it must bound proves the
                # resolved source wrong for this contig (a zero-length .fai
                # record, a mislabeled genome).  Degrade the row to raw
                # counts rather than render a zero-division or a >100%.
                logger.warning(
                    "implausible length %s for contig %s of %s "
                    "(covered positions: %s); rendering raw counts for it",
                    length, chrom, self.resource.resource_id, covered[chrom])
                del lengths[chrom]
        rows = [
            CoverageRow(
                chrom,
                count,
                count / lengths[chrom] if chrom in lengths else None,
            )
            for chrom, count in covered.items()
        ]
        if lengths and len(lengths) == len(covered):
            global_fraction: float | None = (
                coverage.covered_global() / sum(lengths.values()))
        else:
            global_fraction = None
        return CoverageDisplay(rows, global_fraction)

    def _resolve_chrom_lengths(
        self, chroms: Iterable[str],
    ) -> dict[str, int]:
        """Resolve chromosome lengths for the render-time denominator.

        The ladder: the ``reference_genome`` label's genome resource,
        falling back to the bigWig header's chromosome sizes for a
        bigWig-backed score, or raw counts (an empty mapping) when
        nothing resolves.  A chromosome absent from the resolved source
        is simply absent from the result.
        """
        genome_id = cast(
            str | None,
            self.resource.get_labels().get("reference_genome"),
        )
        try:
            ref_genome = self._get_reference_genome_cached(
                self._render_repo, genome_id)
        except ValueError:
            # The label names a resource that is not a genome.  At render
            # time that is a reason to degrade, not to fail the page build.
            logger.warning(
                "reference_genome label %r of %s does not name a genome "
                "resource; ignoring it for coverage fractions",
                genome_id, self.resource.resource_id)
            ref_genome = None
        if ref_genome is not None:
            all_lengths = ref_genome.get_all_chrom_lengths()
            return {
                chrom: all_lengths[chrom]
                for chrom in chroms
                if chrom in all_lengths
            }
        if self.score.table.chrom_lengths_are_exact:
            return self._table_exact_lengths(chroms)
        logger.info(
            "no coverage denominator resolvable for %s; "
            "rendering raw counts only", self.resource.resource_id)
        return {}

    def _table_exact_lengths(
        self, chroms: Iterable[str],
    ) -> dict[str, int]:
        """Contig lengths from a backend that declares them exact.

        Only consulted when the table's ``chrom_lengths_are_exact``
        capability holds (the bigWig header; mapping-aware).  Opens the
        score if it is closed, and closes it again only in that case --
        an already-open score stays open for its owner.
        """
        opened_here = not self.score.is_open()
        if opened_here:
            self.score.open()
        try:
            lengths: dict[str, int] = {}
            for chrom in chroms:
                try:
                    length = self.score.table.find_chromosome_length(chrom)
                except ValueError:
                    # The backend raises ValueError both for a contig it
                    # does not list and for a closed table; the open()
                    # above rules the latter out, so this is the
                    # unknown-contig case.
                    logger.warning(
                        "contig %s has no exact table length in %s; "
                        "rendering raw counts for it",
                        chrom, self.resource.resource_id)
                    continue
                if isinstance(length, int):
                    lengths[chrom] = length
            return lengths
        finally:
            if opened_here:
                self.score.close()

    @staticmethod
    def _do_noregion_histograms(
        resource: GenomicResource,
    ) -> None:
        """Compute a resource's statistics in one task, contig by contig.

        The ``--region-size 0`` path: one task, no splitting *within* a
        contig.  The region-read family requires a contig, so the iteration
        over contigs is stated here rather than smuggled down as a null.

        Each contig is scanned through the very task functions a split run
        calls and folded together by the very functions that fold those
        tasks' results, so this produces what a split run produces -- not
        something merely believed to match, and a resource either of them
        refuses is attributed there rather than reaching the reader as a
        reason-less "statistics were not built".  An empty contig (an
        unscored alt, of which hg38 has hundreds) contributes an empty
        histogram, which merges cleanly; ``_merge_histograms`` only
        nullifies on a genuine error.
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
                *(GenomicScoreImplementation._do_min_max_task(
                    resource, all_min_max_scores, chrom, None, None)
                  for chrom in chroms),
            )
        GenomicScoreImplementation._merge_and_save_histograms(
            resource,
            *(GenomicScoreImplementation._do_histogram_task(
                resource, all_hist_confs, chrom, None, None)
              for chrom in chroms),
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
            index_filename = self._resolve_index_filename(filename)
            if index_filename is not None:
                files.add(index_filename)
        return files

    def _resolve_index_filename(self, filename: str) -> str | None:
        """Return the tabix index of ``filename``, or ``None`` with a warning.

        Resolves the index the way the table itself opens it: the table
        definition's ``index_filename`` when it is set, and otherwise the
        conventional ``.tbi`` / ``.csi`` probe over the resource manifest.

        Only a name the manifest carries is ever returned -- the statistics
        hash looks every file-set entry up in that same manifest and would
        raise on a missing key.  A configured index absent from the
        manifest is a misconfiguration; it is reported and dropped rather
        than falling back to the conventional probe, which would silently
        hash an index the table does not read (gain#595).
        """
        manifest = self.resource.get_manifest()
        # The definition is a Box over untyped config, so ``get`` is Any.
        configured = cast(
            "str | None",
            self.score.table.definition.get("index_filename"))
        if configured is not None:
            if configured in manifest:
                return configured
            logger.warning(
                "resource <%s>: tabix table %s configures index_filename "
                "%s, which is not in the resource manifest; the index is "
                "left out of the resource file set",
                self.resource.resource_id, filename, configured)
            return None
        # The statistics hash is computed against the same manifest, so
        # resolving from it here is free and keeps the two consistent.
        index_filename = resolve_tabix_index_filename(manifest, filename)
        if index_filename is None:
            logger.warning(
                "resource <%s>: tabix table %s has no index "
                "(neither %s.tbi nor %s.csi) in the resource manifest; "
                "the index is left out of the resource file set",
                self.resource.resource_id, filename, filename, filename)
        return index_filename

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
            # Resolved afresh for every contig, never inherited from the
            # previous one.  Two different things can leave it unset, and they
            # get opposite treatment below: a contig PROVEN to hold no records
            # is skipped, a contig whose length merely could not be DETERMINED
            # is scanned whole.
            chrom_length: int | ContigExtent
            if ref_genome is not None and chrom in ref_genome.chromosomes:
                chrom_length = ref_genome.get_chrom_length(chrom)
            else:
                # Asked of the table itself, which is the only thing that knows
                # how its format answers.  This used to be an isinstance ladder
                # over the concrete backends, reaching past the abstraction into
                # a pysam handle and the tabix probe to re-derive per backend
                # what each already implements -- so a new backend could not be
                # added without editing it, and the else-branch turned that
                # omission into an assertion failure (gain#509).
                #
                # The step is left at the table's own default.  The ladder used
                # to seed the tabix probe at ITS default (half the table's),
                # which sounds like a behaviour change and measurably is not:
                # the probe brackets the length on the geometric ladder
                # {step * 2^k}, and 50M and 100M generate the same ladder, so
                # both seeds find the same bracket and the same bound.
                chrom_length = self.score.table.find_chromosome_length(chrom)
                if chrom_length is ContigExtent.EMPTY:
                    # PROVEN to hold no records -- only a backend holding the
                    # whole file can say this (e.g. a chrom_mapping onto a file
                    # contig with no data rows).  There is nothing to scan and
                    # nothing to validate, and an unbounded region here would
                    # cost a table open per empty contig -- hundreds of them for
                    # a mapping that covers hg38's alts.  INFO, not WARNING:
                    # there is nothing for an operator to fix.
                    logger.info(
                        "contig %s holds no records; not scanned", chrom)
                    continue
            if chrom_length is ContigExtent.UNDETERMINED:
                # The length could not be determined for a contig that may well
                # hold records -- skipping it would leave them out of the
                # statistics AND out of the ordering checks the scan performs on
                # the way, while the resource still reported its statistics as
                # freshly built.  A length is what SPLITTING needs, not what
                # READING needs, so scan the contig whole.  An unbounded region
                # keeps the per-record path (see :meth:`_do_histogram_task`):
                # slower than a split contig, never wrong.
                logger.warning(
                    "unable to find chromosome length for %s; "
                    "scanning it as a single unbounded region", chrom)
                regions.append(Region(chrom))
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
    def _scan_region(
        score: GenomicScore,
        chrom: str,
        start: int | None,
        end: int | None,
        score_ids: list[str],
    ) -> Generator[
            tuple[int, int, list[ScoreValue]], None, None]:
        """Read a region the way the statistics scan reads it.

        Every per-record pass reads here, so validation is composed in the
        open -- one visible extra link over the record stream -- rather than
        each pass being trusted to remember it (ADR 0008).  What the pass
        then sees is exactly what a reader sees: the same transform, over the
        same records, in the same order.

        One read.  ``validate_records`` is a transducer over the very stream
        the transform consumes, not a second pass over the region.
        """
        records = score.validate_records(
            score.fetch_records(chrom, start, end))
        yield from score.region_values_from_records(
            records, chrom, start, end, score_ids)

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
            for _left, _right, rec in GenomicScoreImplementation._scan_region(
                    score, chrom, start, end, score_ids):
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
        coverage: RegionCoverage | None = None,
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
            # Only the span-derived weight reads the window: a count-kind
            # record counts once wherever the point it collapses to falls
            # (see test_multi_base_allele_record_clipped_by_region_weighs_one).
            for left, right, rec in GenomicScoreImplementation._scan_region(
                    score, chrom, start, end, score_ids):
                # Coverage measures every kind from the same clip the
                # span weight reads, so the two paths agree on the one
                # edge the shared clip leaves open: a record beginning
                # past the region's end covers nothing (gain#636).  A
                # scan needing neither skips the clip entirely.
                span = clip_span(left, right, start, end) \
                    if weight_is_span or coverage is not None else None
                if coverage is not None and span is not None:
                    coverage.add_interval(
                        span[0], span[1], normalize_values(rec))
                if weight_is_span:
                    if span is None:
                        continue
                    left, right = span
                    weight = right - left + 1
                else:
                    weight = 1
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
        coverage: RegionCoverage | None = None,
    ) -> dict[str, Histogram]:
        """Vectorized equivalent of :meth:`_do_histogram`.

        Reads a region as batches of column arrays -- tabix pulls raw pysam
        rows directly and bigWig converts each fetched interval chunk in one
        shot, neither building a ``Record`` per row -- and accumulates each
        score's histogram with the histogram's own ``add_batch`` rather than a
        per-record ``add_value``.  The clip, the weight, the overlap rule and
        the value coercion are identical to the per-record path (pinned by the
        bulk-vs-per-record tests, and by both paths reading one statement of
        the per-kind rules); the dispatch restricts this to the score and
        histogram combinations :meth:`_can_bulk_histogram` admits, over
        tabix/bigWig tables -- everything else keeps :meth:`_do_histogram`.
        """
        result: dict[str, Histogram] = {}
        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            result[score_id] = build_empty_histogram(hist_conf)

        accumulate = GenomicScoreImplementation._accumulate_arrays
        if coverage is not None:

            def accumulate_with_coverage(
                arrays: RecordArrays,
                result: dict[str, Histogram],
                region: tuple[str, int | None, int | None],
                score: GenomicScore,
            ) -> None:
                GenomicScoreImplementation._accumulate_arrays(
                    arrays, result, region, score)
                GenomicScoreImplementation._accumulate_coverage(
                    arrays, coverage, region)

            accumulate = accumulate_with_coverage

        return GenomicScoreImplementation._bulk_region_scan(
            resource, result, chrom, start, end, accumulate)

    @staticmethod
    def _accumulate_coverage(
        arrays: RecordArrays,
        coverage: RegionCoverage,
        region: tuple[str, int | None, int | None],
    ) -> None:
        """Fold one batch of column arrays into the region's coverage.

        Clipping only: rows are clipped to the region on both edges -- a
        record beginning past the region's end covers nothing, the same
        verdict the per-record path gets from ``clip_span`` (gain#636) --
        and handed to :meth:`RegionCoverage.add_interval_batch`, which
        owns the run-collapse algebra.  Nothing here knows what "equal
        values" means.  The batches the backends return rarely carry a
        row outside the queried region, so the all-kept batch skips the
        mask copies entirely.
        """
        _chrom, start, end = region
        pos_begin, pos_end, value_cells = arrays
        keep = np.ones(pos_begin.shape[0], dtype=bool)
        if start is not None:
            keep &= pos_end >= start
        if end is not None:
            keep &= pos_begin <= end
        if not keep.any():
            return
        if keep.all():
            left, right = pos_begin, pos_end
            cells = list(value_cells.values())
        else:
            left, right = pos_begin[keep], pos_end[keep]
            cells = [column[keep] for column in value_cells.values()]
        if start is not None:
            left = np.maximum(left, start)
        if end is not None:
            right = np.minimum(right, end)
        coverage.add_interval_batch(left, right, cells)

    @staticmethod
    def _bulk_region_scan(
        resource: GenomicResource,
        result: dict[str, _AccT],
        chrom: str,
        start: int,
        end: int,
        accumulate: Callable[
            [RecordArrays, dict[str, _AccT],
             tuple[str, int | None, int | None], GenomicScore],
            None],
    ) -> dict[str, _AccT]:
        """Drive a bulk region scan, folding each batch into ``result``.

        The shared skeleton of :meth:`_do_histogram_bulk` and
        :meth:`_do_min_max_bulk`: open the score and stream the region's
        column-array batches through ``accumulate`` (which mutates
        ``result``).  The caller supplies the pre-built ``result`` -- empty
        histograms or seeded ``MinMaxValue`` -- and the matching accumulator.
        Batches are keyed by SCORE ID: the score resolves each id to its
        payload column itself (gain#398), so nothing here handles column
        indices.

        This is the scan's vectorized door, and the counterpart of
        :meth:`_scan_region`: the batches are read through
        ``validate_record_arrays``, one visible extra link over the stream
        the scan is already pulling, so that a pass cannot be added that
        quietly reads unvalidated (ADR 0008).  The kind states its own rule
        in that method's body; nothing here knows what the rule is.

        The validator is per REGION, and a region lies within one contig, so
        the ordering carry never spans a contig boundary -- the same reason
        the per-record validators reset on a change of chromosome.
        """
        impl = build_score_implementation_from_resource(resource)
        with impl.score.open() as score:
            batches = score.validate_record_arrays(
                score.fetch_region_value_arrays(
                    chrom, start, end, list(result),
                    batch_size=GenomicScoreImplementation._SCAN_BATCH_SIZE),
                chrom)
            for arrays in batches:
                accumulate(arrays, result, (chrom, start, end), score)
        return result

    @staticmethod
    def _accumulate_arrays(
        arrays: RecordArrays,
        result: dict[str, Histogram],
        region: tuple[str, int | None, int | None],
        score: GenomicScore,
    ) -> None:
        """Fold one batch of column arrays into the per-score histograms.

        ``arrays`` is one ``(pos_begin, pos_end, {score_id: cells})`` batch as
        produced by :meth:`_region_value_arrays`.  Clips each record to
        ``[start, end]`` exactly as the per-record read does (and drops
        records ending before ``start``), weights it as ``score``'s kind
        weights it, and adds each score's values vectorized.  Whether the
        batch is one this kind's records may form was settled before it got
        here, by the door's ``validate_record_arrays``.

        A histogram that refuses its batch is nullified and the rest of the
        resource's scores carry on, exactly as :meth:`_do_histogram` nullifies
        one that refuses a value: a categorical histogram raises once its
        values outgrow ``UNIQUE_VALUES_LIMIT``, and the score it belongs to
        must not cost the others their statistics.  A nullified score is
        skipped by every later batch, which is what the per-record path gets
        from ``NullHistogram.add_value`` being a no-op.
        """
        pos_begin, pos_end, value_cells = arrays
        keep, weights = GenomicScoreImplementation._clip_and_weigh(
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

    @staticmethod
    def _clip_and_weigh(
        pos_begin: np.ndarray,
        pos_end: np.ndarray,
        region: tuple[str, int | None, int | None],
        score: GenomicScore,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Clip a batch to the region and weigh it, per ``score``'s kind.

        Returns ``(keep, weights)``: the mask of records surviving the
        ``pos_end >= start`` skip, and their weights.  For a span-weighted
        kind that skip is the same edge
        :func:`~gain.genomic_resources.genomic_scores.clip_span` applies per
        record in the statistics scan; for a count kind the per-record scan
        reads no window at all, and the two paths agree because the mask is
        computed on the RAW ``pos_end`` -- no record a backend can answer a
        region query with falls to it.  Either way a region measures the
        same whichever path a resource was eligible for.
        Nothing here stops a negative weight if an inverted span arrives
        (the backends' doing rather than this function's).  The per-record
        consumer refuses such a record -- one beginning past the region's
        end -- via :func:`~gain.genomic_resources.genomic_scores.clip_span`;
        gain#636 tracks closing that edge on this path too.

        Measuring only.  Whether the batch is one this kind's records may
        form is settled upstream, by the door's ``validate_record_arrays``,
        against the RAW columns -- which is why no rule is stated here and
        why this may clip freely without a verdict depending on it.

        The weight is read off the score class, which states it once for this
        path and for the per-record one: ``RECORD_WEIGHT_IS_SPAN`` -- a
        position-score record counts once per base pair of the queried region
        it covers (``min(end, pos_end) - max(start, pos_begin) + 1``); an
        allele record and a fragment count 1, however wide they are.
        """
        _chrom, start, end = region
        count = pos_begin.shape[0]
        left = pos_begin if start is None else np.maximum(pos_begin, start)
        right = pos_end if end is None else np.minimum(pos_end, end)
        keep = np.ones(count, dtype=bool) if start is None \
            else (pos_end >= start)

        kleft = left[keep]
        kright = right[keep]
        weights = (kright - kleft + 1).astype(np.int64) \
            if score.RECORD_WEIGHT_IS_SPAN \
            else np.ones(kleft.size, dtype=np.int64)
        return keep, weights

    @staticmethod
    def _can_bulk_histogram(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
    ) -> bool:
        """Whether the vectorized scan may serve this histogram build.

        :meth:`_bulk_scan_eligible` plus the conditions that are this caller's
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
        So a categorical histogram over an ``int`` score, or a number
        histogram over a ``str`` one, keeps :meth:`_do_histogram`, which
        handles both as it always has.
        """
        pairing = {
            NumberHistogramConfig: ("float", "int"),
            CategoricalHistogramConfig: ("str",),
        }
        bulk_score_ids = []
        score_defs = build_score_implementation_from_resource(
            resource).score.score_definitions
        for score_id, hist_conf in all_hist_confs.items():
            if isinstance(hist_conf, NullHistogramConfig):
                continue
            value_types = pairing.get(type(hist_conf))
            score_def = score_defs.get(score_id)
            if value_types is None or score_def is None \
                    or score_def.value_type not in value_types:
                return False
            bulk_score_ids.append(score_id)
        return GenomicScoreImplementation._bulk_scan_eligible(
            resource, bulk_score_ids)

    @staticmethod
    def _can_bulk_min_max(
        resource: GenomicResource,
        score_ids: list[str],
    ) -> bool:
        """Whether the vectorized scan may serve this min/max pass.

        :meth:`_bulk_scan_eligible` plus the one condition that is this
        caller's alone: every score must be a NUMBER.  The reduction is
        ``min()``/``max()`` over the non-nan values of a float64 column, and a
        ``str`` score's column is an object array, which ``np.isnan`` refuses
        outright.

        A str score reaches here only through a misconfiguration: a min/max is
        scheduled for a score whose histogram is a number histogram without a
        view range, and a number histogram over a str score is exactly the
        mismatch :meth:`_can_bulk_histogram` keeps off the bulk path.  Left
        ungated, a column of nothing but NA sentinels would raise here, out of
        a generator and past every nullify handler, where the per-record path
        yields an empty min/max and nullifies that one histogram -- so the
        condition is stated for this consumer too, not assumed from the other.
        """
        score_defs = build_score_implementation_from_resource(
            resource).score.score_definitions
        for score_id in score_ids:
            score_def = score_defs.get(score_id)
            if score_def is None \
                    or score_def.value_type not in ("float", "int"):
                return False
        return GenomicScoreImplementation._bulk_scan_eligible(
            resource, score_ids)

    @staticmethod
    def _bulk_scan_eligible(
        resource: GenomicResource,
        score_ids: list[str],
    ) -> bool:
        """Whether a vectorized region scan may serve these scores.

        The shared gate for the histogram and min/max bulk paths, and the place
        the conditions that are THIS caller's live -- as opposed to the one
        condition that is the backend's, which the score answers itself:

        * a resource kind the bulk path is exercised against
          (:data:`_BULK_SCAN_RESOURCE_TYPES`): a position, allele or fragment
          score.  Their record semantics are not assumed here -- the score
          class states them, in ``RECORD_WEIGHT_IS_SPAN`` and in its own
          ``validate_record_arrays`` body -- so what this excludes is
          ``np_score``, of which no production GRR has one;
        * every score of a value type the column parse defines
          (``float``, ``int``, ``str``) -- asked of the score, which owns
          that parse;
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

        A resource the scan refuses is reported here and the refusal
        re-raised: this is the only frame that knows both which resource the
        scan was reading and that the reading is what failed, and the task
        graph's own report names no resource.
        """
        try:
            if chrom is not None and start is not None and end is not None \
                    and GenomicScoreImplementation._can_bulk_min_max(
                        resource, score_ids):
                return GenomicScoreImplementation._do_min_max_bulk(
                    resource, score_ids, chrom, start, end)
            return GenomicScoreImplementation._do_min_max(
                resource, score_ids, chrom, start, end)
        except MalformedResourceError as err:
            report_resource_failure(
                err, "could not scan the values of", resource.resource_id)
            raise

    @staticmethod
    def _do_histogram_task(
        resource: GenomicResource,
        all_hist_confs: dict[str, HistogramConfig],
        chrom: str,
        start: int | None,
        end: int | None,
    ) -> RegionScanResult:
        """Compute a region's histograms, bulk-vectorized where eligible.

        The bulk path needs a bounded region: a concrete contig, because its
        overlap guard runs along a single chromosome's records, and concrete
        bounds, because that is what the score's bulk read takes.  Any
        unbounded scan keeps the per-record path.

        A resource the scan refuses is reported here and the refusal
        re-raised, for the reason :meth:`_do_min_max_task` gives.

        Coverage rides the same read: a kind whose rows have a span to
        union gets a :class:`RegionCoverage` accumulated by whichever
        path serves the histograms, carried out in the task's RETURN
        value — a mutated argument would not travel under a distributed
        executor, whose task results arrive serialized.
        """
        coverage = None
        if resource.get_type() in _COVERAGE_SCAN_RESOURCE_TYPES:
            coverage = RegionCoverage(chrom, start, end)
        try:
            if chrom is not None and start is not None and end is not None \
                    and GenomicScoreImplementation._can_bulk_histogram(
                        resource, all_hist_confs):
                histograms = GenomicScoreImplementation._do_histogram_bulk(
                    resource, all_hist_confs, chrom, start, end,
                    coverage=coverage)
            else:
                histograms = GenomicScoreImplementation._do_histogram(
                    resource, all_hist_confs, chrom, start, end,
                    coverage=coverage)
            return RegionScanResult(histograms, coverage)
        except MalformedResourceError as err:
            report_resource_failure(
                err, "could not build the histograms of",
                resource.resource_id)
            raise

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
        arrays: RecordArrays,
        result: dict[str, MinMaxValue],
        region: tuple[str, int | None, int | None],
        score: GenomicScore,
    ) -> None:
        """Fold one batch of column arrays into the per-score min/max.

        Shares the clip/skip with the histogram path, and the door it is read
        through with every pass; the reduction takes ``min()``/``max()`` over
        the kept values
        with the nans dropped first -- an empty remainder contributes nothing --
        folded into the running ``MinMaxValue`` exactly as ``add_value`` seeds
        and combines them.

        An extremum of an ``int`` score is converted back to ``int``, because
        that is the type the per-record ``add_value`` folds in and therefore
        what ``MinMaxValue.serialize`` writes (``min: 3``, not ``min: 3.0``).
        The column arrives as ``float64`` -- the array's non-value has to be a
        nan -- so the round trip is exact up to 2**53 and the correctly
        rounded integer above it.
        """
        pos_begin, pos_end, value_cells = arrays
        keep, _weights = GenomicScoreImplementation._clip_and_weigh(
            pos_begin, pos_end, region, score)

        for score_id, min_max in result.items():
            values = value_cells[score_id][keep]
            finite = values[~np.isnan(values)]
            if finite.size:
                is_int = \
                    score.score_definitions[score_id].value_type == "int"
                low: float = int(finite.min()) if is_int \
                    else float(finite.min())
                high: float = int(finite.max()) if is_int \
                    else float(finite.max())
                min_max.min = low if np.isnan(min_max.min) \
                    else min(min_max.min, low)
                min_max.max = high if np.isnan(min_max.max) \
                    else max(min_max.max, high)

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
    def _merge_coverage(
        resource: GenomicResource,
        *results: RegionScanResult,
    ) -> CoverageStatistics | None:
        """Fold the regions' coverage, or ``None`` for an uncovered kind.

        The fold sorts the regions into genomic order rather than
        trusting the task-argument order they arrived in, is keyed by
        chromosome, and within one chromosome ``RegionCoverage.merge``
        still refuses a pair that is not adjacent-and-in-order -- a gap
        or overlap fails the build rather than mis-counting it.
        """
        coverages = sorted(
            (result.coverage for result in results
             if result.coverage is not None),
            key=lambda coverage: (
                coverage.chrom,
                coverage.start if coverage.start is not None else 0))
        if not coverages:
            return None
        statistics = CoverageStatistics()
        try:
            for coverage in coverages:
                statistics.fold_region(coverage)
        except ValueError as err:
            report_resource_failure(
                err, "could not merge the coverage of",
                resource.resource_id)
            raise
        return statistics

    @staticmethod
    def _save_coverage(
        resource: GenomicResource,
        statistics: CoverageStatistics | None,
    ) -> None:
        if statistics is None:
            return
        with resource.proto.open_raw_file(
                resource, COVERAGE_STATISTICS_FILE, mode="wt") as outfile:
            outfile.write(statistics.serialize())

    @staticmethod
    def _merge_and_save_histograms(
        resource: GenomicResource,
        *results: RegionScanResult,
    ) -> dict[str, Histogram]:
        merged_histograms = GenomicScoreImplementation._merge_histograms(
            resource, *(result.histograms for result in results))
        GenomicScoreImplementation._save_coverage(
            resource,
            GenomicScoreImplementation._merge_coverage(resource, *results))
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

    Carries no statistics behaviour of its own: a fragment's weight-1 rule
    is declared on ``FragmentScore`` (``RECORD_WEIGHT_IS_SPAN``) and read by
    both scan paths from there.
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
