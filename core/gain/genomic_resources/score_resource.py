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
    NUMBER_HISTOGRAM_VALUE_TYPES,
    Histogram,
    HistogramConfig,
    NumberHistogram,
    NumberHistogramConfig,
    load_histogram,
    truncated_histogram_filename,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import (
    HistogramError,
    score_configuration_error,
)
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


def refuse_unfoldable_histograms[ScoreDefT: ScoreDef](
    score_defs: dict[str, ScoreDefT], resource_id: str,
) -> dict[str, ScoreDefT]:
    """Refuse a configured NUMBER histogram no value of the score can feed.

    A ``histogram: {type: number}`` over a score whose value type is not one
    a number histogram accumulates
    (:data:`~gain.genomic_resources.histogram.NUMBER_HISTOGRAM_VALUE_TYPES`)
    is a config stating something the score cannot do, so gain#1336 raises
    on it rather than working around it, naming the resource and the score.

    It is a fact about a score DEFINITION -- the ``value_type`` and
    ``hist_conf`` every :class:`ScoreDef` carries -- so it lives here, on
    the base both families share, and each family calls it once at its
    own construction point (gain#1308): the caller decides *when* (after
    the value type is final, before anything reads the column), and says
    why there.  Raising at CONSTRUCTION rather than where the statistics
    build reads the configs is what makes the refusal reach every
    consumer: annotation never unpacks score definitions.

    Two things it does not refuse.  A definition with ``hist_conf=None``
    is skipped -- the default chosen for its type later never pairs a
    non-numeric type with a number histogram.  And a CATEGORICAL histogram
    over a number is deliberately let through: it folds one value at a
    time and nullifies just that score, which is a fact about a value
    rather than about the config.
    """
    for score_id, score_def in score_defs.items():
        if not isinstance(score_def.hist_conf, NumberHistogramConfig):
            continue
        if score_def.value_type in NUMBER_HISTOGRAM_VALUE_TYPES:
            continue
        raise score_configuration_error(
            resource_id, score_id,
            f"has value type {score_def.value_type!r}, which a number "
            f"histogram cannot accumulate; give the score a categorical "
            f"histogram ('histogram: {{type: categorical}}') or no "
            f"histogram at all")
    return score_defs


class ScoreResource[ScoreDefT: ScoreDef](ResourceConfigValidationMixin):
    """Shared catalogue base for gene and genomic score resources.

    Parameterised by the concrete score-definition type so that
    ``get_score_definition`` returns the right kind for each family (a
    ``GenomicScoreDef`` for genomic scores, a ``GeneScoreDef`` for gene
    scores) without either side having to override it.

    Constructing one keeps the resource and validates its config against the
    family's ``get_schema()``, so ``config`` is the normalized document. A
    concrete subclass calls ``super().__init__(resource)`` and then sets
    ``score_definitions`` -- the ``score_id -> definition`` mapping -- itself.

    Everything a subclass may add on top of this (a table, an open/close
    lifecycle, fetch methods, aggregators) is its own concern and must NOT be
    lifted here -- see the module docstring and the API-surface guard test.
    """

    # An abstract intermediate base: it is never instantiated directly, and
    # the abstract ``get_schema`` inherited from ResourceConfigValidationMixin
    # is provided by each concrete family (GenomicScore, GeneScore), not here.
    # pylint: disable=abstract-method

    resource: GenomicResource
    config: dict[str, Any]
    score_definitions: dict[str, ScoreDefT]

    def __init__(self, resource: GenomicResource) -> None:
        self.resource = resource
        self.config = self.validate_and_normalize_schema(
            resource.get_config(), resource)

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

    def get_score_histogram(
        self, score_id: str, *, truncated: bool = False,
    ) -> Histogram:
        """Return defined histogram for a score.

        A score may declare a categorical (or null) histogram just as readily
        as a numeric one, so the honest return type is the full ``Histogram``
        union.  Callers that need numeric-only attributes
        (``bars``/``bins``/``min_value``/...) must narrow with
        ``isinstance(hist, NumberHistogram)`` first.

        With ``truncated=True`` a truncated sidecar is acceptable: when one
        exists (categorical histograms past ``UNIQUE_VALUES_LIMIT`` have
        one), it is returned instead of the full histogram, so the caller
        never reads the full values file.  Without a sidecar the full
        histogram is returned either way.
        """
        hist_filename = self.get_histogram_filename(score_id)
        sidecar_filename = truncated_histogram_filename(hist_filename)
        if sidecar_filename in self.resource.get_manifest():
            if truncated:
                # A summary is acceptable, never required: a manifested
                # sidecar whose file is unreadable falls back to the full
                # histogram instead of degrading to a NullHistogram.
                if self.resource.file_exists(sidecar_filename):
                    return load_histogram(self.resource, sidecar_filename)
                return load_histogram(self.resource, hist_filename)
            try:
                full_exists = self.resource.file_exists(hist_filename)
            except Exception as exc:
                # On caching protocols the existence probe itself fetches
                # the file, so a missing remote blob surfaces here.
                raise HistogramError(
                    f"full histogram <{hist_filename}> of resource "
                    f"<{self.resource.resource_id}> could not be read "
                    f"while its truncated sidecar exists; pull the "
                    f"resource data (dvc pull) or load with "
                    f"truncated=True",
                ) from exc
            if not full_exists:
                # A sidecar without its full file is a DVC-tracked
                # histogram whose blob is not pulled -- not a resource
                # without statistics.
                raise HistogramError(
                    f"full histogram <{hist_filename}> of resource "
                    f"<{self.resource.resource_id}> is absent while its "
                    f"truncated sidecar exists; pull the resource data "
                    f"(dvc pull) or load with truncated=True",
                )
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
