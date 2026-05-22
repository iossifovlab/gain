"""Provides base class for annotators."""
from __future__ import annotations

import abc
import os
from collections.abc import Sequence
from itertools import starmap
from pathlib import Path
from typing import Any

from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotatorInfo,
    Attribute,
    AttributeConfig,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AttributeSpec,
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
                        parameters=defaults,
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
            parameters = {**defaults, **attr_config.parameters}
            self._attributes.append(Attribute(
                name=attr_config.name,
                source=attr_config.source,
                internal=internal,
                spec=spec,
                parameters=parameters,
            ))

        work_dir = info.parameters.get("work_dir")
        self.work_dir = Path(work_dir) if work_dir is not None else None
        super().__init__(pipeline, info)

    @property
    def attributes(self) -> list[Attribute]:
        return self._attributes

    def get_attribute_defaults(
        self, spec: AttributeSpec,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {}

    def open(self) -> Annotator:
        super().open()
        if self.work_dir is not None:
            os.makedirs(self.work_dir, exist_ok=True)
        return self

    @abc.abstractmethod
    def _do_annotate(self, annotatable: Annotatable, context: dict[str, Any]) \
            -> dict[str, Any]:
        """Annotate the annotatable.

        Internal abstract method used for annotation. It should produce
        all name-keyed attributes defined for this annotator instance.
        """

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        if annotatable is None:
            values = self._empty_result()
        else:
            values = self._do_annotate(annotatable, context)
        return {attr.name: values[attr.name] for attr in self._attributes}

    def _do_batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """Annotate a batch of annotatables."""
        return list(starmap(
            self._do_annotate, zip(annotatables, contexts, strict=True),
        ))

    def batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,
    ) -> list[dict[str, Any]]:
        inner_output = self._do_batch_annotate(
            annotatables, contexts, batch_work_dir=batch_work_dir,
        )
        return [{
            attr.name: result[attr.name]
            for attr in self._attributes
        } for result in inner_output]
