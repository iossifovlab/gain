"""The catalogue plane shared by gene scores and genomic scores.

``ScoreResource`` is the one base both families extend.  It owns *only* the
things that mean the same for a gene score and a genomic score: the score
definitions, the two ways to enumerate/look them up, and the histogram
accessors.  It deliberately owns nothing about a resource's *lifecycle* (open/
close), its *table*, its *fetch* surface, its *chromosomes* or its
*aggregators* -- those belong to genomic scores alone, which are keyed by
position and read over a region, whereas gene scores are keyed by gene symbol,
have no open/close and nothing to aggregate.

The boundary is not incidental; it is the whole reason this module exists.  See
``docs/2026-07-14-gain-score-abstraction.html`` for the design, and
``tests/small/genomic_resources/test_score_resource_api.py`` for the guard that
keeps a lifecycle/table/fetch/aggregator method from being lifted into this
base "because both subclasses happen to have one".

The location is deliberate too: ``gene_scores`` already imports
``genomic_resources.{histogram,repository,resource_implementation}``, so living
here adds **zero** new dependency edges.  A top-level ``gain/scores/`` package
would instead create a ``genomic_resources`` <-> ``scores`` import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from gain.genomic_resources.histogram import (
    Histogram,
    HistogramConfig,
    NumberHistogram,
    load_histogram,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
)


@dataclass
class ScoreDef:
    """Catalogue-plane fields common to a gene score and a genomic score.

    Column addressing is intentionally **not** here: it is a loading detail
    that differs per family (a VCF keys a score by INFO name, a bigWig has no
    header, a gene score renames a pandas column), so it lives on the concrete
    ``GenomicScoreDef`` / ``GeneScoreDef`` subclasses instead.
    """

    score_id: str
    value_type: str

    desc: str

    small_values_desc: str | None
    large_values_desc: str | None

    hist_conf: HistogramConfig | None


class ScoreResource[ScoreDefT: ScoreDef](ResourceConfigValidationMixin):
    """Shared catalogue base for gene and genomic score resources.

    Parameterised by the concrete score-definition type so that
    ``get_score_definition`` returns the right kind for each family (a
    ``GenomicScoreDef`` for genomic scores, a ``GeneScoreDef`` for gene
    scores) without either side having to override it.

    A concrete subclass must set two attributes in its own ``__init__``:

    * ``resource`` -- the underlying :class:`GenomicResource`, from which the
      histogram accessors read the manifest and public URLs;
    * ``score_definitions`` -- the ``score_id -> definition`` mapping.

    Everything a subclass may add on top of this (a table, an open/close
    lifecycle, fetch methods, aggregators) is its own concern and must NOT be
    lifted here -- see the module docstring and the API-surface guard test.
    """

    # An abstract intermediate base: it is never instantiated directly, and
    # the abstract ``get_schema`` inherited from ResourceConfigValidationMixin
    # is provided by each concrete family (GenomicScore, GeneScore), not here.
    # pylint: disable=abstract-method

    resource: GenomicResource
    score_definitions: dict[str, ScoreDefT]

    def get_all_scores(self) -> list[str]:
        return list(self.score_definitions)

    def get_score_definition(self, score_id: str) -> ScoreDefT | None:
        return self.score_definitions.get(score_id)

    def _guard_score_id(self, score_id: str) -> None:
        """Raise if ``score_id`` is not a defined score.

        Guards on ``score_definitions`` directly -- the shared, canonical
        source of what scores exist -- so it depends on no memoisation.
        """
        if score_id not in self.score_definitions:
            raise ValueError(
                f"unknown score {score_id}; "
                f"available scores are {list(self.score_definitions.keys())}")

    def get_score_range(
            self, score_id: str) -> tuple[float, float] | None:
        """Return the value range for a numeric score."""
        self._guard_score_id(score_id)
        hist = self.get_score_histogram(score_id)
        if isinstance(hist, NumberHistogram):
            return (hist.min_value, hist.max_value)
        return None

    def get_histogram_filename(self, score_id: str) -> str:
        """Return the histogram filename for a score."""
        self._guard_score_id(score_id)
        filename = f"statistics/histogram_{score_id}.yaml"
        if filename in self.resource.get_manifest():
            return filename
        return f"statistics/histogram_{score_id}.json"

    def get_score_histogram(self, score_id: str) -> Histogram:
        """Return defined histogram for a score.

        A score may declare a categorical (or null) histogram just as readily
        as a numeric one, so the honest return type is the full ``Histogram``
        union.  Callers that need numeric-only attributes
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

    @staticmethod
    def histogram_schema() -> dict[str, Any]:
        """The ``histogram`` config-schema fragment shared by both families.

        Contributed into each family's ``get_schema()`` instead of pasted into
        both -- the two blocks used to be byte-identical modulo line-wrapping.
        Built fresh on every call so a caller that mutates the returned schema
        (e.g. a ``copy.deepcopy`` then in-place edit) cannot affect another.
        """
        return {"type": "dict", "schema": {
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
        }}
