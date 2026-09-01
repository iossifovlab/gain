from __future__ import annotations

import json
import weakref
from typing import Any, ClassVar, cast

from gain import logging
from gain.genomic_resources.genomic_position_table import (
    ContigExtent,
    TabixGenomicPositionTable,
)
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
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
)
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
    build_allele_section_display,
)
from gain.genomic_resources.statistics.coverage import (
    COVERAGE_FRAGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_SEGMENT_LENGTHS_IMAGE_FILE,
    COVERAGE_STATISTICS_FILE,
    CoverageDisplay,
    CoverageStatistics,
    FragmentDisplay,
    build_coverage_display,
    build_fragment_display,
    resolve_chrom_lengths,
)
from gain.genomic_resources.utils import read_resource_id_label
from gain.task_graph.graph import Task, TaskDesc, TaskGraph
from gain.utils.regions import (
    Region,
    split_into_regions,
)

from . import scan

logger = logging.getLogger(__name__)


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

        Two ways it can fail to name one, and the guard below only ever
        covered the second.  A value that is not a resource id at all --
        the int, list or dict a free-form ``meta.labels`` allows -- used
        to reach resolution as itself and raise ``TypeError`` past the
        ``except ValueError``, failing the page build this comment says
        must not fail; it is now read as absent and reported by the
        narrowing (gain#1053).  A value that IS an id but names no
        genome still reaches resolution and is caught here.
        """
        genome_id = read_resource_id_label(
            self.resource, "reference_genome")
        try:
            return self._get_reference_genome_cached(
                self._render_repo, genome_id)
        except ValueError:
            logger.warning(
                "reference_genome label %r of %s does not name a genome "
                "resource; ignoring it for coverage fractions",
                genome_id, self.resource.resource_id)
            return None

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
                    scan.do_noregion_histograms,
                    args=[self.resource],
                    deps=[],
                ),
            ]

        with self.score.open():
            regions = self._get_chrom_regions(region_size, grr)
            all_min_max_scores, all_hist_confs = \
                scan.unpack_score_defs(self.resource)

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
                        scan.do_min_max_task,
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
                    scan.merge_min_max,
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
                    scan.do_histogram_task,
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
                scan.merge_and_save_histograms,
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
        # Narrowed rather than cast: a label that is not a resource id
        # used to reach the resolution cache as itself and raise
        # ``TypeError`` here, aborting a repository-wide statistics walk
        # over one mis-authored resource.  Read as absent, the contig
        # lengths fall through to the table's own answer below, which is
        # what an unlabelled score already does (gain#1053).
        ref_genome_id = read_resource_id_label(
            self.resource, "reference_genome")
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
                # keeps the per-record path (see scan.do_histogram_task):
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
