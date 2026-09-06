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
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
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
    """What every genomic-score kind answers alike.

    The resource protocol -- the statistics build tasks, the file set,
    the hashes -- and the page protocol: ``get_info`` and
    ``get_statistics_info`` hand the repository the page builder passes
    to whichever kind renders, through ``_render_repo``.  It names no
    kind's template and no kind's section accessors; each kind is a
    subclass that does both, and the factory in :mod:`.builders` and
    the entry points hand out only those.  Nothing instantiates this
    class for a real resource.
    """

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
