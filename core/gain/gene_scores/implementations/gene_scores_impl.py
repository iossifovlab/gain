from __future__ import annotations

import json
import math
from typing import Any, ClassVar

import numpy as np

from gain import logging
from gain.gene_scores.gene_scores import (
    GeneScore,
    build_gene_score_from_resource,
)
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.histogram import (
    CategoricalHistogram,
    CategoricalHistogramConfig,
    HistogramError,
    NullHistogram,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
)
from gain.genomic_resources.score_implementation import (
    ScoreImplementationBase,
)
from gain.task_graph.graph import TaskDesc, TaskGraph

logger = logging.getLogger(__name__)


class GeneScoreImplementation(ScoreImplementationBase):
    """Class used to represent gene score resource implementations."""

    def __init__(self, resource: GenomicResource) -> None:
        super().__init__(resource)
        self.score: GeneScore = build_gene_score_from_resource(
            resource,
        )

    template_name: ClassVar[str] = "gene_score.jinja"
    styles_template_name: ClassVar[str] = "gene_score_styles.jinja"

    def _get_template_data(self) -> dict[str, Any]:
        data = {}
        data["gene_score"] = self.score
        return data

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    def create_statistics_build_tasks(
        self,
        **kwargs: Any,  # noqa: ARG002
    ) -> list[TaskDesc]:
        create_task = TaskGraph.make_task(
            f"{self.resource.resource_id}_build_histograms",
            self._build_histograms,
            args=[self.resource],
            deps=[],
        )
        return [create_task]

    @staticmethod
    def _build_histograms(
        resource: GenomicResource,
    ) -> dict[str, NumberHistogram | CategoricalHistogram | NullHistogram]:
        histograms: dict[
            str, NumberHistogram | CategoricalHistogram | NullHistogram] = {}
        gene_score = build_gene_score_from_resource(resource)
        for score_id in gene_score.score_definitions:
            histogram: (
                NumberHistogram | CategoricalHistogram | NullHistogram | None
            )
            # A runtime histogram-build failure is recorded as a serialized
            # NullHistogram carrying the reason, matching the genomic score
            # implementation. HistogramError is a BaseException, so it is
            # caught explicitly here; a plain ``except ValueError`` or
            # ``except Exception`` would let it escape and fail the task.
            try:
                histogram = GeneScoreImplementation._calc_histogram(
                    gene_score, score_id)
            except (ValueError, TypeError, HistogramError) as e:
                logger.warning(
                    "Histogram for score %s in %s nullified: %s",
                    score_id, resource.resource_id, e,
                )
                histogram = NullHistogram(NullHistogramConfig(str(e)))

            if histogram is None:
                logger.warning(
                    "Gene score %s in %s has no histogram config!",
                    score_id, resource.resource_id,
                )
                continue

            histograms[score_id] = histogram

        GeneScoreImplementation._save_and_plot_histograms(
            resource, gene_score, histograms)
        return histograms

    @staticmethod
    def _calc_histogram(
        gene_score: GeneScore, score_id: str,
    ) -> NumberHistogram | CategoricalHistogram | None:
        if score_id not in gene_score.score_definitions:
            raise ValueError(
                f"Score ID {score_id} not found in gene score definitions")
        score_def = gene_score.score_definitions.get(score_id)
        assert score_def is not None
        hist_conf = score_def.hist_conf
        if hist_conf is None or isinstance(hist_conf, NullHistogramConfig):
            return None
        histogram: NumberHistogram | CategoricalHistogram

        if isinstance(hist_conf, NumberHistogramConfig):
            histogram = NumberHistogram(hist_conf)
            for value in gene_score.get_values(score_id):
                histogram.add_value(value)
        elif isinstance(hist_conf, CategoricalHistogramConfig):
            histogram = CategoricalHistogram(hist_conf)
            for value in gene_score.get_values(score_id):
                # Categorical values pass through untouched -- int()/isnan
                # coercion is invalid for string categories (#352). Missing
                # values are NaN floats (or None) regardless of the column's
                # declared type, so filter them without assuming a number.
                if value is None:
                    continue
                if isinstance(value, float) and math.isnan(value):
                    continue
                # get_values yields numpy scalars for numeric columns;
                # CategoricalHistogram.add_value accepts only native str/int,
                # so normalize numpy -> python (this is what int() used to do
                # for the int-valued case).
                if isinstance(value, np.generic):
                    value = value.item()
                histogram.add_value(value)
        else:
            raise TypeError(f"Unknown histogram config: {hist_conf}")
        return histogram

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        manifest = self.resource.get_manifest()
        config = self.get_config()
        score_filename = config["filename"]
        return json.dumps({
            "score_config": [
                {
                    "id": score_def.score_id,
                    "hist_conf": score_def.hist_conf.to_dict()
                    if score_def.hist_conf else "null",
                }
                for score_def in self.score.score_definitions.values()
            ],
            "score_file": manifest[score_filename].md5,
        }, sort_keys=True, indent=2).encode()
