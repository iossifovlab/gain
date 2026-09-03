"""Provides base class for annotators."""
from __future__ import annotations

import abc
import os
from collections.abc import Callable, Mapping, Sequence
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


# A real ``dict`` subclass, not a ``UserDict``: ``_do_annotate`` promises
# ``dict[str, Any]`` to every caller and every other annotator still returns
# a plain one, so the marker has to BE a dict to travel that contract.  What
# is added is the type itself -- there is no behaviour here to get wrong,
# which is the hazard the rule guards against.
class AggregatedValues(dict[str, Any]):  # ruff: ignore[subclass-builtin]
    """Values a ``_do_annotate`` has already reduced, keyed by ATTRIBUTE NAME.

    The contract an annotator uses to say "these are finished".
    :meth:`AnnotatorBase._apply_aggregators` recognises it by type and
    passes it through untouched, reducing nothing and re-keying nothing.

    Both parts of that matter, and both are why a marker type is needed
    rather than a convention (gain#1130).  A finished ``list`` aggregation
    is indistinguishable BY VALUE from a raw list of values still to be
    reduced, so the type is what tells the base which it is holding.  And
    the keys are attribute names rather than sources because a source
    exposed twice with two aggregators has two different finished values,
    which a source-keyed mapping has nowhere to put.

    The legacy shape -- a source-keyed dict, whose values are finished
    too since gain#1133 -- stays live beside it until gain#1134 moves the
    remaining annotators onto name keys and the rename goes.

    **Read the names when you ANSWER, never in ``__init__``.**  The one
    statement of the rule every annotator building one of these has to
    follow, kept here rather than in each of them.  A pipeline naming one
    attribute twice renames the later ones --
    ``annotation_factory.resolve_repeated_attributes`` -- and it does so
    AFTER every annotator has been constructed.  So an annotator that
    captured ``attr.name`` while building its queries would key its
    answers by names the pipeline has since moved away from, and the
    attributes it renamed would come back empty.  Whatever a query list
    caches, it must not cache names; ``self._attributes`` is walked again
    at annotate time and the names read off it then.
    """


def fold_own_values(
    attributes: Sequence[Attribute], values: Mapping[str, Any],
) -> AggregatedValues:
    """Fold a source-keyed dict of an annotator's OWN values, by attribute.

    The one statement of what an annotator does when the values it
    reduces are its own rather than a score's record stream -- a gene
    list, a set intersection -- and there is therefore no folding read to
    move the reduction into.  Each attribute takes its source's value,
    folded by the aggregator the attribute names, keyed by the
    attribute's NAME.

    Only a ``list`` is folded.  A scalar, a ``None``, an absent source
    pass through: an aggregator names how to reduce MANY values and there
    is nothing to reduce here, which is what the base's own fold did
    before gain#1133 retired it.

    A fresh accumulator per attribute per call.  The name resolution is
    memoised (:meth:`Aggregator.build`), so building anew costs about
    what clearing a held instance did, and nothing outlives the call --
    which is what the old reuse contract needed care to make safe.

    This is a function rather than a method for the reason gain#1133
    exists: the BASE does not aggregate.  An annotator that reduces says
    so by calling this and answering an :class:`AggregatedValues`; the
    base never decides to fold anything on an annotator's behalf.
    """
    return AggregatedValues(
        (attr.name, _fold_one(attr, values.get(attr.source)))
        for attr in attributes
    )


def _fold_one(attr: Attribute, value: Any) -> Any:
    if attr.aggregator is None or not isinstance(value, list):
        return value
    return Aggregator.build(attr.aggregator).aggregate(value)


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

    def get_attribute_defaults(
        self, spec: AttributeSpec,  # ruff: ignore[unused-method-argument]
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

        Internal abstract method used for annotation.  Either shape will
        do, and :meth:`_apply_aggregators` tells them apart by type: an
        :class:`AggregatedValues`, whose keys are attribute NAMES and
        whose values are finished, or a source-keyed dict of values that
        are finished too and need only the rename gain#1134 removes.

        Nothing here is reduced on an annotator's behalf (gain#1133): an
        annotator that folds does it in its score's own read, or -- when
        the values are its own rather than a record stream -- through
        :func:`fold_own_values`.
        """

    def _pair_aggregated(
        self,
        values: Sequence[Any],
        query_count: int,
        *,
        resource_id: str,
        reduced: Callable[[Attribute], bool],
        otherwise: Callable[[Attribute], Any],
    ) -> AggregatedValues:
        """Pair a score's reduced ``values`` back onto the attributes, by ORDER.

        The one statement of how an annotator turns what its score's
        folding read answered into an :class:`AggregatedValues`.  The
        read's tuple is parallel to the queries the annotator built over
        ``self._attributes`` in attribute order, so the attributes are
        walked again here and each one for which ``reduced`` holds takes
        the next value; every other attribute takes ``otherwise(attr)`` --
        the fragment count, the allele keys -- whatever that kind answers
        beside its reductions.  The names are read HERE, never cached
        beside the queries, for the reason :class:`AggregatedValues`
        states.

        One value per query, so as many as there are queries: checked
        rather than assumed, because the pairing is POSITIONAL and a read
        that answered a different number would otherwise slide every
        attribute onto its neighbour's value.  A length compare, not a
        ``zip(strict=True)``: the strict zip needs a second list of names
        to zip against, and building one costs about three times what the
        whole annotate call costs (measured, gain#1124).
        """
        if len(values) != query_count:
            raise ValueError(
                f"{self.get_info().type} asked {query_count} queries of "
                f"resource '{resource_id}' and got {len(values)} values "
                f"back")
        answers = iter(values)
        return AggregatedValues(
            (attr.name, next(answers) if reduced(attr) else otherwise(attr))
            for attr in self._attributes)

    def _apply_aggregators(
        self, values: dict[str, Any],
    ) -> dict[str, Any]:
        """Key what ``_do_annotate`` answered by ATTRIBUTE NAME.

        The base reduces nothing (gain#1133).  Every annotator in gain
        that folds a region does it in its score, which is what makes
        folding proportional to records rather than to base pairs, and
        says so by answering an :class:`AggregatedValues`; the two that
        reduce something other than a record stream -- the gene score and
        gene set annotators -- fold their own values and answer one too.
        Such a result is already keyed by name and already finished, so
        it is copied through: folding it again would reduce a finished
        list a second time.

        Everything else is the legacy shape -- a source-keyed dict of
        values that are ALREADY final -- and all that is left to do with
        it is the source-to-name rename.  The rename is what gain#1134
        removes, by moving the remaining annotators onto name keys; this
        method goes with it.

        It answers for ``annotate`` and ``batch_annotate`` alike, both of
        which route through it, so one statement covers both paths.
        """
        if isinstance(values, AggregatedValues):
            return dict(values)
        return {attr.name: values.get(attr.source) for attr in self._attributes}

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
        batch_work_dir: str | None = None,  # ruff: ignore[unused-method-argument]
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
