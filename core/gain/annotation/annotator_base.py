"""Provides base class for annotators."""
from __future__ import annotations

import abc
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, cast

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
    CoverageAggregator,
    WeightedValues,
    validate_aggregator,
)


class _Unmeasured:
    """Marker for a source the annotator never looked up.

    Distinct from ``None``, which stands for a source that *was* looked
    up and carried nothing.  For a score value the two absences are the
    same; for its coverage they are not -- nothing found is a coverage of
    ``0``, while nothing looked up is no coverage at all.
    """

    def __repr__(self) -> str:
        return "UNMEASURED"


UNMEASURED: Final = _Unmeasured()


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
            attr = Attribute(
                name=attr_config.name,
                source=attr_config.source,
                internal=internal,
                aggregator=aggregator,
                spec=spec,
                parameters=parameters,
            )
            if aggregator is not None:
                if spec is not None and not spec.supports_aggregation:
                    raise ValueError(
                        f"Attribute '{attr.source}' in annotator"
                        f" {info.type} does not support aggregation.")
                validate_aggregator(
                    aggregator,
                    self._aggregator_value_type(attr),
                )
                attr.aggregator_instance = Aggregator.build(aggregator)
            if spec.coverage_of is not None:
                attr.aggregator_instance = CoverageAggregator()
            self._attributes.append(attr)

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

    @staticmethod
    def _value_source(attr: Attribute) -> str:
        """Return the source whose raw values back ``attr``.

        For a coverage attribute that is the source it measures, not its
        own name -- there are no values filed under a coverage source.
        A ``_do_annotate`` implementation that resolves what to fetch from
        the configured attributes must go through this, so that asking
        only for a coverage still fetches the values it counts.
        """
        if attr.spec is not None and attr.spec.coverage_of is not None:
            return attr.spec.coverage_of
        return attr.source

    def _unmeasured_result(self) -> dict[str, Any]:
        """Return the result of an annotatable that was never looked up.

        :meth:`_empty_result` says every source was consulted and carried
        nothing -- which for a coverage attribute is the measurement
        ``0``.  This says the lookup did not happen at all: a region past
        the annotator's length cutoff, or no annotatable to speak of.  A
        value is absent either way, but a coverage is only ``0`` when
        something was actually measured.
        """
        return {
            self._value_source(attr): UNMEASURED
            for attr in self._attributes
        }

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
        """Reduce each attribute's raw values with its aggregator.

        A ``_do_annotate`` implementation may hand over either a plain
        list -- every value counting once -- or a
        :class:`WeightedValues`, in which each value carries the number of
        times it counts.

        The aggregator instance is the attribute's own and is reused
        across annotate calls; each call clears it first.  This is
        correct single-threaded and is not thread-safe.
        """
        result: dict[str, Any] = {}
        for attr in self._attributes:
            if attr.spec is not None and attr.spec.coverage_of is not None:
                result[attr.name] = self._aggregate_coverage(
                    attr, values.get(attr.spec.coverage_of))
                continue
            value = values.get(attr.source)
            if value is UNMEASURED:
                result[attr.name] = None
            elif isinstance(value, WeightedValues):
                result[attr.name] = (
                    attr.aggregator_instance.aggregate_weighted(value)
                    if attr.aggregator_instance is not None
                    else value.expand()
                )
            elif attr.aggregator_instance is not None and isinstance(
                    value, list):
                result[attr.name] = attr.aggregator_instance.aggregate(value)
            else:
                result[attr.name] = value
        return result

    @staticmethod
    def _aggregate_coverage(attr: Attribute, raw: Any) -> int | None:
        """Return how much of ``raw`` was not ``None``.

        ``raw`` is the measured source's own raw values, in whichever of
        the three shapes ``_do_annotate`` produced: weighted values for a
        region, a plain list, or a single value for a point query.  A
        source that was consulted and produced nothing at all counts as
        zero -- an uncovered region is a measurement, not a missing one.

        A source that was never consulted (``UNMEASURED``) is the one case
        that is not zero: there is no measurement to report, so the
        coverage is as absent as the value it would have described.
        """
        if raw is UNMEASURED:
            return None
        aggregator = attr.aggregator_instance
        assert isinstance(aggregator, CoverageAggregator)
        if isinstance(raw, WeightedValues):
            return cast(int, aggregator.aggregate_weighted(raw))
        if isinstance(raw, list):
            return cast(int, aggregator.aggregate(raw))
        return cast(int, aggregator.aggregate([raw]))

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        if annotatable is None:
            values = self._unmeasured_result()
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
        return [
            self._apply_aggregators(
                self._unmeasured_result() if annotatable is None else result)
            for annotatable, result in zip(
                annotatables, inner_output, strict=True)
        ]
