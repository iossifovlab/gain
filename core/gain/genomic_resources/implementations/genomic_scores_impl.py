from __future__ import annotations

import functools
import json
import weakref
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
    AlleleScore,
    GenomicScore,
    RecordArrays,
    build_score_from_resource,
    clip_span,
    owned_records_mask,
    owns_record,
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
from gain.genomic_resources.statistics.alleles import (
    ALLELE_COMPLEX_GRID_IMAGE_FILE,
    ALLELE_DELETION_LENGTHS_IMAGE_FILE,
    ALLELE_INSERTION_LENGTHS_IMAGE_FILE,
    ALLELE_STATISTICS_FILE,
    AlleleSectionDisplay,
    AlleleStatistics,
    RegionAlleles,
    allele_arrays_folded_into,
    build_allele_section_display,
    merge_region_alleles,
    records_folded_into,
    region_alleles_for,
    save_allele_statistics,
    serves_allele_arrays,
)
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_FRAGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_STATISTICS_FILE,
    CoverageDisplay,
    CoverageStatistics,
    FragmentDisplay,
    RegionCoverage,
    accumulate_coverage,
    build_coverage_display,
    build_fragment_display,
    merge_region_coverage,
    normalize_values,
    resolve_chrom_lengths,
    save_and_plot_coverage,
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
# path -- no error, no failing test, just the slow path forever.
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

# The kinds whose rows are PAIRWISE DISJOINT -- no two share a position.
# Position scores, whose ``validate_records`` refuses a row beginning at
# or before its predecessor's end, on raw spans.  (Adjacent rows are
# legal and common, and the segment algebra depends on it, so this is
# deliberately not phrased as "cannot touch": ADR 0020 and
# ``add_interval`` use touching for exactly that adjacency.)
#
# Named for the FACT rather than for one of its consequences, because it
# has two: disjoint rows have an exact run algebra, so their segment
# statistics are published; and they cannot double-count a position, so
# the scan hands their coverage full unclipped spans.  Fragment rows
# overlap, so they get neither (their counts are their own statistic --
# gain#794).
_NON_OVERLAPPING_ROW_RESOURCE_TYPES = frozenset(
    equivalent_resource_types("position_score"))

# The kinds whose rows ARE fragments, in both spellings, and so publish
# a fragment count and fragment-length histogram (gain#794).  A separate
# statement from the one above rather than its complement: that these
# are exactly the coverage-scanned kinds that overlap is true today and
# incidental -- a future overlapping kind that is not a fragment score
# would inherit fragment counts it has no business publishing.
_FRAGMENT_STATISTICS_RESOURCE_TYPES = frozenset(
    equivalent_resource_types("fragment_score"))


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
    ``_bulk_region_scan`` stay a driver that reads whatever producer it
    was handed.
    """
    return allele_arrays_folded_into(
        cast(AlleleScore, score), chrom, start, end, score_ids,
        batch_size=batch_size, alleles=alleles)


class RegionScanResult(NamedTuple):
    """What one region's statistics task hands to the merge step."""

    histograms: dict[str, Histogram]
    coverage: RegionCoverage | None
    alleles: RegionAlleles | None


class GenomicScoreImplementation(ScoreImplementationBase):
    # pylint: disable=too-many-public-methods
    """Genomic scores base class."""

    def __init__(self, resource: GenomicResource):
        super().__init__(resource)
        self.score: GenomicScore = build_score_from_resource(resource)
        self._render_repo: GenomicResourceRepo | None = None
        # One page render asks for the stored coverage once per section
        # -- Coverage and Fragments both -- and over an HTTP or S3
        # repository that is a network round trip each.  Held for the
        # life of this object, which is built per render.
        self._coverage_statistics: CoverageStatistics | None = None
        self._coverage_statistics_read = False

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

    @staticmethod
    def get_coverage_segment_lengths_image_filename() -> str:
        """The info page's one statement of the global histogram's path."""
        return COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_coverage_fragment_lengths_image_filename() -> str:
        """The info page's one statement of the fragment image's path."""
        return COVERAGE_FRAGMENT_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_allele_insertion_lengths_image_filename() -> str:
        """The info page's one statement of the insertion image's path."""
        return ALLELE_INSERTION_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_allele_deletion_lengths_image_filename() -> str:
        """The info page's one statement of the deletion image's path."""
        return ALLELE_DELETION_LENGTHS_IMAGE_FILE

    @staticmethod
    def get_allele_complex_grid_image_filename() -> str:
        """The info page's one statement of the complex grid's path."""
        return ALLELE_COMPLEX_GRID_IMAGE_FILE

    def get_coverage_statistics(self) -> CoverageStatistics | None:
        """The resource's coverage statistics, or ``None`` if not built.

        Absence is an expected state, not an error: statistics roll out
        lazily as resources are rebuilt (``calc_statistics_hash`` does
        not know about this file), so a resource built before the
        statistic existed simply has nothing to show yet.
        """
        if self._coverage_statistics_read:
            return self._coverage_statistics
        try:
            content = self.resource.get_file_content(
                COVERAGE_STATISTICS_FILE)
        except FileNotFoundError:
            statistics = None
        else:
            statistics = CoverageStatistics.deserialize(content)
        self._coverage_statistics = statistics
        self._coverage_statistics_read = True
        return statistics

    def get_allele_statistics(self) -> AlleleStatistics | None:
        """The resource's allele statistics, or ``None`` if not built.

        Absence is an expected state for the reason
        :meth:`get_coverage_statistics` gives: the rollout is lazy.
        """
        try:
            content = self.resource.get_file_content(ALLELE_STATISTICS_FILE)
        except FileNotFoundError:
            return None
        return AlleleStatistics.deserialize(content)

    def get_allele_display(self) -> AlleleSectionDisplay | None:
        """The Alleles section's payload, or ``None`` if not built."""
        statistics = self.get_allele_statistics()
        if statistics is None:
            return None
        return build_allele_section_display(statistics)

    def get_coverage_display(self) -> CoverageDisplay | None:
        """The Coverage section's payload: raw counts plus fractions.

        ``None`` when the statistic is not built.  This frame's whole
        job is the genome rung of the denominator ladder -- it needs the
        repository handed to the enclosing :meth:`get_info` /
        :meth:`get_statistics_info` call, and the cache it goes through
        is shared with the scan's contig splitting.  Invoked outside a
        page build no repository is available and that rung resolves
        nothing, which degrades to raw counts rather than failing.
        """
        coverage = self.get_coverage_statistics()
        if coverage is None:
            return None
        lengths = resolve_chrom_lengths(
            self.resource, self.score, self._render_genome(),
            coverage.covered_by_chromosome())
        return build_coverage_display(
            self.resource.resource_id, coverage, lengths)

    def get_fragment_display(self) -> FragmentDisplay | None:
        """The Fragments section's payload, or ``None`` if not computed.

        ``None`` covers both ways a fragment resource can have nothing
        to show: no statistics file at all, and a file written before
        fragment counts existed.  Both render the section's "not
        computed" fallback -- these statistics roll out lazily, as
        :meth:`get_coverage_statistics` explains.
        """
        coverage = self.get_coverage_statistics()
        if coverage is None:
            return None
        return build_fragment_display(coverage)

    def _render_genome(self) -> ReferenceGenome | None:
        """The resource's labelled reference genome, at render time.

        A label naming something that is not a genome is a reason to
        degrade to raw counts, not to fail the page build.
        """
        genome_id = cast(
            str | None,
            self.resource.get_labels().get("reference_genome"),
        )
        try:
            return self._get_reference_genome_cached(
                self._render_repo, genome_id)
        except ValueError:
            logger.warning(
                "reference_genome label %r of %s does not name a genome "
                "resource; ignoring it for coverage fractions",
                genome_id, self.resource.resource_id)
            return None

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

    #: Reference genomes already resolved, per repository.  Keyed by the
    #: repository FIRST: an id only names a genome relative to one, so an
    #: id-keyed cache hands a second repository defining the same id the
    #: first one's chromosome lengths (gain#857).  The key is WEAK -- a
    #: strong one would pin every repo ever seen -- and entries do free,
    #: because no protocol holds its repo back.  Identity is the
    #: comparison: the repo classes define no ``__eq__``.  Not held in
    #: the factory below beside its four peers because a
    #: ``ReferenceGenome`` owns a backend whose ``close()`` clears the
    #: index -- one shared instance would let any caller blank it.
    _REF_GENOME_CACHE: ClassVar[weakref.WeakKeyDictionary[
        GenomicResourceRepo, dict[str, ReferenceGenome]]
    ] = weakref.WeakKeyDictionary()

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
        cache = GenomicScoreImplementation._REF_GENOME_CACHE
        # ``get`` with a default reads without inserting.
        if (resolved := cache.get(grr, {}).get(genome_id)) is not None:
            return resolved
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
            # Not remembered: it may be present next call, and callers
            # cope (raw counts rendering, the table's own length scanning).
            return None
        cache.setdefault(grr, {})[genome_id] = ref_genome
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
        records = score.validate_records(
            score.fetch_records(chrom, start, end))
        if alleles is not None:
            records = records_folded_into(records, alleles)
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
            for left, _right, rec in GenomicScoreImplementation._scan_region(
                    score, chrom, start, end, score_ids):
                # The same record partition every statistic reads: a
                # region reduces the records it OWNS.  min/max is
                # idempotent under duplication, so the merged result
                # would survive without this -- but the bulk twin
                # selects, and a region measuring differently by which
                # path served it is what the parity tests refuse.
                if not owns_record(left, start, end):
                    continue
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
        *,
        coverage: RegionCoverage | None = None,
        alleles: RegionAlleles | None = None,
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
            # Coverage unions POSITIONS, and a union is only additive
            # across parallel regions when the spans are clipped to
            # disjoint extents -- so a kind whose rows can overlap keeps
            # clipping.  Rows that cannot are already pairwise disjoint,
            # so their coverage is exact unclipped and rides the same
            # record partition as every other statistic.
            clip_coverage = coverage is not None \
                and not coverage.rows_are_disjoint
            track_fragments = coverage is not None \
                and coverage.tracks_fragments
            for left, right, rec in GenomicScoreImplementation._scan_region(
                    score, chrom, start, end, score_ids,
                    alleles=alleles):
                owned = owns_record(left, start, end)
                if coverage is not None:
                    if clip_coverage:
                        span = clip_span(left, right, start, end)
                        if span is not None:
                            coverage.add_interval(
                                span[0], span[1], normalize_values(rec))
                    elif owned:
                        coverage.add_interval(
                            left, right, normalize_values(rec))
                if not owned:
                    continue
                if track_fragments:
                    # A fragment is the row as stored, at its own span.
                    assert coverage is not None
                    coverage.add_fragment(right - left + 1)
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
        *,
        coverage: RegionCoverage | None = None,
        alleles: RegionAlleles | None = None,
    ) -> dict[str, Histogram]:
        """Vectorized equivalent of :meth:`_do_histogram`.

        Reads a region as batches of column arrays -- tabix pulls raw pysam
        rows directly and bigWig converts each fetched interval chunk in one
        shot, neither building a ``Record`` per row -- and accumulates each
        score's histogram with the histogram's own ``add_batch`` rather than a
        per-record ``add_value``.  The selection, the weight, the overlap rule
        and the value coercion are identical to the per-record path (pinned
        by the bulk-vs-per-record tests, and by both paths reading one
        statement of the per-kind rules); the dispatch restricts this to the
        score and histogram combinations :meth:`_can_bulk_histogram`
        admits, over tabix/bigWig tables -- everything else keeps
        :meth:`_do_histogram`.
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
                accumulate_coverage(arrays, coverage, region)

            accumulate = accumulate_with_coverage

        batches = None
        if alleles is not None:
            # The widened read, folding the nucleotides off each batch on
            # its way to the shared door; what the door and this scan see
            # is the same three-column batch as ever.
            batches = functools.partial(
                _allele_batches, alleles=alleles,
                batch_size=GenomicScoreImplementation._SCAN_BATCH_SIZE)
        return GenomicScoreImplementation._bulk_region_scan(
            resource, result, chrom, start, end, accumulate, batches=batches)

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
        *,
        batches: Callable[
            [GenomicScore, str, int, int, list[str]],
            Iterable[RecordArrays]] | None = None,
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
            if batches is None:
                batches = GenomicScoreImplementation._validated_batches
            for arrays in batches(score, chrom, start, end, list(result)):
                accumulate(arrays, result, (chrom, start, end), score)
        return result

    @staticmethod
    def _validated_batches(
        score: GenomicScore,
        chrom: str,
        start: int,
        end: int,
        score_ids: list[str],
    ) -> Iterable[RecordArrays]:
        """The default producer: the shared read through the shared door."""
        return score.validate_record_arrays(
            score.fetch_region_value_arrays(
                chrom, start, end, score_ids,
                batch_size=GenomicScoreImplementation._SCAN_BATCH_SIZE),
            chrom)

    @staticmethod
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
        resource's scores carry on, exactly as :meth:`_do_histogram` nullifies
        one that refuses a value: a categorical histogram raises once its
        values outgrow ``UNIQUE_VALUES_LIMIT``, and the score it belongs to
        must not cost the others their statistics.  A nullified score is
        skipped by every later batch, which is what the per-record path gets
        from ``NullHistogram.add_value`` being a no-op.
        """
        pos_begin, pos_end, value_cells = arrays
        keep, weights = GenomicScoreImplementation._select_and_weigh(
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
    def _select_and_weigh(
        pos_begin: np.ndarray,
        pos_end: np.ndarray,
        region: tuple[str, int | None, int | None],
        score: GenomicScore,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Select a batch's owned records and weigh them, per ``score``'s kind.

        Returns ``(keep, weights)``:
        :func:`~gain.genomic_resources.genomic_scores.owned_records_mask`,
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
        path and for the per-record one: ``RECORD_WEIGHT_IS_SPAN`` -- a
        position-score record counts once per base pair it spans; an allele
        record and a fragment count 1, however wide they are.
        """
        _chrom, start, end = region
        keep = owned_records_mask(pos_begin, start, end)
        if not score.RECORD_WEIGHT_IS_SPAN:
            # A count kind needs only HOW MANY records it owns; gathering
            # their spans to measure one array's length would allocate
            # two full columns per batch and read neither.
            return keep, np.ones(
                int(np.count_nonzero(keep)), dtype=np.int64)
        if keep.all():
            # The common case by a wide margin: rows arrive begin-sorted
            # and only a leading run can fall outside the region, so
            # every batch after a region's first is wholly owned.
            return keep, (pos_end - pos_begin + 1).astype(np.int64)
        left = pos_begin[keep]
        right = pos_end[keep]
        return keep, (right - left + 1).astype(np.int64)

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
          ``validate_record_arrays`` body.  The set names every score kind
          GAIn accepts, so this test cannot fail today; it is kept so that
          a newly registered kind lands on the per-record path by default;
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
        executor, whose task results arrive serialized.  An allele
        score's :class:`RegionAlleles` rides it on the same terms, with
        one extra condition on the bulk path: it needs the nucleotides,
        so a backend that will not serve them sends the region back to
        the per-record read rather than to a statistic with no class
        data.
        """
        coverage = None
        resource_type = resource.get_type()
        if resource_type in _COVERAGE_SCAN_RESOURCE_TYPES:
            coverage = RegionCoverage(
                chrom, start, end,
                rows_are_disjoint=resource_type
                in _NON_OVERLAPPING_ROW_RESOURCE_TYPES,
                track_fragments=resource_type
                in _FRAGMENT_STATISTICS_RESOURCE_TYPES)
        score = build_score_implementation_from_resource(resource).score
        alleles = region_alleles_for(score, chrom, start, end)
        nucleotides = True
        if alleles is not None:
            # Asked of an OPEN score, and of the score ids a bulk read
            # would ask for -- the filter ``_do_histogram_bulk`` builds
            # its result from, since a null histogram has nothing to
            # accumulate.  Unopened, a table naming its key columns
            # nowhere but in its own header answers False and costs the
            # whole region the bulk scan for no gain in correctness.
            with score.open():
                nucleotides = serves_allele_arrays(score, [
                    score_id for score_id, conf in all_hist_confs.items()
                    if not isinstance(conf, NullHistogramConfig)])
        try:
            if chrom is not None and start is not None and end is not None \
                    and nucleotides \
                    and GenomicScoreImplementation._can_bulk_histogram(
                        resource, all_hist_confs):
                histograms = GenomicScoreImplementation._do_histogram_bulk(
                    resource, all_hist_confs, chrom, start, end,
                    coverage=coverage, alleles=alleles)
            else:
                histograms = GenomicScoreImplementation._do_histogram(
                    resource, all_hist_confs, chrom, start, end,
                    coverage=coverage, alleles=alleles)
            return RegionScanResult(histograms, coverage, alleles)
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
        per-record ``MinMaxValue.add_value``.  The parse, the region selection,
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

        Shares the record selection with the histogram path, and the door it
        is read through with every pass; the reduction takes ``min()``/``max()``
        over the owned values
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
        pos_begin, _pos_end, value_cells = arrays
        # The selection alone: min/max reduces the records this region
        # owns and weighs nothing, so it reads the mask directly rather
        # than asking for weights it would discard.
        _chrom, start, end = region
        keep = owned_records_mask(pos_begin, start, end)

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
    def _merge_and_save_histograms(
        resource: GenomicResource,
        *results: RegionScanResult,
    ) -> dict[str, Histogram]:
        merged_histograms = GenomicScoreImplementation._merge_histograms(
            resource, *(result.histograms for result in results))
        save_and_plot_coverage(resource, merge_region_coverage(
            resource.resource_id,
            (result.coverage for result in results)))
        save_allele_statistics(resource, merge_region_alleles(
            resource.resource_id,
            (result.alleles for result in results)))
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

    It does carry its own info page, which is the genomic-score page plus
    a Fragments section.  The section lives in a template that FILLS a
    block the shared template leaves empty, so a kind with no fragments
    renders no section at all -- rather than a heading permanently
    reading "not computed", which is what gating one shared template on
    a boolean produced for Coverage on allele scores.
    """
    # pylint: disable=useless-parent-delegation

    template_name: ClassVar[str] = "fragment_score.jinja"

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
