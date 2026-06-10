"""Provides base class for annotators."""
from __future__ import annotations

import abc
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotatorInfo,
    Attribute,
    AttributeConfig,
    ParamsUsageMonitor,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
)
from gain.genomic_resources.aggregators import (
    Aggregator,
    validate_aggregator,
)


class AnnotatorBase(Annotator):
    """Base implementation of the `Annotator` class."""

    def __init__(
        self, pipeline: AnnotationPipeline | None,
        info: AnnotatorInfo,
    ):
        self.attribute_specs: dict[str, AttributeSpec] = {}
        for source, spec in self.get_attribute_specs().items():
            if isinstance(spec, AttributeSpec):
                self.attribute_specs[source] = spec
            else:
                raise TypeError(
                    f"Invalid attribute spec for source '{source}'"
                    f" in annotator {info.type}")

        if not info.attributes:
            for source, spec in self.attribute_specs.items():
                if spec.is_default:
                    defaults = self.get_attribute_defaults(spec)
                    info.attributes.append(AttributeConfig(
                        name=source,
                        source=source,
                        internal=None,
                        aggregator=defaults.get("aggregator"),
                        parameters={
                            k: v for k, v in defaults.items()
                            if k != "aggregator"
                        },
                    ))

        self._attributes: list[Attribute] = []
        for attr_config in info.attributes:
            if attr_config.source not in self.attribute_specs:
                raise ValueError(
                    f"The attribute source '{attr_config.source}'"
                    " is not supported for the annotator"
                    f" {info.type}")
            spec = self.attribute_specs[attr_config.source]
            internal = (
                attr_config.internal
                if attr_config.internal is not None
                else spec.internal_default
            )
            defaults = self.get_attribute_defaults(spec)
            default_aggregator = defaults.get("aggregator")
            parameters = ParamsUsageMonitor({
                **{k: v for k, v in defaults.items() if k != "aggregator"},
                **attr_config.parameters,
            })
            aggregator = (
                attr_config.aggregator
                if attr_config.aggregator is not None
                else default_aggregator
            )
            self._attributes.append(Attribute(
                name=attr_config.name,
                source=attr_config.source,
                internal=internal,
                aggregator=aggregator,
                spec=spec,
                parameters=parameters,
            ))

        self._aggregator_instances: list[Aggregator | None] = []
        for attr in self._attributes:
            if attr.aggregator is not None:
                if attr.spec is not None and not attr.spec.supports_aggregation:
                    raise ValueError(
                        f"Attribute '{attr.source}' in annotator"
                        f" {info.type} does not support aggregation.")
                validate_aggregator(
                    attr.aggregator,
                    self._aggregator_value_type(attr),
                )
                self._aggregator_instances.append(
                    Aggregator.build(attr.aggregator))
            else:
                self._aggregator_instances.append(None)

        work_dir = info.parameters.get("work_dir")
        if work_dir is None:
            raise ValueError(
                f"Missing a 'work_dir' parameter in annotator {info}.")
        self.work_dir: Path = Path(work_dir)
        super().__init__(pipeline, info)

    @property
    def attributes(self) -> list[Attribute]:
        return self._attributes

    def _aggregator_value_type(self, attr: Attribute) -> str | None:
        return attr.spec.value_type if attr.spec else None

    def get_attribute_defaults(
        self, spec: AttributeSpec,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    def open(self) -> Annotator:
        super().open()
        os.makedirs(self.work_dir, exist_ok=True)
        return self

    @abc.abstractmethod
    def _do_annotate(self, annotatable: Annotatable, context: dict[str, Any]) \
            -> dict[str, Any]:
        """Annotate the annotatable.

        Internal abstract method used for annotation. It should produce
        a source-keyed dict, one entry per configured attribute.
        """

    def _apply_aggregators(
        self, values: dict[str, Any],
    ) -> dict[str, Any]:
        result = {}
        for attr, aggregator in zip(
            self._attributes, self._aggregator_instances, strict=True,
        ):
            value = values.get(attr.source)
            if aggregator is not None and isinstance(value, list):
                result[attr.name] = aggregator.aggregate(value)
            else:
                result[attr.name] = value
        return result

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        if annotatable is None:
            values = self._empty_result()
        else:
            values = self._do_annotate(annotatable, context)
        return self._apply_aggregators(values)

    def _do_batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """Annotate a batch of annotatables."""
        return [
            self._empty_result() if annotatable is None
            else self._do_annotate(annotatable, context)
            for annotatable, context in zip(annotatables, contexts, strict=True)
        ]

    def batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        inner_output = self._do_batch_annotate(
            annotatables, contexts, batch_work_dir=batch_work_dir,
        )
        return [self._apply_aggregators(result) for result in inner_output]
