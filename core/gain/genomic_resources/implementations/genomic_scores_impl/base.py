from __future__ import annotations

import json
import weakref
from typing import Any, ClassVar

from gain import logging
from gain.genomic_resources.dvc import DVC_SUFFIX
from gain.genomic_resources.genomic_scores import (
    GenomicScore,
    build_score_from_resource,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
    ChromLengthSource,
    ContigExtent,
    DerivedFrom,
    StoredChromLengths,
    derive_chrom_lengths,
    files_md5_of,
    load_chrom_lengths,
    save_chrom_lengths,
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
    DerivedFilesState,
    InfoImplementationMixin,
)
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.genomic_resources.statistics.alleles import ALLELE_STATISTIC
from gain.genomic_resources.statistics.base_statistic import StoredStatistic
from gain.genomic_resources.statistics.coverage import COVERAGE_STATISTIC
from gain.genomic_resources.statistics.fragments import FRAGMENT_STATISTIC
from gain.genomic_resources.utils import read_resource_id_label
from gain.task_graph.graph import Task, TaskDesc, TaskGraph
from gain.utils.log_safety import escape_unsafe_characters
from gain.utils.regions import (
    Region,
    split_into_regions,
)

from . import scan

logger = logging.getLogger(__name__)

#: How many contigs the genome does not list are named before ``...``:
#: a mapping onto hg38's alts leaves hundreds, and the count says so.
_UNLISTED_CONTIGS_SAMPLE = 5


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

        # One resolver pass per build: its answers are stored for the
        # readers (gain#1576) AND split the regions below.  Written here,
        # in the controller, rather than as a task: the file is
        # independent of the histograms and under its own gate.
        stored = self._store_chrom_lengths(grr)

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

        regions = self._regions_from(stored.lengths, region_size)
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
        # The statistics hash looks every entry up in the resource
        # manifest, and the score's table resolves its index against that
        # same manifest, so the two cannot disagree (gain#595).
        return self.score.resource_files()

    def stored_statistics(self) -> list[StoredStatistic]:
        # The same three declarations the scan gates on, so what is
        # declared here is what ``scan.merge_and_save_histograms`` writes.
        return [
            stored for stored in (
                COVERAGE_STATISTIC, FRAGMENT_STATISTIC, ALLELE_STATISTIC)
            if stored.writes_for(self.score)
        ]

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
        """Every rung's answer per contig of the score, in table order.

        The genome the ``reference_genome`` label names -- resolved
        through ``grr`` -- is the top rung; the table's own answer (the
        bigWig header, the tabix probe) the other.  Both are asked for
        every contig and each record holds what each answered, its
        ``best`` by rank; a contig with no length keeps the table's
        reason (``EMPTY`` / ``UNDETERMINED``) in its record.

        With a genome that resolved, the contigs it does not list are
        reported too (gain#1575): some of them is one WARNING per call,
        all of them a ``ValueError``.

        Opens the score if it is closed, and closes it again only in
        that case -- an already-open score stays open for its owner.
        """
        return self._derive_chrom_lengths(self._resolve_labelled_genome(grr))

    def derived_files_state(
        self, grr: GenomicResourceRepo | None,
    ) -> DerivedFilesState:
        """Whether the stored chromosome lengths describe the score now.

        ``CURRENT`` when the stored file's key -- the label the genome
        resolves from and the manifest md5 of every table file -- is
        the resource's key today.  Otherwise ``STALE`` when every table
        file is here to rebuild it from, and ``PAYLOAD_ABSENT`` when
        the missing ones are all beside their ``.dvc`` sidecars: an
        unpulled DVC checkout, reported once as a WARNING here, where
        the missing files are known.  A file missing with no sidecar
        is not that -- the resource is broken, whatever the stored key
        says, and the rebuild fails it, as any read would.  Looks for
        files and compares keys; opens no table.
        """
        missing = [
            file_name for file_name in sorted(self.files)
            if not self.resource.file_exists(file_name)]
        unpulled = [
            file_name for file_name in missing
            if self.resource.file_exists(file_name + DVC_SUFFIX)]
        if len(unpulled) < len(missing):
            # A file nothing vouches for: the resource is broken, and
            # the rebuild is what fails it, as any read of it would.
            return DerivedFilesState.STALE
        stored = load_chrom_lengths(self.resource)
        if stored is not None and self._is_derived_from_now(
                stored.derived_from, grr):
            return DerivedFilesState.CURRENT
        logger.info(
            "stored chromosome lengths of <%s> are %s; needs update",
            self.resource.get_full_id(),
            "absent" if stored is None else "outdated")
        if unpulled:
            logger.warning(
                "<%s>: %s is a .dvc pointer whose payload is not here; "
                "its chromosome lengths will be stored by the next "
                "repair that has it",
                self.resource.get_full_id(), ", ".join(unpulled))
            return DerivedFilesState.PAYLOAD_ABSENT
        return DerivedFilesState.STALE

    def rebuild_derived_files(
        self, grr: GenomicResourceRepo | None,
    ) -> None:
        self._store_chrom_lengths(grr)

    def _store_chrom_lengths(
        self, grr: GenomicResourceRepo | None,
    ) -> StoredChromLengths:
        """Run the ladder over the score and write what it found.

        The one writer of ``CHROM_LENGTHS_FILE``, for both the full
        statistics build and the lengths-only rewrite.
        """
        genome_id, ref_genome = self._labelled_genome(grr)
        stored = StoredChromLengths(
            lengths=self._derive_chrom_lengths(ref_genome),
            derived_from=DerivedFrom(
                # The label the genome was resolved FROM, so an
                # unresolvable genome is recorded as none at all -- and
                # reads as a change the day it resolves.
                reference_genome=(
                    genome_id if ref_genome is not None else None),
                files_md5=self._files_md5()),
            table_source=self.score.chrom_length_source)
        save_chrom_lengths(self.resource, stored)
        return stored

    def _derive_chrom_lengths(
        self, ref_genome: ReferenceGenome | None,
    ) -> dict[str, ChromLength]:
        """Run the ladder over the score, open-if-closed, with
        ``ref_genome`` as its top rung."""
        opened_here = not self.score.is_open()
        if opened_here:
            self.score.open()
        try:
            lengths = derive_chrom_lengths(self.score, ref_genome)
        finally:
            if opened_here:
                self.score.close()
        if ref_genome is not None:
            self._report_contig_overlap(ref_genome, lengths)
        return lengths

    def _report_contig_overlap(
        self, ref_genome: ReferenceGenome, lengths: dict[str, ChromLength],
    ) -> None:
        """Say which of the score's contigs the genome does not list.

        Read off the records: a contig the genome lists carries its
        answer, whatever the table said.  Zero overlap is a mis-authored
        label -- typically a ``chrom_mapping`` that does not produce the
        genome's names -- and fails the resource; a mapping that leaves
        some contigs off the genome on purpose is only warned about.
        """
        unlisted = [
            chrom for chrom, record in lengths.items()
            if ChromLengthSource.REFERENCE_GENOME not in record.answers
        ]
        if not unlisted:
            return
        # Repository content on a log line: a name is escaped so it
        # cannot end the line and start a forged record (gain#642).
        sample = [
            escape_unsafe_characters(chrom)
            for chrom in unlisted[:_UNLISTED_CONTIGS_SAMPLE]
        ]
        if len(unlisted) > _UNLISTED_CONTIGS_SAMPLE:
            sample.append("...")
        if len(unlisted) == len(lengths):
            raise ValueError(
                f"reference_genome {ref_genome.resource_id} of "
                f"{self.resource.resource_id} lists none of the score's "
                f"contigs ({', '.join(sample)}); a chrom_mapping that "
                f"does not produce the genome's contig names is the usual "
                f"cause")
        logger.warning(
            "reference_genome %s of %s does not list %d of the score's "
            "%d contigs (%s); their lengths fall to the table's own "
            "source",
            ref_genome.resource_id, self.resource.resource_id,
            len(unlisted), len(lengths), ", ".join(sample))

    def _is_derived_from_now(
        self, key: DerivedFrom, grr: GenomicResourceRepo | None,
    ) -> bool:
        """Whether ``key`` describes the resource as it is today.

        The files by their manifest md5; the genome by the label.  A key
        derived from a genome stands while the label still names it,
        whether or not the genome is still in the repository -- the
        record is the record.  One derived with none stands only while
        there is still none to resolve: a genome that turns up later
        reads as a change, and that is the one case the label has to be
        resolved to tell.
        """
        if key.files_md5 != self._files_md5():
            return False
        genome_id = read_resource_id_label(
            self.resource, "reference_genome")
        if key.reference_genome is not None:
            return key.reference_genome == genome_id
        return genome_id is None or self._labelled_genome(grr)[1] is None

    def _files_md5(self) -> dict[str, str | None]:
        """The manifest md5 of every table file, keyed by name -- what
        the statistics hash and the stored lengths' key both compare."""
        return files_md5_of(self.resource.get_manifest(), self.files)

    def _resolve_labelled_genome(
        self, grr: GenomicResourceRepo | None,
    ) -> ReferenceGenome | None:
        """The genome the ``reference_genome`` label names, or ``None``."""
        return self._labelled_genome(grr)[1]

    def _labelled_genome(
        self, grr: GenomicResourceRepo | None,
    ) -> tuple[str | None, ReferenceGenome | None]:
        """The ``reference_genome`` label as an id, and the genome it
        names -- either ``None`` when there is none.

        The one resolver of that label for the statistics build, the
        stored lengths' key and the page's coverage denominator
        (gain#1414), so a label that fails to name a genome is treated
        alike wherever it is read: the lengths fall through to the
        table's own answer, as an unlabelled score's do, and the page
        degrades to raw counts.  Never a raise -- a mis-authored label
        on one resource must not abort a repository-wide statistics
        walk or a page build.

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
            return genome_id, self._get_reference_genome_cached(
                grr, genome_id)
        except ValueError:
            logger.warning(
                "meta.labels.reference_genome of %s names %r, which is "
                "not a genome resource; ignoring it",
                self.resource.resource_id, genome_id)
            return genome_id, None

    def _get_chrom_regions(
        self, region_size: int, grr: GenomicResourceRepo | None = None,
    ) -> list[Region]:
        """The statistics regions, resolved live; writes nothing.

        The build itself goes through :meth:`_store_chrom_lengths`; this
        is the seam the region-boundary tests pin, with no file written
        into the fixture as a side effect.
        """
        return self._regions_from(self.get_chrom_lengths(grr), region_size)

    @staticmethod
    def _regions_from(
        lengths: dict[str, ChromLength], region_size: int,
    ) -> list[Region]:
        regions = []
        for chrom, resolved in lengths.items():
            best = resolved.best
            if best is not None:
                # Any rung's answer is a length to split by, the genome's
                # ahead of the table's; the table's extent, if it also
                # reported one, is a fact about the table's rows and not
                # a reason to skip a contig the genome vouches for.
                regions.extend(
                    split_into_regions(chrom, best.length, region_size))
                continue
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
            # No answer from any rung, so the record carries the table's
            # reason, and not EMPTY means UNDETERMINED: the length could
            # not be determined for a contig that may well hold records --
            # skipping it would leave them out of the statistics AND out
            # of the ordering checks the scan performs on the way, while
            # the resource still reported its statistics as freshly built.
            # A length is what SPLITTING needs, not what READING needs, so
            # scan the contig whole.  An unbounded region keeps the
            # per-record path (see scan.do_histogram_task): slower than a
            # split contig, never wrong.
            logger.warning(
                "unable to find chromosome length for %s; "
                "scanning it as a single unbounded region", chrom)
            regions.append(Region(chrom))
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
        return json.dumps({
            "config": {
                "histograms": [
                    hist_conf.to_dict()
                    for hist_conf in self.get_config_histograms().values()
                    if hist_conf is not None
                ],
                "table": {
                    # The validated ``table`` section the score built its
                    # table from; the definition the table holds is a Box
                    # over a copy of it and serialises identically.
                    "config": self.score.get_config()["table"],
                    "files_md5": self._files_md5(),
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
