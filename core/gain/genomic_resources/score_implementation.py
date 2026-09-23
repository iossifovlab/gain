"""The implementation plane shared by gene scores and genomic scores.

``ScoreImplementationBase`` sits one layer above ``ScoreResource`` (the
catalogue plane in :mod:`gain.genomic_resources.score_resource`): where that
base owns what a score *is*, this base owns what a score *implementation* does
that means the same for both families -- contributing ``score_ids`` /
``score_descriptions`` into the FTS index, and serialising-and-plotting a
computed histogram into the resource.

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
from typing import Any

from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    Histogram,
    NullHistogram,
    drop_stale_histogram_file,
    plot_histogram,
    truncated_histogram_filename,
)
from gain.genomic_resources.repository import (
    GR_INDEX_SCORE_FIELDS,
    GenomicResource,
    ReadWriteRepositoryProtocol,
)
from gain.genomic_resources.resource_implementation import (
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
    search index and the histogram save-and-plot loop.
    """

    score: ScoreResource

    @abstractmethod
    def create_statistics_build_tasks(
        self, **kwargs: Any,
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

    @staticmethod
    def _save_and_plot_histograms(
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
        file of a score id ``score_definitions`` no longer holds.
        """
        proto = resource.proto
        for score_id, histogram in histograms.items():
            hist_filename = score.get_histogram_filename(score_id)
            with proto.open_raw_file(
                resource,
                hist_filename,
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize())
            sidecar_filename = truncated_histogram_filename(hist_filename)
            if (
                isinstance(histogram, CategoricalHistogram)
                and histogram.unique_values
                    > CategoricalHistogram.UNIQUE_VALUES_LIMIT
            ):
                with proto.open_raw_file(
                    resource,
                    sidecar_filename,
                    mode="wt",
                ) as outfile:
                    outfile.write(histogram.serialize_truncated())
            else:
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
            hist_filename = score.get_histogram_filename(score_id)
            for filename in (
                hist_filename,
                truncated_histogram_filename(hist_filename),
                score.get_histogram_image_filename(score_id),
            ):
                drop_stale_histogram_file(resource, filename)
        _drop_orphaned_histogram_files(resource, score)


#: Every name a score-histogram file takes, with ``{}`` for the score id:
#: both serialisations (legacy ``.yaml``, current ``.json``), their
#: truncated sidecars, and the image.  No other statistic writes under
#: ``statistics/histogram_``.
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
    and the listing is of the files present, not the stored manifest.
    """
    proto = resource.proto
    assert isinstance(proto, ReadWriteRepositoryProtocol)
    owned = {
        template.format(score_id)
        for template in _HISTOGRAM_FILE_TEMPLATES
        for score_id in score.score_definitions}
    for entry in proto.collect_resource_entries(resource):
        if entry.name in owned or not any(
                fnmatch.fnmatchcase(entry.name, pattern)
                for pattern in _HISTOGRAM_FILE_PATTERNS):
            continue
        drop_stale_histogram_file(resource, entry.name)
