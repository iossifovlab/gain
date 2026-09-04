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
from gain.genomic_resources.aggregators import validate_aggregator


# A real ``dict`` subclass, not a ``UserDict``: ``annotate`` promises
# ``dict[str, Any]`` to the pipeline and hands this back as-is, so it has
# to BE a dict to travel that contract.  What is added is the type itself
# -- there is no behaviour here to get wrong, which is the hazard the rule
# guards against.
class AggregatedValues(dict[str, Any]):  # ruff: ignore[subclass-builtin]
    """Values a ``_do_annotate`` has already reduced, keyed by ATTRIBUTE NAME.

    The seam's one shape (gain#1130, gain#1134).  A ``_do_annotate`` that
    returns this has already applied every aggregator its attributes
    name and already keyed its answers by the attributes' names, and the
    base hands it through untouched -- reducing nothing, re-keying
    nothing.

    Both halves of that are why this is a type and not a convention.  A
    finished ``list`` aggregation is indistinguishable BY VALUE from a raw
    list of values still to be reduced, so the type is what says which
    one an annotator is answering.  And the keys are attribute names
    rather than sources because a source exposed twice with two
    aggregators has two different finished values, which a source-keyed
    mapping has nowhere to put.  Nothing checks the type at run time any
    more -- the base's branch that told the two shapes apart went with
    the second shape -- so it is a contract stated on ``_do_annotate``'s
    signature and held by the type checker, not a discriminator.

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
    """Answer an annotator's OWN values by attribute, each one folded.

    For the annotators whose values are their own rather than a score's
    record stream -- a gene list, a set intersection, one entry per
    prediction request -- so there is no folding read to move the
    reduction into.  Each attribute takes its source's value, reduced by
    the aggregator it names (:meth:`Attribute.fold`), under the
    attribute's NAME.

    Only a ``list`` is folded.  A scalar, a ``None``, an absent source
    pass through, as does any attribute naming no aggregator: an
    aggregator says how to reduce MANY values and there is nothing to
    reduce.  That is what the base's own fold did before gain#1133
    retired it.

    This is a function rather than a method for the reason gain#1133
    exists: the BASE does not aggregate.  An annotator that reduces says
    so by calling this and answering an :class:`AggregatedValues`; the
    base never decides to fold anything on an annotator's behalf.
    """
    result = AggregatedValues()
    for attr in attributes:
        value = values.get(attr.source)
        if attr.aggregator is not None and isinstance(value, list):
            value = attr.fold(value)
        result[attr.name] = value
    return result


class AnnotatorBase(Annotator):
    """Base implementation of the `Annotator` class.

    The class every in-tree annotator extends.  Its constructor checks
    the configured attributes against :meth:`get_attribute_specs`,
    resolves each one's name, aggregator and parameters (consulting
    :meth:`get_attribute_defaults`), and requires a ``work_dir``
    parameter.  A subclass implements :meth:`get_attribute_specs` and
    ``_do_annotate``; overrides ``_do_batch_annotate`` when it has a
    batched path; and overrides :meth:`get_attribute_defaults`,
    :meth:`open` and :meth:`close` when it has defaults or resources.
    :meth:`annotate` and :meth:`batch_annotate` are left alone, except
    by a batch-only annotator, which makes :meth:`annotate` refuse.
    """

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
        """The configured attributes, in configuration order.

        With no attributes configured, every spec marked ``is_default``
        stands in, under its source name.
        """
        return self._attributes

    def _aggregator_value_type(self, attr: Attribute) -> str | None:
        return attr.spec.value_type if attr.spec else None

    def get_attribute_defaults(
        self, spec: AttributeSpec,  # ruff: ignore[unused-method-argument]
    ) -> dict[str, Any]:
        """Defaults for ``spec``: an ``aggregator`` and parameters.

        Empty by default.  The constructor consults it for every
        attribute: the ``aggregator`` key becomes the aggregator when
        the configuration names none, and every other key becomes a
        parameter that the configuration's own parameters override.
        Override it when defaults live somewhere other than the spec --
        a score resource declares its own, for instance.
        """
        return {}

    def open(self) -> Annotator:
        """Create ``work_dir`` and mark the annotator open; returns ``self``.

        Overrides that open resources call this and return ``self``.
        """
        super().open()
        os.makedirs(self.work_dir, exist_ok=True)
        return self

    def _every(self, value: Any) -> AggregatedValues:
        """``value`` under every attribute's name.

        For the annotators with one thing to say -- a lifted-over
        annotatable, a renamed chromosome -- however many attributes
        expose it.  The names are read here, at answer time, for the
        reason :class:`AggregatedValues` states.
        """
        return AggregatedValues({attr.name: value for attr in self._attributes})

    def _from_sources(self, values: Mapping[str, Any]) -> AggregatedValues:
        """Source-keyed ``values`` answered by attribute name, nothing folded.

        The rename the base used to do to every result, kept as something
        an annotator asks for.  The non-folding twin of
        :func:`fold_own_values`: for values that are final as they stand
        -- a point read's one value per score, a tool's output row, the
        allele keys an exact match synthesises -- where a ``list`` is the
        answer and must not be reduced.  An attribute whose source is
        absent answers ``None``.
        """
        return AggregatedValues({
            attr.name: values.get(attr.source) for attr in self._attributes})

    def _empty_result(self) -> AggregatedValues:
        """``None`` under every attribute name.

        The answer for a ``None`` annotatable, and what annotators return
        when a guard fires -- a chromosome the resource does not have, a
        region past the length cutoff.
        """
        return self._every(None)

    @abc.abstractmethod
    def _do_annotate(self, annotatable: Annotatable, context: dict[str, Any]) \
            -> AggregatedValues:
        """Annotate the annotatable.

        Answers an :class:`AggregatedValues`: keyed by attribute NAME,
        every value finished.  The base hands it back as-is (gain#1134);
        nothing is reduced or renamed on an annotator's behalf.

        An annotator that folds does it in its score's own read and
        pairs the answers back with :meth:`_pair_aggregated`, or -- when
        the values are its own rather than a record stream -- through
        :func:`fold_own_values`.  One with nothing to fold answers through
        :meth:`_from_sources` or :meth:`_every`.
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

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        """Answer through ``_do_annotate``; the empty result for ``None``.

        Subclasses implement ``_do_annotate`` instead of overriding
        this: the ``None`` annotatable is handled here, so
        ``_do_annotate`` never sees one.  A batch-only annotator
        overrides it to raise ``NotImplementedError``.
        """
        if annotatable is None:
            return self._empty_result()
        return self._do_annotate(annotatable, context)

    def _do_batch_annotate(
        self,
        annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,  # ruff: ignore[unused-method-argument]
    ) -> list[AggregatedValues]:
        """Annotate a batch of annotatables.

        One :class:`AggregatedValues` per annotatable, in order, on the
        same contract as :meth:`_do_annotate`.
        """
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
    ) -> Sequence[dict[str, Any]]:
        """Answer through ``_do_batch_annotate``: one result per annotatable.

        Subclasses with a batched backend override ``_do_batch_annotate``
        instead, whose default loops ``_do_annotate`` and handles the
        ``None`` annotatables itself.
        """
        return self._do_batch_annotate(
            annotatables, contexts, batch_work_dir=batch_work_dir,
        )
