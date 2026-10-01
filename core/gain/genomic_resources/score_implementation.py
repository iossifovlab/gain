"""The implementation plane shared by gene scores and genomic scores.

``ScoreImplementationBase`` sits one layer above ``ScoreResource`` (the
catalogue plane in :mod:`gain.genomic_resources.score_resource`): where that
base owns what a score *is*, this base owns what a score *implementation* does
that means the same for both families -- contributing ``score_ids`` /
``score_descriptions`` into the FTS index.  Serialising-and-plotting a
computed histogram into the resource is the module-level
:func:`save_and_plot_histograms`, which both families' statistics builds
call.

It deliberately keeps ``create_statistics_build_tasks`` abstract: a gene score
emits a single task that scans a DataFrame, whereas a genomic score emits a
region-split DAG with a min/max merge stage.  These are genuinely different
strategies for genuinely different data shapes, so the base does not try to
unify them.

The location mirrors ``score_resource`` for the same reason: ``gene_scores``
already depends on ``genomic_resources``, so living here adds no new dependency
edge, whereas a top-level ``gain/scores/`` package would create a cycle.
"""
from __future__ import annotations

import fnmatch
from abc import abstractmethod

from gain.genomic_resources.dvc import DVC_SUFFIX
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    Histogram,
    NullHistogram,
    drop_stale_histogram_file,
    plot_histogram,
)
from gain.genomic_resources.repository import (
    GR_INDEX_SCORE_FIELDS,
    GenomicResource,
    GenomicResourceRepo,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.resource_implementation import (
    DEFAULT_STATISTICS_REGION_SIZE,
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.genomic_resources.score_resource import ScoreResource
from gain.task_graph.graph import TaskDesc


class ScoreImplementationBase(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """Shared implementation base for gene and genomic score resources.

    A concrete subclass must set ``self.score`` (a :class:`ScoreResource`) in
    its own ``__init__``; from it this base reads the score definitions for the
    search index.
    """

    score: ScoreResource

    @abstractmethod
    def create_statistics_build_tasks(
        self, *,
        region_size: int = DEFAULT_STATISTICS_REGION_SIZE,
        grr: GenomicResourceRepo | None = None,
    ) -> list[TaskDesc]:
        """Create tasks for calculating resource statistics for task graph.

        Kept abstract: gene and genomic scores build statistics with genuinely
        different task shapes (a single DataFrame scan versus a region-split
        DAG), so each family provides its own.
        """
        raise NotImplementedError

    def collect_index_info(
        self,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        header, row = super().collect_index_info()
        score_ids = " ".join(self.score.score_definitions.keys())
        score_descriptions = " ".join(
            sd.desc
            for sd in self.score.score_definitions.values()
            if sd.desc
        )
        return (
            (*header, *GR_INDEX_SCORE_FIELDS),
            (*row, score_ids, score_descriptions),
        )


def save_and_plot_histograms(
    resource: GenomicResource,
    score: ScoreResource,
    histograms: dict[str, Histogram],
) -> None:
    """Serialise each histogram into the resource and render its PNG.

    Every score in ``score.score_definitions`` leaves with exactly the
    files this build produced. A file an earlier build wrote that the
    current histogram no longer justifies -- the truncated sidecar of
    a histogram now within the limit, the image of a ``NullHistogram``,
    all three of a score absent from ``histograms`` -- is deleted
    rather than left to be served as current.  So is every histogram
    file of a score id ``score_definitions`` no longer holds.  The
    exception is a gzipped full histogram (``histogram_<id>.json.gz``):
    nothing here deletes one yet (gain#1734).

    A categorical histogram past ``UNIQUE_VALUES_LIMIT`` is written as
    deterministic gzipped JSON next to its plain truncated sidecar; every
    other histogram as plain JSON (ADR 0032).  A full histogram left in the
    other encoding by an earlier build is not deleted.  Readers usually
    skip it, because the sidecar decides which encoding loads.  A
    DVC-tracked sidecar that cannot be dropped, though, makes a stale
    ``.json.gz`` load after a rebuild back within the limit.  ADR 0032
    lists these cases.
    """
    proto = resource.proto
    for score_id, histogram in histograms.items():
        sidecar_filename = score.get_truncated_histogram_filename(score_id)
        if (
            isinstance(histogram, CategoricalHistogram)
            and histogram.unique_values
                > CategoricalHistogram.UNIQUE_VALUES_LIMIT
        ):
            # Decided from the histogram in hand, never from the stored
            # manifest: a first build past the limit has no sidecar
            # listed yet (ADR 0032).
            with proto.open_raw_file(
                resource,
                score.get_gzipped_histogram_filename(score_id),
                mode="wb",
            ) as outfile:
                outfile.write(histogram.serialize_gzipped())
            with proto.open_raw_file(
                resource,
                sidecar_filename,
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize_truncated())
        else:
            with proto.open_raw_file(
                resource,
                score.get_plain_histogram_filename(score_id),
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize())
            # A sidecar from an earlier build whose histogram has since
            # shrunk below the limit (or stopped being categorical)
            # would otherwise be served as current by truncated= loads.
            drop_stale_histogram_file(resource, sidecar_filename)
        image_filename = score.get_histogram_image_filename(score_id)
        if isinstance(histogram, NullHistogram):
            # Nullified while building: the null histogram is recorded
            # with its reason, but an image drawn by an earlier build
            # would show values the statistics no longer have.
            drop_stale_histogram_file(resource, image_filename)
        else:
            score_def = score.score_definitions[score_id]
            plot_histogram(
                resource,
                image_filename,
                histogram,
                score_id,
                score_def.small_values_desc,
                score_def.large_values_desc,
            )
    for score_id in score.score_definitions.keys() - histograms.keys():
        # A score the build dropped -- its definition resolves to a
        # null histogram config -- writes nothing, so an earlier
        # build's files would be served as current.
        for filename in (
            score.get_plain_histogram_filename(score_id),
            score.get_truncated_histogram_filename(score_id),
            score.get_histogram_image_filename(score_id),
        ):
            drop_stale_histogram_file(resource, filename)
    _drop_orphaned_histogram_files(resource, score)


#: The score-histogram file names the orphan sweep reconciles, with ``{}``
#: for the score id: the plain serialisations (legacy ``.yaml``, current
#: ``.json``), their truncated sidecars, and the image.  The gzipped full
#: histogram, ``statistics/histogram_{}.json.gz``, is not listed yet, so an
#: orphaned one is left in place (gain#1734).  No other statistic writes
#: under ``statistics/histogram_``.
_HISTOGRAM_FILE_TEMPLATES = (
    "statistics/histogram_{}.json",
    "statistics/histogram_{}.yaml",
    "statistics/histogram_{}.png",
    "statistics/truncated/histogram_{}.json",
    "statistics/truncated/histogram_{}.yaml",
)
_HISTOGRAM_FILE_PATTERNS = tuple(
    template.format("*") for template in _HISTOGRAM_FILE_TEMPLATES)


def _drop_orphaned_histogram_files(
    resource: GenomicResource,
    score: ScoreResource,
) -> None:
    """Delete the histogram files of score ids ``score`` no longer defines.

    A score removed from or renamed in ``scores:`` is never visited by the
    per-score reconciliation (gain#1309).  Ownership is matched against
    the names each defined id produces, never parsed out of a filename,
    and the listing is of the files present, not the stored manifest.  A
    ``.dvc`` pointer stands for the file it tracks, so an orphan whose
    blob is not pulled is still found and reported (gain#1732).
    """
    proto = resource.proto
    assert isinstance(proto, ReadWriteRepositoryProtocol)
    owned = {
        template.format(score_id)
        for template in _HISTOGRAM_FILE_TEMPLATES
        for score_id in score.score_definitions}
    present = {
        entry.name.removesuffix(DVC_SUFFIX)
        for entry in proto.collect_resource_entries(resource)}
    for name in sorted(present - owned):
        if any(fnmatch.fnmatchcase(name, pattern)
               for pattern in _HISTOGRAM_FILE_PATTERNS):
            drop_stale_histogram_file(resource, name)
