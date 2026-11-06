"""The base of every annotator that reads a score resource.

``ScoreAnnotatorBase`` gives the ``default_annotation`` list of a score
resource one parser and one meaning, whether the resource is a gene score
or a position or allele score (gain#310).  The fragment score annotator
reads no ``default_annotation``.  Each entry of the list is
parsed like an attribute of a pipeline config, and its keys mean:

- ``source``: the score the entry makes a default attribute.  A source
  that is not a score of the resource is refused as the annotator is
  built.
- ``name``: the attribute name when the pipeline config lists no
  attributes.
- ``internal``: the default of ``internal``.  A pipeline config that sets
  ``internal`` wins.
- ``aggregator``: the aggregator when the pipeline config names none.
- ``description``: the description of the attribute spec.
- any other key: a parameter default of the attribute.  A parameter in
  the pipeline config wins.

With ``default_annotation`` absent, every score is a default attribute
under its own id.  The resource hands over the raw entries
(:meth:`~gain.genomic_resources.score_resource.ScoreResource.get_default_annotation_attributes`):
the GRR does not import the annotation layer, so the parse lives here.

A subclass states the spec of one score in :meth:`_score_attribute_spec`,
with its own value types, and keeps its own read and fold.
"""
import abc
from typing import Any

from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotatorInfo,
    AttributeConfig,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.score_resource import ScoreResource


class ScoreAnnotatorBase[ScoreT: ScoreResource[Any]](AnnotatorBase):
    """An annotator of one score resource and its ``default_annotation``."""

    def __init__(
        self, pipeline: AnnotationPipeline | None,
        info: AnnotatorInfo,
        score: ScoreT,
    ):
        self.score: ScoreT = score
        self._default_entries = self._parse_default_annotation(score)

        if not info.attributes:
            info.attributes.extend(
                AttributeConfig(
                    name=entry.name, source=entry.source,
                    aggregator=entry.aggregator)
                for entry in self._default_entries.values()
            )
        super().__init__(pipeline, info)
        self._mark_resource_parameters_used(info)

    def _mark_resource_parameters_used(self, info: AnnotatorInfo) -> None:
        """Keep a resource parameter default out of the unused check.

        A key of a ``default_annotation`` entry is the resource author's,
        and a subclass may read none of them.  The pipeline author cannot
        remove such a key, so it does not refuse the pipeline.  A key that
        the pipeline config sets itself stays subject to the check.
        """
        for attr_config, attr in zip(
                info.attributes, self.attributes, strict=True):
            entry = self._default_entries.get(attr.source)
            if entry is None:
                continue
            for key in entry.parameters:
                if key != "description" and key not in attr_config.parameters:
                    attr.parameters.get(key)

    @staticmethod
    def _parse_default_annotation(
        score: ScoreT,
    ) -> dict[str, AttributeConfig]:
        """Parse the ``default_annotation`` entries, keyed by source.

        In the order of the score definitions.  When two entries name one
        source, the last one wins.
        """
        resource_id = score.resource.resource_id
        by_source: dict[str, AttributeConfig] = {}
        for raw in score.get_default_annotation_attributes():
            entry = AnnotationConfigParser.parse_raw_attribute_config(raw)
            if entry.source not in score.score_definitions:
                raise ValueError(
                    f"Default annotation attribute '{entry.source}' is "
                    f"not defined in the score resource '{resource_id}'.")
            by_source[entry.source] = entry
        return {
            source: by_source[source]
            for source in score.score_definitions
            if source in by_source
        }

    @abc.abstractmethod
    def _score_attribute_spec(self, score_id: str) -> AttributeSpec:
        """The spec of the attribute of one score, before any default."""

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        specs: dict[str, AttributeSpec] = {}
        for score_id in self.score.score_definitions:
            spec = self._score_attribute_spec(score_id)
            entry = self._default_entries.get(score_id)
            spec.is_default = entry is not None
            if entry is not None:
                description = entry.parameters.get("description")
                if description:
                    spec.description = description
                if entry.internal is not None:
                    spec.internal_default = entry.internal
            specs[score_id] = spec
        return specs

    def get_attribute_defaults(
        self, spec: AttributeSpec,
    ) -> dict[str, Any]:
        entry = self._default_entries.get(spec.source)
        if entry is None:
            return {}
        defaults = {
            key: value for key, value in entry.parameters.items()
            if key != "description"
        }
        if entry.aggregator is not None:
            defaults["aggregator"] = entry.aggregator
        return defaults
