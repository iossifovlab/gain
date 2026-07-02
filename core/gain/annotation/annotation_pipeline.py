"""Provides annotation pipeline class."""

from __future__ import annotations

import abc
import itertools
import sys
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import IO, Any

import gain.logging as logging
from gain.annotation.annotatable import Annotatable
from gain.annotation.annotation_config import (
    AnnotationPreamble,
    AnnotatorInfo,
    Attribute,
    RawPipelineConfig,
)
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

logger = logging.getLogger(__name__)

_AnnotationDependencyGraph = dict[
    AnnotatorInfo, list[tuple[AnnotatorInfo, Attribute]],
]


def _build_dependency_graph(
    pipeline: AnnotationPipeline,
) -> _AnnotationDependencyGraph:
    """Make dependency graph for an annotation pipeline."""
    graph: _AnnotationDependencyGraph = {}
    for annotator in pipeline.annotators:
        annotator_info = annotator.get_info()
        graph[annotator_info] = _get_dependencies_for(annotator, pipeline)
    return graph


def _get_dependencies_for(
    annotator: Annotator,
    pipeline: AnnotationPipeline,
) -> list[tuple[AnnotatorInfo, Attribute]]:
    """Get all dependencies for a given annotator."""
    result: list[tuple[AnnotatorInfo, Attribute]] = []
    used_attrs = annotator.used_context_attributes
    for attr in used_attrs:
        attr_info = pipeline.get_attribute_info(attr)
        assert attr_info is not None
        upstream_annotator = \
            pipeline.get_annotator_by_attribute_info(attr_info)
        assert upstream_annotator is not None
        result.append((upstream_annotator.get_info(), attr_info))
        if upstream_annotator.used_context_attributes:
            result.extend(_get_dependencies_for(upstream_annotator, pipeline))
    return result


def _get_rerun_annotators_with_triggers(
    pipeline: AnnotationPipeline,
    annotators_new: Iterable[AnnotatorInfo],
) -> dict[AnnotatorInfo, tuple[AnnotatorInfo, Attribute]]:
    """Map each rerun annotator to the new annotator that triggered it.

    An annotator is forced to rerun either because it consumes a new
    upstream attribute, or because it produces an internal attribute that a
    new (or rerun) downstream annotator depends on. The value is the *new*
    annotator at the root of the trigger together with the specific
    attribute that the dependent actually consumed.
    """
    annotators_new = list(annotators_new)
    result: dict[AnnotatorInfo, tuple[AnnotatorInfo, Attribute]] = {}

    dependency_graph = _build_dependency_graph(pipeline)

    for dependent, dependencies in dependency_graph.items():
        if dependent in annotators_new:
            for dependency, dep_attr in dependencies:
                if dep_attr.internal:
                    result.setdefault(dependency, (dependent, dep_attr))
        else:
            for dependency, dep_attr in dependencies:
                if dependency in annotators_new:
                    result.setdefault(dependent, (dependency, dep_attr))
                    break

    return result


def _get_rerun_annotators(
    pipeline: AnnotationPipeline,
    annotators_new: Iterable[AnnotatorInfo],
) -> set[AnnotatorInfo]:
    """Get all annotators that must be re-run for reannotation."""
    return set(
        _get_rerun_annotators_with_triggers(pipeline, annotators_new))


def _get_deleted_attributes(
    pipeline_current: AnnotationPipeline,
    pipeline_previous: AnnotationPipeline,
    *,
    full_reannotation: bool = False,
) -> list[str]:
    """Get a list of attributes that are deleted in the new annotation."""
    infos_new = pipeline_current.get_info()

    if full_reannotation is True:
        return [
            attr.name
            for annotator in pipeline_previous.annotators
            for attr in annotator.attributes
        ]

    result: list[str] = []
    for annotator in pipeline_previous.annotators:
        if annotator.get_info() not in infos_new:
            result.extend(
                attr.name for attr in annotator.attributes
                if not attr.internal
            )
    return result


FULL_REANNOTATION_REASON = "forced by --full-reannotation"


@dataclass
class PlanEntry:
    """A single attribute entry in a reannotation/annotation plan."""

    name: str
    internal: bool
    annotator_id: str
    reason: str | None = None


@dataclass
class ReannotationPlan:
    """Structured description of how a reannotation reuses/recomputes data.

    Each bucket is a list of :class:`PlanEntry`:

    - ``copied``: attributes reused unchanged from the input;
    - ``added``: attributes of annotators new to the pipeline;
    - ``computed``: attributes of unchanged annotators forced to recompute
      (``reason`` records the triggering dependency);
    - ``deleted``: attributes present in the previous pipeline but no longer
      produced.
    """

    copied: list[PlanEntry] = field(default_factory=list)
    added: list[PlanEntry] = field(default_factory=list)
    computed: list[PlanEntry] = field(default_factory=list)
    deleted: list[PlanEntry] = field(default_factory=list)


def _attr_label(entry: PlanEntry) -> str:
    return f"{entry.name} (internal)" if entry.internal else entry.name


def _format_plan_line(
    label: str, entries: list[PlanEntry], *, with_reason: bool = False,
) -> str:
    names = ", ".join(_attr_label(entry) for entry in entries)
    suffix = ""
    if entries:
        annotator_ids = {entry.annotator_id for entry in entries}
        if with_reason and len(annotator_ids) == 1:
            annotator_id = next(iter(annotator_ids))
            reason = entries[0].reason
            inner = annotator_id
            if reason:
                inner = f"{annotator_id} <- {reason}" \
                    if "depends on" in (reason or "") else \
                    f"{annotator_id} {reason}"
            suffix = f"   [{inner}]"
        elif with_reason:
            suffix = "   " + " ".join(
                f"[{entry.annotator_id}"
                + (f" {entry.reason}]" if entry.reason else "]")
                for entry in entries
            )
    count = len(entries)
    body = f": {names}" if names else ":"
    return f"  {label:<8} ({count}){body}{suffix}"


def _render_plan(
    plan: ReannotationPlan,
    header: str,
) -> str:
    lines = [
        header,
        _format_plan_line("COPIED", plan.copied),
        _format_plan_line("ADDED", plan.added, with_reason=True),
        _format_plan_line("COMPUTED", plan.computed, with_reason=True),
        _format_plan_line("DELETED", plan.deleted),
    ]
    return "\n".join(lines)


def format_annotation_plan(
    pipeline: AnnotationPipeline,
) -> str:
    """Render a plain annotation pipeline as an all-ADDED plan."""
    plan = ReannotationPlan(
        added=[
            PlanEntry(
                name=attr.name,
                internal=bool(attr.internal),
                annotator_id=annotator.get_info().annotator_id,
            )
            for annotator in pipeline.annotators
            for attr in annotator.attributes
        ],
    )
    return _render_plan(plan, "Annotation plan:")


def print_annotation_plan(
    pipeline: AnnotationPipeline,
    file: IO[str] | None = None,
) -> None:
    """Print a plain annotation pipeline plan."""
    print(format_annotation_plan(pipeline), file=file or sys.stderr)


@dataclass
class AttributeSpec:
    """Describes a single attribute an annotator can produce."""

    source: str
    value_type: str
    description: str
    is_default: bool = True
    internal_default: bool = False
    supports_aggregation: bool = True
    attribute_type: str = "attribute"

    def __post_init__(self) -> None:
        if self.attribute_type == "annotatable":
            self.supports_aggregation = False

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a response dict."""
        return {
            "source": self.source,
            "type": self.value_type,
            "description": self.description,
            "default": self.is_default,
            "internal": self.internal_default,
            "attribute_type": self.attribute_type,
            "supports_aggregation": self.supports_aggregation,
        }


class Annotator(abc.ABC):
    """Annotator provides a set of attrubutes for a given Annotatable."""

    BASE_DOC_URL = "https://iossifovlab.com/gaindocs/annotation_infrastructure.html"

    def __init__(self, pipeline: AnnotationPipeline | None,
                 info: AnnotatorInfo):
        self.pipeline = pipeline
        self._info = info
        self._is_open = False

    def get_info(self) -> AnnotatorInfo:
        return self._info

    @abc.abstractmethod
    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        """Produce annotation attributes for an annotatable."""

    def batch_annotate(
        self, annotatables: Sequence[Annotatable | None],
        contexts: list[dict[str, Any]],
        batch_work_dir: str | None = None,  # noqa: ARG002
    ) -> Iterable[dict[str, Any]]:
        return itertools.starmap(
            self.annotate, zip(annotatables, contexts, strict=True),
        )

    def close(self) -> None:
        self._is_open = False

    def open(self) -> Annotator:
        self._is_open = True
        return self

    def is_open(self) -> bool:
        return self._is_open

    @property
    def resources(self) -> list[GenomicResource]:
        return self._info.resources

    @property
    def resource_ids(self) -> set[str]:
        return {resource.get_id() for resource in self._info.resources}

    @property
    @abc.abstractmethod
    def attributes(self) -> list[Attribute]:
        """Return the list of attributes this annotator produces."""

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return ()

    def _empty_result(self) -> dict[str, Any]:
        return {attr.source: None for attr in self.attributes}

    @abc.abstractmethod
    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        """Get specs of all attributes the annotator can produce."""


class AnnotationPipeline:
    """Provides annotation pipeline abstraction."""

    def __init__(self, repository: GenomicResourceRepo):
        self.repository: GenomicResourceRepo = repository
        self.annotators: list[Annotator] = []
        self.preamble: AnnotationPreamble | None = None
        self.raw: RawPipelineConfig = []
        self._is_open = False

    def get_info(self) -> list[AnnotatorInfo]:
        return [annotator.get_info() for annotator in self.annotators]

    def get_attributes(self) -> list[Attribute]:
        return [attribute_info for annotator in self.annotators for
                attribute_info in annotator.attributes]

    def get_attribute_info(
            self, attribute_name: str) -> Attribute | None:
        for annotator in self.annotators:
            for attribute_info in annotator.attributes:
                if attribute_info.name == attribute_name:
                    return attribute_info
        return None

    def get_resource_ids(self) -> set[str]:
        return {r_id for annotator in self.annotators
                for r_id in annotator.resource_ids}

    def get_annotator_by_attribute_info(
        self, attribute_info: Attribute,
    ) -> Annotator | None:
        for annotator in self.annotators:
            if attribute_info in annotator.attributes:
                return annotator
        return None

    def add_annotator(self, annotator: Annotator) -> None:
        assert isinstance(annotator, Annotator)
        self.annotators.append(annotator)

    def annotate(
        self, annotatable: Annotatable | None,
        context: dict | None = None,
    ) -> dict:
        """Apply all annotators to an annotatable."""
        if not self._is_open:
            self.open()

        if context is None:
            context = {}

        for annotator in self.annotators:
            attributes = annotator.annotate(annotatable, context)
            context.update(attributes)

        return context

    def get_attributes_by_type(
        self, attribute_type: str,
    ) -> list[Attribute]:
        return [
            attribute_info for attribute_info in self.get_attributes()
            if attribute_info.spec is not None
            and attribute_info.spec.attribute_type == attribute_type
        ]

    def batch_annotate(
        self, annotatables: Sequence[Annotatable | None],
        contexts: list[dict] | None = None,
        batch_work_dir: str | None = None,
    ) -> list[dict]:
        """Apply all annotators to a list of annotatables."""
        if not self._is_open:
            self.open()

        if contexts is None:
            contexts = [{} for _ in annotatables]

        for annotator in self.annotators:
            attributes_list = annotator.batch_annotate(
                annotatables, contexts,
                batch_work_dir=batch_work_dir,
            )
            for context, attributes in zip(
                contexts, attributes_list, strict=True,
            ):
                context.update(attributes)

        return contexts

    def open(self) -> AnnotationPipeline:
        """Open all annotators in the pipeline and mark it as open."""
        if self._is_open:
            logger.warning("annotation pipeline is already open")
            return self

        assert not self._is_open

        for annotator in self.annotators:
            annotator.open()

        self._is_open = True
        return self

    def close(self) -> None:
        """Close the annotation pipeline."""
        logger.info("closing annotation pipeline")
        for annotator in self.annotators:
            try:
                annotator.close()
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "exception while closing annotator %s",
                    annotator.get_info())
        self._is_open = False

    def print(self) -> None:
        """Print the annotation pipeline."""
        print("NEW ATTRIBUTES -")
        for anno in self.annotators:
            for attr in anno.attributes:
                print("    +", attr.name)

    def __enter__(self) -> AnnotationPipeline:
        return self

    def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            exc_tb: TracebackType | None) -> bool:
        if exc_type is not None:
            logger.error(
                "exception during annotation: %s, %s, %s",
                exc_type, exc_value, traceback.format_tb(exc_tb))
        self.close()
        return exc_type is None


class ReannotationPipeline(AnnotationPipeline):
    """Provides functionality for reannotation."""

    def __init__(
        self,
        pipeline_new: AnnotationPipeline,
        pipeline_previous: AnnotationPipeline,
        *,
        full_reannotation: bool = False,
    ):
        super().__init__(pipeline_new.repository)

        self.pipeline_new = pipeline_new
        self.pipeline_previous = pipeline_previous
        self.full_reannotation = full_reannotation

        self.annotators: list[Annotator] = []

        self.infos_new: set[AnnotatorInfo] = set()
        self.infos_rerun: set[AnnotatorInfo] = set()
        self.rerun_triggers: \
            dict[AnnotatorInfo, tuple[AnnotatorInfo, Attribute]] = {}

        if full_reannotation:
            # Recompute everything, reuse nothing: every new-pipeline
            # annotator must run, otherwise its attribute is marked
            # deleted (see ``_get_deleted_attributes``) but never
            # recomputed, leaving a stale/not-recomputed value (#108).
            self.annotators = list(pipeline_new.annotators)
        else:
            infos_current = pipeline_new.get_info()
            infos_previous = pipeline_previous.get_info()

            self.infos_new = {
                i for i in infos_current
                if i not in infos_previous
            }

            self.rerun_triggers = _get_rerun_annotators_with_triggers(
                pipeline_new, self.infos_new)
            self.infos_rerun = set(self.rerun_triggers)

            for annotator in pipeline_new.annotators:
                info = annotator.get_info()
                if info in self.infos_new or info in self.infos_rerun:
                    self.annotators.append(annotator)

        self.deleted_attributes = _get_deleted_attributes(
            pipeline_new, pipeline_previous,
            full_reannotation=full_reannotation)

        self.plan = self._build_plan()

    def get_attributes(self) -> list[Attribute]:
        return self.pipeline_new.get_attributes()

    @staticmethod
    def _trigger_description(consumed: Attribute) -> str:
        """Human-readable name of the attribute that forced a rerun.

        Names the specific new attribute the dependent actually consumed.
        """
        return f"depends on new {consumed.name}"

    def _build_plan(self) -> ReannotationPlan:
        plan = ReannotationPlan()

        # DELETED: derive the producing annotator from the previous pipeline.
        # Internal attributes appear here only under full reannotation; the
        # incremental path filters them out. This asymmetry is intentional
        # and mirrors ``_get_deleted_attributes``.
        deleted_set = set(self.deleted_attributes)
        for annotator in self.pipeline_previous.annotators:
            annotator_id = annotator.get_info().annotator_id
            for attr in annotator.attributes:
                if attr.name in deleted_set:
                    plan.deleted.append(PlanEntry(
                        name=attr.name,
                        internal=bool(attr.internal),
                        annotator_id=annotator_id,
                    ))

        for annotator in self.pipeline_new.annotators:
            info = annotator.get_info()
            annotator_id = info.annotator_id
            for attr in annotator.attributes:
                name = attr.name
                internal = bool(attr.internal)
                if self.full_reannotation:
                    plan.computed.append(PlanEntry(
                        name, internal, annotator_id,
                        reason=FULL_REANNOTATION_REASON))
                elif info in self.infos_new:
                    plan.added.append(PlanEntry(
                        name, internal, annotator_id))
                elif info in self.infos_rerun:
                    _, consumed = self.rerun_triggers[info]
                    plan.computed.append(PlanEntry(
                        name, internal, annotator_id,
                        reason=self._trigger_description(consumed)))
                else:
                    plan.copied.append(PlanEntry(
                        name, internal, annotator_id))

        return plan

    def format_plan(self, reference: str | None = None) -> str:
        """Render the reannotation plan as human-readable text."""
        prefix = "Reannotation plan"
        if self.full_reannotation:
            prefix += " [full reannotation]"
        if reference is not None:
            prefix += f" (vs {reference})"
        header = f"{prefix}:"
        return _render_plan(self.plan, header)

    def print_plan(
        self,
        reference: str | None = None,
        file: IO[str] | None = None,
    ) -> None:
        """Print the reannotation plan."""
        print(self.format_plan(reference), file=file or sys.stderr)


class AnnotatorDecorator(Annotator):
    """Defines annotator decorator base class."""

    def __init__(self, child: Annotator):
        super().__init__(child.pipeline, child.get_info())
        self.child = child

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return self.child.get_attribute_specs()

    @property
    def attributes(self) -> list[Attribute]:
        return self.child.attributes

    def close(self) -> None:
        self.child.close()

    def open(self) -> Annotator:
        return self.child.open()

    def is_open(self) -> bool:
        return self.child.is_open()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.child, name)


class InputAnnotableAnnotatorDecorator(AnnotatorDecorator):
    """Defines annotator decorator to use input annotatable if defined."""

    @staticmethod
    def decorate(child: Annotator) -> Annotator:
        if "input_annotatable" in child.get_info().parameters:
            return InputAnnotableAnnotatorDecorator(child)
        return child

    def __init__(self, child: Annotator):
        super().__init__(child)

        assert "input_annotatable" in self._info.parameters
        self.input_annotatable_name = \
            self._info.parameters["input_annotatable"]

        if not self.pipeline:
            raise ValueError(
                "InputAnnotableAnnotatorDecorator can only work "
                "within a pipeline")
        att_info = self.pipeline.get_attribute_info(
            self.input_annotatable_name)
        if att_info is None:
            available_attributes = ",".join([
                f"'{att.name}' [{att.spec.attribute_type if att.spec else '?'}]"
                for att in self.pipeline.get_attributes()
            ])
            raise ValueError(f"The attribute '{self.input_annotatable_name}' "
                             "has not been defined before its use. The "
                             "available attributes are: "
                             f"{available_attributes}")
        if att_info.spec is None \
                or att_info.spec.attribute_type != "annotatable":
            raise ValueError(f"The attribute '{self.input_annotatable_name}' "
                             "is expected to be of type annotatable.")
        self.child._info.documentation += (  # noqa: SLF001
            f"\n* **input_annotatable**: `{self.input_annotatable_name}`"
        )

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return (*self.child.used_context_attributes,
                self.input_annotatable_name)

    def annotate(
        self, annotatable: Annotatable | None,  # noqa: ARG002
        context: dict[str, Any],
    ) -> dict[str, Any]:

        input_annotatable = context[self.input_annotatable_name]

        if input_annotatable is None or \
           isinstance(input_annotatable, Annotatable):
            return self.child.annotate(input_annotatable, context)
        raise ValueError(
            f"The object with a key {input_annotatable} in the "
            f"annotation context {context} is not an Annotatable.",
        )


class ValueTransformAnnotatorDecorator(AnnotatorDecorator):
    """Define value transformer annotator decorator."""

    @staticmethod
    def decorate(child: Annotator) -> Annotator:
        """Apply value transform decorator to an annotator."""
        value_transformers: dict[str, Callable[[Any], Any]] = {}
        for attr in child.attributes:
            if "value_transform" in attr.parameters:
                transform_str = attr.parameters["value_transform"]
                try:
                    # pylint: disable=eval-used
                    transform = eval(  # noqa: S307
                        f"lambda value: {transform_str}",
                    )
                except Exception as error:
                    raise ValueError(
                        f"The value trasform |{transform_str}| is "
                        f"sytactically invalid.", error) from error
                value_transformers[attr.name] = transform
                # pylint: disable=protected-access
                attr._documentation = (  # noqa: SLF001
                    f"{attr.documentation}\n\n"
                    f"**value_transform:** {transform_str}"
                )
        if value_transformers:
            return ValueTransformAnnotatorDecorator(child, value_transformers)
        return child

    def __init__(self, child: Annotator,
                 value_transformers: dict[str, Callable[[Any], Any]]):
        super().__init__(child)
        self.value_transformers = value_transformers

    def annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.child.annotate(annotatable, context)
        return {k: (self.value_transformers[k](v)
                    if k in self.value_transformers else v)
                for k, v in result.items()}
