"""The base every genomic score annotator extends.

``GenomicScoreAnnotatorBase`` binds an annotator to one
:class:`~gain.genomic_resources.genomic_scores.GenomicScore` and answers
the questions the pipeline asks of any annotator -- attribute specs,
defaults, help text -- from the score's own definitions.  The kinds,
``position_score_annotator`` and ``allele_score_annotator``, live one
per module beside this one and add the read.

``get_genomic_resource`` is the shared helper both kinds resolve their
``resource_id`` through; it is public because two sibling modules import
it.
"""
import abc
from typing import Any

from gain import logging
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotatorInfo,
    Attribute,
    AttributeConfig,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.aggregators import (
    AggregatorSource,
    aggregator_name,
)
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_types import reject_retired_resource
from gain.templates import get_template

logger = logging.getLogger(__name__)


def get_genomic_resource(
        pipeline: AnnotationPipeline, info: AnnotatorInfo,
        resource_types: set[str]) -> GenomicResource:
    """Return genomic score resource used for given genomic score annotator."""
    if "resource_id" not in info.parameters:
        raise ValueError(f"The {info} has not 'resource_id' parameters")
    resource_id = info.parameters["resource_id"]
    resource = pipeline.repository.get_resource(resource_id)
    # Before the membership test: a retired spelling is a type GAIn used to
    # accept, and the generic message below would only say the annotator
    # wants something else -- true, and no help to someone holding a
    # resource that worked last release (gain#920).
    reject_retired_resource(resource)
    if resource.get_type() not in resource_types:
        raise ValueError(
            f"The {info} requires 'resource_id' to point to a "
            f"resource of type {resource_types}; "
            f"resource of type <{resource.get_type()}> found.")
    return resource


class GenomicScoreAnnotatorBase(AnnotatorBase):
    """Genomic score base annotator."""

    def __init__(self, pipeline: AnnotationPipeline, info: AnnotatorInfo,
                 score: GenomicScore):
        self.score = score
        self._resource_attr_params: dict[str, dict[str, Any]] = {}
        info.resources.append(score.resource)

        default_annotation = self.score.get_config().get("default_annotation")
        if default_annotation is not None:
            score_defs = self.score.score_definitions
            parsed_defaults = [
                AnnotationConfigParser.parse_raw_attribute_config(attr)
                for attr in default_annotation
            ]
            for parsed in parsed_defaults:
                if parsed.source not in score_defs:
                    raise ValueError(
                        f"Default annotation attribute '{parsed.source}' is "
                        "not defined in the score resource!")
                params = {
                    k: v for k, v in parsed.parameters.items()
                    if k != "description"
                }
                if parsed.aggregator is not None:
                    params["aggregator"] = parsed.aggregator
                if params:
                    self._resource_attr_params[parsed.source] = params
            if not info.attributes:
                defaults_by_source = {p.source: p for p in parsed_defaults}
                for source in score_defs:
                    if source not in defaults_by_source:
                        continue
                    parsed = defaults_by_source[source]
                    info.attributes.append(AttributeConfig(
                        name=parsed.name or parsed.source,
                        source=parsed.source,
                        internal=parsed.internal,
                        aggregator=parsed.aggregator,
                    ))

        super().__init__(pipeline, info)
        # A count of bases, read through the accessor that says so: it is
        # compared against an annotatable's length, and anything that is
        # not a whole non-negative number is refused here, as the
        # pipeline loads.  Read with a bare `.get()` it reached the
        # comparison instead and raised per annotated variant
        # (gain#1166).
        self._region_length_cutoff = info.parameters.get_integer(
            "region_length_cutoff", default=500_000, minimum=0)

        self.simple_score_queries: list[str] = [
            attr.source for attr in self._attributes
            if attr.source in self.score.score_definitions]

    def open(self) -> Annotator:
        self.score.open()
        super().open()
        return self

    def is_open(self) -> bool:
        return self.score.is_open()

    def _collect_score_queries(self) -> list:
        return []

    @staticmethod
    def _query_aggregator(attr: Attribute) -> str | None:
        """The aggregator NAME an attribute puts on its region query.

        ``None`` when the attribute names none, passed through to be
        refused by the score's resolver as the pipeline loads -- only a
        ``bool`` score, which has no default, can be in that position.
        """
        return (
            aggregator_name(attr.aggregator)
            if attr.aggregator is not None else None)

    def close(self) -> None:
        self.score.close()
        super().close()

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        default_annotation = self.score.get_config().get("default_annotation")
        specs = {
            attr_source: AttributeSpec(
                source=attr_def.score_id,
                value_type=attr_def.value_type,
                description=attr_def.desc,
                is_default=default_annotation is None,
                internal_default=False,
            )
            for attr_source, attr_def in self.score.score_definitions.items()
        }
        if default_annotation is not None:
            for attr in default_annotation:
                parsed = \
                    AnnotationConfigParser.parse_raw_attribute_config(attr)
                if parsed.source in specs:
                    specs[parsed.source].is_default = True
        return specs

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        return dict(self._resource_attr_params.get(spec.source, {}))

    def _build_score_aggregator_documentation(
        self, attr: Attribute,
        aggregator: str,
        attribute_conf_agg: AggregatorSource | None,
    ) -> str:
        """Collect score aggregator documentation.

        No fallback for an unset aggregator, and no local copy of the
        per-value-type defaults: the score class owns that table
        (``GenomicScore.DEFAULT_AGGREGATORS``) and ``_build_scoredefs``
        applies it, so a definition's ``aggregator`` is already resolved by
        the time anything reads it.
        """
        if attribute_conf_agg is None:
            score_def = self.score.get_score_definition(attr.source)
            assert score_def is not None
            value_str = f"`{score_def.aggregator}` [default]"
        else:
            value_str = str(attribute_conf_agg)
        return f"**{aggregator}**: {value_str}"

    @staticmethod
    def _append_attribute_documentation(attr: Attribute, line: str) -> None:
        """Add one markdown line to what an attribute documents about itself.

        The separator and the write to the attribute's own string live here
        so that everything documenting an attribute agrees on them without
        each caller spelling the append out again.
        """
        attr._documentation = (  # ruff: ignore[private-member-access]
            f"{attr.documentation}\n\n{line}")

    def add_score_aggregator_documentation(
            self, attr: Attribute,
            aggregator: str,
            attribute_conf_agg: AggregatorSource | None) -> None:
        """Collect score aggregator documentation."""
        self._append_attribute_documentation(
            attr,
            self._build_score_aggregator_documentation(
                attr, aggregator, attribute_conf_agg))

    @abc.abstractmethod
    def build_score_aggregator_documentation(
        self, attr: Attribute,
    ) -> list[str]:
        """Construct score aggregator documentation."""

    def build_attribute_help(self, attr: Attribute) -> str:
        """Build attribute help."""
        hist_url = self.score.get_histogram_image_public_url(attr.source)
        score_def = self.score.get_score_definition(attr.source)
        assert score_def is not None

        histogram = get_template("score_histogram.jinja").render(
            hist_url=hist_url,
            score_def=score_def,
        )

        assert attr.spec is not None
        data = {
            "name": attr.name,
            "description": attr.spec.description,
            "resource_id": self.score.resource_id,
            "resource_summary": self.score.resource.get_summary(),
            "resource_url":
            f"{self.score.resource.get_public_url()}/index.html",
            "resource_type": self.score.resource.get_type(),
            "histogram": histogram,
            "source": attr.source,
            "aggregators": self.build_score_aggregator_documentation(
                attr,
            ),
            "annotator_type": self.get_info().type,
            "annotator_doc": self.get_info().documentation,
        }
        return get_template("genomic_score_help.jinja").render(data=data)
