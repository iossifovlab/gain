from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import lru_cache
from io import StringIO
from threading import Lock
from typing import Any, cast
from urllib.parse import quote

import numpy as np
import pandas as pd

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    Histogram,
    HistogramConfig,
    NullHistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    build_default_histogram_conf,
    build_histogram_config,
    load_histogram,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
)
from gain.templates import get_template

logger = logging.getLogger(__name__)


@dataclass
class ScoreDef:
    """Class used to represent a gene score definition."""

    resource_id: str
    score_id: str
    column_name: str
    value_type: str

    desc: str

    hist_conf: HistogramConfig | None
    small_values_desc: str | None
    large_values_desc: str | None


class GeneScore(
    ResourceConfigValidationMixin,
):
    """Class used to represent gene scores."""

    def __init__(self, resource: GenomicResource) -> None:
        super().__init__()

        if resource.get_type() != "gene_score":
            logger.error(
                "invalid resource type for gene score %s",
                resource.resource_id)
            raise ValueError(f"invalid resource type {resource.resource_id}")

        self.resource = resource
        config = resource.get_config()
        if config is None:
            raise ValueError(
                f"genomic resource {resource.resource_id} not configured")
        self.config = self.validate_and_normalize_schema(config, resource)
        assert "filename" in self.config
        self.filename = self.config["filename"]

        compression = False
        data_filename = self.filename
        if data_filename.endswith(".gz"):
            compression = True
            data_filename = data_filename[:-len(".gz")]

        with resource.open_raw_file(
                self.filename, compression=compression) as file:
            sep = self.config.get("separator", None)
            if sep is None:
                sep = "\t" if data_filename.endswith(".tsv") else ","
            self.df = pd.read_csv(file, sep=sep)

        gene_column = self.config.get("gene_column", "gene")
        if gene_column != "gene":
            self.df = self.df.rename(columns={gene_column: "gene"})

        if self.config.get("scores") is None:
            raise ValueError(f"missing scores config in {resource.get_id()}")

        self.score_definitions: dict[str, ScoreDef] = {}

        for score_conf in self.config["scores"]:
            score_id = score_conf["id"]
            deprecated_name = score_conf.get("name", None)
            if deprecated_name is not None:
                logger.warning(
                    "The 'name' field in gene score definitions is "
                    "deprecated. Please use 'column_name' instead. "
                    "Resource: %s, score id: %s",
                    self.resource.resource_id, score_id)
                score_name = deprecated_name
            else:
                score_name = score_conf.get("column_name", score_id)
            hist_conf = build_histogram_config(score_conf)
            if hist_conf is None:
                hist_conf = build_default_histogram_conf(
                    score_conf.get("type", "float"))

            if not isinstance(
                    hist_conf,
                    NumberHistogramConfig | CategoricalHistogramConfig
                    | NullHistogramConfig):
                raise TypeError(
                    f"Missing histogram config for {score_id} in "
                    f"{self.resource.resource_id}")

            if isinstance(hist_conf, NumberHistogramConfig) and \
                    not hist_conf.has_view_range():
                min_value = self.get_min(score_name)
                max_value = self.get_max(score_name)
                hist_conf.view_range = (min_value, max_value)

            self.score_definitions[score_conf["id"]] = ScoreDef(
                resource_id=self.resource.resource_id,
                score_id=score_conf["id"],
                column_name=score_name,
                value_type=score_conf.get("type", "float"),

                desc=score_conf.get("desc", ""),

                hist_conf=hist_conf,
                small_values_desc=score_conf.get("small_values_desc"),
                large_values_desc=score_conf.get("large_values_desc"),
            )
        self.df = self.df.rename(columns={
            score_def.column_name: score_def.score_id
            for score_def in self.score_definitions.values()
        })
        records = self.df.to_dict(orient="records")

        self.gene_values: dict[str, dict[str, float]] = {}

        for record in records:
            gene = record["gene"]
            self.gene_values[gene] = {
                score_id: record[score_id]
                for score_id in self.score_definitions
            }

    def get_min(self, score_id: str) -> float:
        """Return minimal score value."""
        return float(self.df[score_id].min())

    def get_max(self, score_id: str) -> float:
        """Return maximal score value."""
        return float(self.df[score_id].max())

    def get_values(self, score_id: str) -> list[float]:
        """Return a list of score values."""
        return cast(list[float], list(self.df[score_id].values))

    def _get_number_hist_conf(
            self, score_id: str) -> NumberHistogramConfig | None:
        if score_id not in self.score_definitions:
            logger.warning("Score %s does not exist!", score_id)
            raise ValueError(
                f"unexpected score_id {score_id} for gene score "
                f"{self.resource.resource_id}")
        hist_conf = self.score_definitions[score_id].hist_conf
        if hist_conf is None:
            logger.warning(
                "histogram not configured for %s for gene score %s",
                score_id, self.resource.resource_id)
            return None
        if not isinstance(hist_conf, NumberHistogramConfig):
            return None
        return hist_conf

    def get_x_scale(self, score_id: str) -> str | None:
        """Return the scale type of the X axis."""
        hist_conf = self._get_number_hist_conf(score_id)
        if hist_conf is None:
            return None
        if hist_conf.x_log_scale:
            return "log"
        return "linear"

    def get_y_scale(self, score_id: str) -> str | None:
        """Return the scale type of the Y axis."""
        hist_conf = self._get_number_hist_conf(score_id)
        if hist_conf is None:
            return None
        if hist_conf.y_log_scale:
            return "log"
        return "linear"

    def get_genes(
        self, score_id: str,
        score_min: float | None = None,
        score_max: float | None = None,
        values: list[str] | None = None,
    ) -> set[str]:
        """Return set of genes for
        a score between a min and max value or
        genes with certain gene score values."""
        score_value_df = self.get_score_df(score_id)
        df = score_value_df[score_id]
        if values is None:
            if score_min is None:
                score_min = float("-inf")
            if score_max is None:
                score_max = float("inf")

            index = np.logical_and(
                df.to_numpy() >= score_min,
                df.to_numpy() <= score_max)
            index = np.logical_and(index, df.notna())
            genes = score_value_df[index].gene
        else:
            genes = score_value_df.loc[
                score_value_df[score_id].isin([float(v) for v in values])
            ].gene
        return set(genes.values)

    @lru_cache(maxsize=64)
    def get_all_scores(self) -> list[str]:
        return list(self.score_definitions.keys())

    def to_dict(self, score_id: str) -> dict[str, float]:
        """Return {gene_symbol: value} for a score, with NaN rows dropped."""
        df = self.get_score_df(score_id)
        return cast(
            dict[str, float],
            df.set_index("gene")[score_id].to_dict())

    def _to_dict(self, score_id: str) -> dict[str, Any]:
        """Return dictionary of all defined scores keyed by gene symbol.

        .. deprecated::
            Use the public :meth:`to_dict` instead. Retained as a thin
            compatibility alias because gpf still calls it across the repo
            boundary until iossifovlab/gpf#983 switches to ``to_dict``;
            removing it before then would break that live consumer.
        """
        warnings.warn(
            "GeneScore._to_dict is deprecated; use the public "
            "GeneScore.to_dict instead. It is retained only until "
            "gpf#983 migrates off it.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.to_dict(score_id)

    def get_gene_value(
        self, score_id: str, gene_symbol: str,
    ) -> float | None:
        """Return the value for a given gene symbol."""
        if gene_symbol not in self.gene_values:
            return None
        if score_id not in self.gene_values[gene_symbol]:
            return None

        value = self.gene_values[gene_symbol][score_id]

        if np.isnan(value):
            return None

        return value

    def to_tsv(self, score_id: str | None = None) -> list[str]:
        """Return a TSV version of the gene score data."""
        df = None
        if score_id is not None:
            df = self.get_score_df(score_id)
        assert df is not None

        outbuf = StringIO()
        df.to_csv(outbuf, sep="\t", index=False)
        return outbuf.getvalue().splitlines(keepends=True)

    def get_score_df(self, score_id: str) -> pd.DataFrame:
        return self.df[["gene", score_id]].dropna()

    @property
    def files(self) -> set[str]:
        return {self.config["filename"]}

    @staticmethod
    def get_schema() -> dict[str, Any]:
        return {
            **get_base_resource_schema(),
            "filename": {"type": "string"},
            "separator": {"type": "string"},
            "default_annotation": {
                "type": ["dict", "list"], "allow_unknown": True,
            },
            "gene_column": {"type": "string"},
            "scores": {"type": "list", "schema": {
                "type": "dict",
                "schema": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "column_name": {"type": "string"},
                    "type": {"type": "string"},
                    "desc": {"type": "string"},
                    "large_values_desc": {"type": "string"},
                    "small_values_desc": {"type": "string"},
                    "histogram": {"type": "dict", "schema": {
                        "type": {
                            "type": "string",
                            "allowed": ["number", "categorical", "null"],
                            "required": True,
                        },
                        "plot_function": {"type": "string"},
                        "number_of_bins": {
                            "type": "number",
                            "dependencies": {"type": "number"},
                        },
                        "view_range": {"type": "dict", "schema": {
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                        }, "dependencies": {"type": "number"}},
                        "x_log_scale": {
                            "type": "boolean",
                            "dependencies": {"type": "number"},
                        },
                        "y_log_scale": {
                            "type": "boolean",
                            "dependencies": {"type": ["number", "categorical"]},
                        },
                        "x_min_log": {
                            "type": "number",
                            "dependencies": {"type": ["number", "categorical"]},
                        },
                        "label_rotation": {
                            "type": "integer",
                            "dependencies": {"type": "categorical"},
                        },
                        "value_order": {
                            "type": "list",
                            "schema": {"type": ["string", "integer"]},
                            "dependencies": {"type": "categorical"},
                        },
                        "displayed_values_count": {
                            "type": "integer",
                            "dependencies": {"type": "categorical"},
                        },
                        "displayed_values_percent": {
                            "type": "number",
                            "dependencies": {"type": "categorical"},
                        },
                        "reason": {
                            "type": "string",
                            "dependencies": {"type": "null"},
                        },
                    }},
                },
            }},
        }

    def _guard_score_id(self, score_id: str) -> None:
        """Raise if ``score_id`` is not a defined score.

        Guards on ``score_definitions`` rather than the ``lru_cache``d
        ``get_all_scores()`` so it does not depend on a memoisation that
        #301 removes. Kept identical to the genomic-score sibling so #301
        can lift a single implementation into the shared base.
        """
        if score_id not in self.score_definitions:
            raise ValueError(
                f"unknown score {score_id}; "
                f"available scores are {list(self.score_definitions.keys())}")

    @lru_cache(maxsize=64)
    def get_score_range(
            self, score_id: str) -> tuple[float, float] | None:
        """Return the value range for a numeric score."""
        self._guard_score_id(score_id)
        hist = self.get_score_histogram(score_id)
        if isinstance(hist, NumberHistogram):
            return (hist.min_value, hist.max_value)
        return None

    def get_histogram_filename(self, score_id: str) -> str:
        """Return the histogram filename for a gene score."""
        self._guard_score_id(score_id)
        filename = f"statistics/histogram_{score_id}.yaml"
        if filename in self.resource.get_manifest():
            return filename
        return f"statistics/histogram_{score_id}.json"

    @lru_cache(maxsize=64)
    def get_score_histogram(self, score_id: str) -> Histogram:
        """Return defined histogram for a score.

        Gene scores may declare a categorical (or null) histogram just as
        readily as a numeric one, so the honest return type is the full
        ``Histogram`` union. Callers that need numeric-only attributes
        (``bars``/``bins``/``min_value``/...) must narrow with
        ``isinstance(hist, NumberHistogram)`` first.
        """
        hist_filename = self.get_histogram_filename(score_id)
        return load_histogram(self.resource, hist_filename)

    def get_histogram_image_filename(self, score_id: str) -> str:
        return f"statistics/histogram_{score_id}.png"

    def _histogram_image_url(self, score_id: str, repo_url: str) -> str:
        return (
            f"{repo_url}/"
            f"{quote(self.get_histogram_image_filename(score_id))}"
        )

    def get_histogram_image_url(self, score_id: str) -> str | None:
        return self._histogram_image_url(
            score_id, self.resource.get_url())

    def get_histogram_image_public_url(self, score_id: str) -> str:
        """Return the histogram image URL on the resource's public mirror.

        Unlike :meth:`get_histogram_image_url`, this is built from the
        resource's public URL so it is reachable from a browser even when
        the GRR is a local directory repository.
        """
        return self._histogram_image_url(
            score_id, self.resource.get_public_url())


@dataclass
class ScoreDesc:
    """Class used to represent a score description."""

    resource_id: str
    score_id: str
    column_name: str
    value_type: str

    hist: Histogram
    description: str
    help: str
    small_values_desc: str | None
    large_values_desc: str | None


def _build_gene_score_help(
    score_def: ScoreDef,
    gene_score: GeneScore,
) -> str:
    score_id = score_def.score_id
    hist_url = gene_score.get_histogram_image_public_url(score_id)
    assert score_def is not None

    histogram = get_template("score_histogram.jinja").render(
        hist_url=hist_url,
        score_def=score_def,
    )

    data = {
        "name": score_def.score_id,
        "description": score_def.desc,
        "resource_id": gene_score.resource.resource_id,
        "resource_summary": gene_score.resource.get_summary(),
        "resource_url": f"{gene_score.resource.get_public_url()}/index.html",
        "histogram": histogram,
    }
    return get_template("gene_score_help.jinja").render(data=data)


class GeneScoresDb:
    """
    Helper class used to load all defined gene scores.

    Used by Web interface.
    """

    def __init__(self, gene_scores: list[GeneScore]):
        super().__init__()
        self.score_descs = {}
        self.gene_scores = {}
        for gene_score in gene_scores:
            self.gene_scores[gene_score.resource.get_id()] = gene_score
            for score_desc in GeneScoresDb.build_descs_from_score(gene_score):
                self.score_descs[score_desc.score_id] = score_desc

    @staticmethod
    def build_descs_from_score(
        gene_score: GeneScore,
    ) -> list[ScoreDesc]:
        """Build score descriptions from score."""
        result = []
        for score_id, score_def in gene_score.score_definitions.items():
            help_doc = _build_gene_score_help(score_def, gene_score)
            result.append(ScoreDesc(
                resource_id=gene_score.resource.resource_id,
                score_id=score_id,
                column_name=score_def.column_name,
                value_type=score_def.value_type,
                hist=gene_score.get_score_histogram(score_id),
                description=score_def.desc,
                help=help_doc,
                small_values_desc=score_def.small_values_desc,
                large_values_desc=score_def.large_values_desc,
            ))
        return result

    def get_score_ids(self) -> list[str]:
        """Return a list of the IDs of all the gene scores contained."""
        return sorted(self.score_descs.keys())

    def get_gene_score_ids(self) -> list[str]:
        """Return a list of the IDs of all the gene scores contained."""
        return sorted(self.gene_scores.keys())

    def get_gene_scores(self) -> list[GeneScore]:
        """Return a list of all the gene scores contained in the DB."""
        return list(self.gene_scores.values())

    def get_scores(self) -> list[ScoreDesc]:
        return list(self.score_descs.values())

    def get_gene_score(self, score_id: str) -> GeneScore | None:
        """Return a given gene score."""
        if score_id not in self.gene_scores:
            return None
        assert self.gene_scores[score_id].df is not None
        return self.gene_scores[score_id]

    def get_score_desc(self, score_id: str) -> ScoreDesc | None:
        if score_id not in self.score_descs:
            return None
        return self.score_descs[score_id]

    def __getitem__(self, score_id: str) -> ScoreDesc:
        if score_id not in self.score_descs:
            raise ValueError(f"score {score_id} not found")

        return self.score_descs[score_id]

    def __contains__(self, score_id: str) -> bool:
        return score_id in self.score_descs

    def __len__(self) -> int:
        return len(self.score_descs)


_INMEMORY_CACHE: dict[tuple[str, str], GeneScore] = {}
_INMEMORY_CACHE_LOCK = Lock()


def build_gene_score_from_resource(resource: GenomicResource) -> GeneScore:
    """Load gene score from a genomic resource."""
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "gene_score":
        logger.error(
            "trying to open a resource %s of type "
            "%s as gene scores", resource.resource_id, resource.get_type())
        raise ValueError(f"invalid resource type: {resource.resource_id}")

    cache_id = (resource.get_full_id(), resource.get_repo_url())
    with _INMEMORY_CACHE_LOCK:
        if cache_id in _INMEMORY_CACHE:
            return _INMEMORY_CACHE[cache_id]

        gene_score = GeneScore(resource)
        _INMEMORY_CACHE[cache_id] = gene_score
        return gene_score


def build_gene_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GeneScore:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_gene_score_from_resource(grr.get_resource(resource_id))
