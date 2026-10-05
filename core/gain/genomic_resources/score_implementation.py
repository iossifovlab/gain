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
import logging
from abc import abstractmethod

from gain.genomic_resources.dvc import DVC_SUFFIX, dvc_sidecar_target
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

logger = logging.getLogger(__name__)


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
    file of a score id ``score_definitions`` no longer holds, the
    gzipped full histogram (``histogram_<id>.json.gz``) included.

    A categorical histogram past ``UNIQUE_VALUES_LIMIT`` is written as
    deterministic gzipped JSON next to its plain truncated sidecar; every
    other histogram as plain JSON (ADR 0032).  A written ``.json.gz``
    with no ``.dvc`` pointer beside it is reported with a warning that
    names the ``dvc add`` to run; gain never runs DVC.  The full
    histogram an earlier build left in the other encoding is deleted, so
    neither a release that reads only the plain encoding nor a
    DVC-tracked sidecar that cannot be dropped serves it as current --
    except a legacy
    ``histogram_<id>.yaml``, which the sidecar is named after and so
    must stay for the sidecar to be found.  A DVC-tracked ``.json.gz``
    beside a DVC-tracked sidecar is kept too, and still loaded, until
    the curator runs ``dvc remove``.  The encoding, and so
    which file is stale, is decided from the histogram in hand.

    Every deletion goes through :func:`drop_stale_histogram_file`: a
    DVC-tracked file is reported and left in place.
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
            gzipped_filename = score.get_gzipped_histogram_filename(score_id)
            with proto.open_raw_file(
                resource,
                gzipped_filename,
                mode="wb",
            ) as outfile:
                outfile.write(histogram.serialize_gzipped())
            _warn_unless_dvc_tracked(resource, gzipped_filename)
            with proto.open_raw_file(
                resource,
                sidecar_filename,
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize_truncated())
            # A plain full histogram from an earlier build within the
            # limit would otherwise be loaded as current by releases
            # that read only the plain encoding.  A legacy ``.yaml`` one
            # is kept: the sidecar just written is named after it, and
            # without it readers derive ``.json`` names that do not exist.
            plain_filename = score.get_plain_histogram_filename(score_id)
            if not plain_filename.endswith(".yaml"):
                drop_stale_histogram_file(resource, plain_filename)
        else:
            with proto.open_raw_file(
                resource,
                score.get_plain_histogram_filename(score_id),
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize())
            # A sidecar from an earlier build whose histogram has since
            # shrunk below the limit (or stopped being categorical)
            # would otherwise be served as current by truncated= loads,
            # and its gzipped full histogram by full loads whenever the
            # sidecar is DVC-tracked and so kept.  When the ``.json.gz``
            # is DVC-tracked too, both are kept with a warning and full
            # loads still pick it until the curator runs ``dvc remove``
            # (ADR 0032).
            drop_stale_histogram_file(resource, sidecar_filename)
            drop_stale_histogram_file(
                resource, score.get_gzipped_histogram_filename(score_id))
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
            score.get_gzipped_histogram_filename(score_id),
            score.get_truncated_histogram_filename(score_id),
            score.get_histogram_image_filename(score_id),
        ):
            drop_stale_histogram_file(resource, filename)
    _drop_orphaned_histogram_files(resource, score)


def _warn_unless_dvc_tracked(
    resource: GenomicResource, filename: str,
) -> None:
    """Ask the curator to ``dvc add`` a file with no ``.dvc`` pointer.

    gain never runs DVC (ADR 0032).  Only the pointer is consulted: a file
    that ``.gitignore`` alone covers is left out of the manifest, so its
    full loads fail, and it is reported like any other.
    """
    if resource.proto.file_exists(resource, f"{filename}{DVC_SUFFIX}"):
        return
    logger.warning(
        "<%s> of resource <%s> is not DVC-tracked; run 'dvc add %s'",
        filename, resource.resource_id, filename)


#: The score-histogram file names the orphan sweep reconciles, with ``{}``
#: for the score id: the full histogram in every encoding (legacy
#: ``.yaml``, plain ``.json``, gzipped ``.json.gz``), the truncated
#: sidecars, and the image.  No other statistic writes under
#: ``statistics/histogram_``.
_HISTOGRAM_FILE_TEMPLATES = (
    "statistics/histogram_{}.json",
    "statistics/histogram_{}.json.gz",
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
        dvc_sidecar_target(entry.name)
        for entry in proto.collect_resource_entries(resource)}
    for name in sorted(present - owned):
        if any(fnmatch.fnmatchcase(name, pattern)
               for pattern in _HISTOGRAM_FILE_PATTERNS):
            drop_stale_histogram_file(resource, name)
