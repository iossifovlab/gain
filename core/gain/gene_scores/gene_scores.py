from __future__ import annotations

import warnings
from dataclasses import dataclass
from io import StringIO
from typing import Any, ClassVar, cast

import numpy as np
import pandas as pd

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.histogram import (
    CategoricalHistogramConfig,
    NullHistogramConfig,
    NumberHistogramConfig,
    build_default_histogram_conf,
    build_histogram_config,
)
from gain.genomic_resources.memo import Memo
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    get_base_resource_schema,
    get_required_filename_schema,
)
from gain.genomic_resources.score_resource import (
    ScoreDef,
    ScoreResource,
    refuse_unfoldable_histograms,
)
from gain.templates import get_template

logger = logging.getLogger(__name__)


# The shared ScoreDef holds the catalogue fields. Column addressing is a
# loading detail, so it lives here: the source column is read into the
# ``df`` column and later renamed to ``score_id``.
@dataclass
class GeneScoreDef(ScoreDef):
    """The definition of one score of a gene score resource.

    The loader builds one definition for each entry of the ``scores:``
    block of the resource.

    Attributes:
        score_id (str): The id of the score.

        value_type (str): The type of the values, such as ``float``.

        desc (str): The description of the score. It is empty when the
            resource gives none.

        small_values_desc (str | None): What a small value means, or
            ``None`` when the resource gives none.

        large_values_desc (str | None): What a large value means, or
            ``None`` when the resource gives none.

        hist_conf (HistogramConfig | None): The histogram configuration of
            the score.

        column_name (str): The name of the column of the data file that
            holds the score.
    """

    column_name: str


class GeneScore(ScoreResource[GeneScoreDef]):
    """The scores of a gene score resource, one value for each gene.

    The constructor reads the whole data file into memory. A gene score has
    no ``open`` and no ``close``. Build one with
    ``build_gene_score_from_resource_id`` or
    ``build_gene_score_from_resource``.

    A read with an unknown score id raises ``ValueError``, except
    ``get_gene_value``.
    """

    #: The noun that the ``n`` of a histogram counts: genes.
    HISTOGRAM_COUNT_UNIT: ClassVar[str] = "genes"

    def __init__(self, resource: GenomicResource) -> None:
        if resource.get_type() != "gene_score":
            logger.error(
                "invalid resource type for gene score %s",
                resource.resource_id)
            raise ValueError(f"invalid resource type {resource.resource_id}")

        super().__init__(resource)
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

        self.score_definitions: dict[str, GeneScoreDef] = {}

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

            self.score_definitions[score_conf["id"]] = GeneScoreDef(
                score_id=score_conf["id"],
                column_name=score_name,
                value_type=score_conf.get("type", "float"),

                desc=score_conf.get("desc", ""),

                hist_conf=hist_conf,
                small_values_desc=score_conf.get("small_values_desc"),
                large_values_desc=score_conf.get("large_values_desc"),
            )

        # Refused BEFORE any number histogram is auto-ranged below: the
        # min/max pass reads the column through ``float()``, and over a
        # text column that raised naming neither resource nor score
        # (gain#1308).  Same rule as a genomic score (gain#1336).
        refuse_unfoldable_histograms(
            self.score_definitions, self.resource.resource_id)

        self.df = self.df.rename(columns={
            score_def.column_name: score_def.score_id
            for score_def in self.score_definitions.values()
        })

        for score_def in self.score_definitions.values():
            hist_conf = score_def.hist_conf
            if isinstance(hist_conf, NumberHistogramConfig) and \
                    not hist_conf.has_view_range():
                min_value = self.get_min(score_def.score_id)
                max_value = self.get_max(score_def.score_id)
                hist_conf.view_range = (min_value, max_value)

        records = self.df.to_dict(orient="records")

        self.gene_values: dict[str, dict[str, float]] = {}

        for record in records:
            gene = record["gene"]
            self.gene_values[gene] = {
                score_id: record[score_id]
                for score_id in self.score_definitions
            }

    def get_min(self, score_id: str) -> float:
        """Return the smallest value of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        float
            The smallest value. Genes without a value do not count. It is
            ``nan`` when no gene has a value.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        self._guard_score_id(score_id)
        return float(self.df[score_id].min())

    def get_max(self, score_id: str) -> float:
        """Return the largest value of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        float
            The largest value. Genes without a value do not count. It is
            ``nan`` when no gene has a value.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        self._guard_score_id(score_id)
        return float(self.df[score_id].max())

    def get_values(self, score_id: str) -> list[Any]:
        """Return the values of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        list
            One value for each gene of the data file, in file order. A
            numeric score gives floats and a categorical score gives
            strings. A gene without a value gives ``nan``. The list is
            empty when the file has no genes.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        # The element type is not narrowed to float: a categorical string
        # score yields the raw string categories.
        self._guard_score_id(score_id)
        return list(self.df[score_id].values)

    def _get_number_hist_conf(
            self, score_id: str) -> NumberHistogramConfig | None:
        self._guard_score_id(score_id)
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
        """Return the scale of the X axis of the histogram of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        str | None
            ``"log"`` or ``"linear"``. ``None`` when the histogram of the
            score is not a number histogram.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        hist_conf = self._get_number_hist_conf(score_id)
        if hist_conf is None:
            return None
        if hist_conf.x_log_scale:
            return "log"
        return "linear"

    def get_y_scale(self, score_id: str) -> str | None:
        """Return the scale of the Y axis of the histogram of a score.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        str | None
            ``"log"`` or ``"linear"``. ``None`` when the histogram of the
            score is not a number histogram.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
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
        """Return the genes whose value of a score matches a condition.

        With ``values`` the condition is membership in that list. Without
        it, the condition is a range with both limits included.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.
        score_min : float | None
            The lower limit. ``None`` leaves the range open below.
        score_max : float | None
            The upper limit. ``None`` leaves the range open above.
        values : list[str] | None
            The values to select. Each item must convert to a float.
            When it is given, the function ignores ``score_min`` and
            ``score_max``.

        Returns
        -------
        set[str]
            The gene symbols that match. Genes without a value never
            match. The set is empty when no gene matches.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``, or if an item of
            ``values`` is not a number.
        """
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

    def to_dict(self, score_id: str) -> dict[str, float]:
        """Return the values of a score keyed by gene symbol.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        dict[str, float]
            The map from gene symbol to value. Genes without a value are
            left out. The map is empty when no gene has a value.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
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
        """Return the value of a score for one gene.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.
        gene_symbol : str
            The symbol of the gene.

        Returns
        -------
        float | None
            The value. ``None`` when the gene is not in the data file,
            when its value is missing, or when ``score_id`` is not a score
            of this resource. The function does not raise ``ValueError``
            for an unknown ``score_id``.
        """
        if gene_symbol not in self.gene_values:
            return None
        if score_id not in self.gene_values[gene_symbol]:
            return None

        value = self.gene_values[gene_symbol][score_id]

        if np.isnan(value):
            return None

        return value

    def to_tsv(self, score_id: str | None = None) -> list[str]:
        """Return the data of one score as lines of tab-separated text.

        Parameters
        ----------
        score_id : str | None
            The id of a score of this resource. The function needs it in
            practice: ``None`` fails an assertion.

        Returns
        -------
        list[str]
            The lines, each with its line ending. The first line is the
            header ``gene`` and ``score_id``. Genes without a value are
            left out.

        Raises
        ------
        AssertionError
            If ``score_id`` is ``None``.
        ValueError
            If the resource does not define ``score_id``.
        """
        df = None
        if score_id is not None:
            df = self.get_score_df(score_id)
        assert df is not None

        outbuf = StringIO()
        df.to_csv(outbuf, sep="\t", index=False)
        return outbuf.getvalue().splitlines(keepends=True)

    def build_score_help(self, score_id: str) -> str:
        """Return the Markdown help text for one score.

        The text has the id and the description of the score, the summary
        of the resource and a link to its info page. When the score has a
        histogram image, the text embeds the image by its public address.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        str
            The Markdown text.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        self._guard_score_id(score_id)
        score_def = self.score_definitions[score_id]

        histogram = get_template("score_histogram.jinja").render(
            hist_url=self.get_histogram_image_public_url(score_id),
            score_def=score_def,
        )

        data = {
            "name": score_def.score_id,
            "description": score_def.desc,
            "resource_id": self.resource.resource_id,
            "resource_summary": self.resource.get_summary(),
            "resource_url": f"{self.resource.get_public_url()}/index.html",
            "histogram": histogram,
        }
        return get_template("gene_score_help.jinja").render(data=data)

    def get_score_df(self, score_id: str) -> pd.DataFrame:
        """Return the values of a score as a table.

        Parameters
        ----------
        score_id : str
            The id of a score of this resource.

        Returns
        -------
        pandas.DataFrame
            The columns ``gene`` and ``score_id``. The rows without a
            value are dropped. The table is empty when no gene has a
            value.

        Raises
        ------
        ValueError
            If the resource does not define ``score_id``.
        """
        self._guard_score_id(score_id)
        return self.df[["gene", score_id]].dropna()

    @property
    def files(self) -> set[str]:
        """The names of the data files of the resource."""
        return {self.config["filename"]}

    @staticmethod
    def get_schema() -> dict[str, Any]:
        """Return the schema that validates the resource configuration.

        Returns
        -------
        dict
            The schema, in the Cerberus format.
        """
        return {
            **get_base_resource_schema(),
            **get_required_filename_schema(),
            "separator": {"type": "string"},
            "default_annotation": {
                "type": ["dict", "list"], "allow_unknown": True,
            },
            "gene_column": {"type": "string"},
            "scores": {"type": "list", "required": True, "schema": {
                "type": "dict",
                "schema": {
                    "id": {"type": "string", "required": True},
                    "name": {"type": "string"},
                    "column_name": {"type": "string"},
                    "type": {"type": "string"},
                    "desc": {"type": "string"},
                    "large_values_desc": {"type": "string"},
                    "small_values_desc": {"type": "string"},
                    "histogram": ScoreResource.histogram_schema(),
                },
            }},
        }


_INMEMORY_CACHE: Memo[tuple[str, str, str], GeneScore] = Memo()


def build_gene_score_from_resource(resource: GenomicResource) -> GeneScore:
    """Build the gene score of a genomic resource.

    The function reads the data file on the first call for a resource.
    Later calls with the same resource return the same object.

    Parameters
    ----------
    resource : GenomicResource
        A resource of type ``gene_score``.

    Returns
    -------
    GeneScore
        The gene score of the resource. It is ready to use.

    Raises
    ------
    ValueError
        If ``resource`` is ``None`` or is not of type ``gene_score``.
    """
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "gene_score":
        logger.error(
            "trying to open a resource %s of type "
            "%s as gene scores", resource.resource_id, resource.get_type())
        raise ValueError(f"invalid resource type: {resource.resource_id}")

    return _INMEMORY_CACHE.get_or_build(
        resource.get_memo_key(), lambda: GeneScore(resource))


def build_gene_score_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GeneScore:
    """Build the gene score of the resource with a given id.

    The function reads the data file on the first call for a resource.
    Later calls that find the same resource return the same object.

    Parameters
    ----------
    resource_id : str
        The id of a resource of type ``gene_score`` in the repository.
    grr : GenomicResourceRepo | None
        The repository that resolves ``resource_id``. When it is
        ``None``, the function builds the default repository with
        ``build_genomic_resource_repository()``.

    Returns
    -------
    GeneScore
        The gene score of the resource. It is ready to use.

    Raises
    ------
    FileNotFoundError
        If the repository has no resource with this id.
    ValueError
        If the resource is not of type ``gene_score``.
    """
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_gene_score_from_resource(grr.get_resource(resource_id))
