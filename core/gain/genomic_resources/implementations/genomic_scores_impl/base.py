from __future__ import annotations

import json
import weakref
from typing import Any, ClassVar, cast

from gain import logging
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    ContigExtent,
    derive_chrom_lengths,
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
        filename = self._table_config()["filename"]
        files = {filename}
        if self.score.uses_tabix_index:
            index_filename = self._resolve_index_filename(filename)
            if index_filename is not None:
                files.add(index_filename)
        return files

    def _table_config(self) -> dict[str, Any]:
        """The ``table`` section of the score's validated configuration.

        What the score built its table from, and so what the file set
        and the statistics hash read: the definition the table holds is
        a ``Box`` over a copy of this dict, and serialises identically.
        """
        return cast("dict[str, Any]", self.score.get_config()["table"])

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
        # The table section is untyped config, so ``get`` is Any.
        configured = cast(
            "str | None", self._table_config().get("index_filename"))
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
        # ``find_resource``, not ``get_resource``: the two repository
        # kinds refuse a missing id with different exceptions (the group
        # repository the CLI builds raises ``ValueError``, a plain one
        # ``FileNotFoundError``), and catching one of them let the other
        # abort a score's statistics over a label naming nothing.
        genome_resource = grr.find_resource(genome_id)
        if genome_resource is None:
            logger.warning(
                "Couldn't find reference genome %s",
                genome_id,
            )
            # Not remembered: it may be present next call, and callers
            # cope (raw counts rendering, the table's own length scanning).
            return None
        ref_genome = build_reference_genome_from_resource(genome_resource)
        logger.info(
            "Using reference genome label <%s> ",
            genome_id,
        )
        cache.setdefault(grr, {})[genome_id] = ref_genome
        return ref_genome

    def get_chrom_lengths(
        self, grr: GenomicResourceRepo | None,
    ) -> dict[str, ChromLength]:
        """The ladder's answer per contig of the score, in table order.

        The genome the ``reference_genome`` label names -- resolved
        through ``grr`` -- is the top rung; the table's own answer (the
        bigWig header, the tabix probe) the rest, per contig.  A contig
        with no length keeps the reason (``EMPTY`` / ``UNDETERMINED``)
        in its record.

        Opens the score if it is closed, and closes it again only in
        that case -- an already-open score stays open for its owner.
        """
        ref_genome = self._resolve_labelled_genome(grr)
        opened_here = not self.score.is_open()
        if opened_here:
            self.score.open()
        try:
            return derive_chrom_lengths(self.score, ref_genome)
        finally:
            if opened_here:
                self.score.close()

    def _resolve_labelled_genome(
        self, grr: GenomicResourceRepo | None,
    ) -> ReferenceGenome | None:
        """The genome the ``reference_genome`` label names, or ``None``.

        The one reader of that label for both the statistics build and
        the page's coverage denominator (gain#1414), so a label that
        fails to name a genome is treated alike wherever it is read:
        the lengths fall through to the table's own answer, as an
        unlabelled score's do, and the page degrades to raw counts.
        Never a raise -- a mis-authored label on one resource must not
        abort a repository-wide statistics walk or a page build.

        Three ways it can fail to name one.  A value that is not a
        resource id at all -- the int, list or dict a free-form
        ``meta.labels`` allows -- is read as absent and reported by the
        narrowing (gain#1053).  An id the repository does not have is
        answered ``None``, with its own warning, by the cached resolver
        (which looks the id up rather than catching one repository
        kind's exception, gain#1419).  An id naming a resource of
        another type reaches ``build_reference_genome_from_resource``
        and is caught here.
        """
        genome_id = read_resource_id_label(
            self.resource, "reference_genome")
        try:
            return self._get_reference_genome_cached(grr, genome_id)
        except ValueError:
            logger.warning(
                "meta.labels.reference_genome of %s names %r, which is "
                "not a genome resource; ignoring it",
                self.resource.resource_id, genome_id)
            return None

    def _get_chrom_regions(
        self, region_size: int, grr: GenomicResourceRepo | None = None,
    ) -> list[Region]:
        """The statistics regions: the ladder's lengths, split."""
        return self._regions_from(self.get_chrom_lengths(grr), region_size)

    @staticmethod
    def _regions_from(
        lengths: dict[str, ChromLength], region_size: int,
    ) -> list[Region]:
        regions = []
        for chrom, resolved in lengths.items():
            if resolved.extent is ContigExtent.EMPTY:
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
            if resolved.extent is ContigExtent.UNDETERMINED:
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

            # The record's two shapes: no extent means the length is set.
            assert resolved.length is not None
            regions.extend(
                split_into_regions(
                    chrom,
                    resolved.length,
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
                    "config": self._table_config(),
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
