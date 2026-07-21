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

from abc import abstractmethod
from typing import Any

from gain.genomic_resources.histogram import (
    Histogram,
    plot_histogram,
)
from gain.genomic_resources.repository import GenomicResource
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
            (*header, "score_ids", "score_descriptions"),
            (*row, score_ids, score_descriptions),
        )

    @staticmethod
    def _save_and_plot_histograms(
        resource: GenomicResource,
        score: ScoreResource,
        histograms: dict[str, Histogram],
    ) -> None:
        """Serialise each histogram into the resource and render its PNG.

        ``plot_histogram`` is a no-op for a ``NullHistogram``, so both families
        can hand it the full histogram mapping without pre-filtering.
        """
        proto = resource.proto
        for score_id, histogram in histograms.items():
            with proto.open_raw_file(
                resource,
                score.get_histogram_filename(score_id),
                mode="wt",
            ) as outfile:
                outfile.write(histogram.serialize())
            score_def = score.score_definitions[score_id]
            plot_histogram(
                resource,
                score.get_histogram_image_filename(score_id),
                histogram,
                score_id,
                score_def.small_values_desc,
                score_def.large_values_desc,
            )
