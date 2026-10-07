"""Fluent, immutable test-data builder for ``annotation_pipeline`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`, like
:mod:`.liftover_chain_builder`: ``builders`` is past pylint's
``max-module-lines``, so the dependency runs ONE WAY -- this module
imports the shared single-realize seam from ``builders`` and ``builders``
does not import back -- and ``an_annotation_pipeline`` is imported from
here.

The resource is a ``genomic_resource.yaml`` naming a pipeline file
(``annotation.yaml``) plus that file.  Knobs:

* :meth:`AnnotationPipelineBuilder.with_annotator` appends one annotator
  step naming a resource by id, so a test composing scores and a pipeline
  in one ``a_grr()`` keeps the ids in agreement without writing yaml.
* :meth:`AnnotationPipelineBuilder.with_pipeline` writes a raw pipeline
  verbatim -- the escape hatch for everything the steps do not model.
  The two are exclusive; mixing them raises ``ResourceValidationError``.
* ``with_meta`` / ``with_labels`` from ``MetaMixin`` add the ``meta:``
  block.

What it deliberately cannot express through steps: anything but a list
of annotators -- a ``preamble:`` / ``annotators:`` mapping goes through
``with_pipeline``.  A step's keys are written as given, never modelled
or checked, and referenced resource ids are not validated (see the
class docstring).  The builder renders plain dicts and lists with
``yaml.safe_dump`` and never imports the annotation layer, which the
GRR package must not depend on.
"""
from __future__ import annotations

import copy
import dataclasses
import pathlib
from typing import Any

import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import setup_directories
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_PIPELINE_FILENAME = "annotation.yaml"


@dataclasses.dataclass(frozen=True)
class AnnotationPipelineBuilder(MetaMixin):
    """Immutable builder for a single ``annotation_pipeline`` resource.

    A bare builder writes an empty pipeline (``[]``), which loads and
    annotates nothing, so it depends on no other resource.

    Referenced resource ids are NOT validated: the builder never checks
    that an id a step names exists in the composed ``a_grr()``, which
    does not know which builders reference which.  A dangling id builds
    without error and surfaces as the pipeline's own configuration error
    when the pipeline is loaded -- the failure a test about dangling ids
    has to be able to build and observe.
    """

    steps: tuple[str | dict[str, Any], ...] = ()
    raw_pipeline: str | None = None

    def with_annotator(
        self, annotator_type: str, resource_id: str | None = None,
        **params: Any,
    ) -> AnnotationPipelineBuilder:
        """Append one annotator step; steps are written in call order.

        The step renders in the shortest form the config language has for
        it: ``- <type>`` with neither a resource id nor params,
        ``- <type>: <resource_id>`` with only an id, and the complete
        mapping ``- <type>: {resource_id: ..., **params}`` otherwise.
        ``params`` are the annotator's own keys (``attributes``,
        ``input_annotatable``, ...), written as given and not modelled;
        they are deep-copied, so mutating them afterwards does not reach
        the builder.
        """
        if self.raw_pipeline is not None:
            raise ResourceValidationError(
                "with_annotator cannot add a step to a pipeline authored "
                "with with_pipeline; put the step in the raw pipeline")
        step: str | dict[str, Any]
        if params:
            details: dict[str, Any] = {}
            if resource_id is not None:
                details["resource_id"] = resource_id
            details.update(copy.deepcopy(params))
            step = {annotator_type: details}
        elif resource_id is not None:
            step = {annotator_type: resource_id}
        else:
            step = annotator_type
        return dataclasses.replace(self, steps=(*self.steps, step))

    def with_pipeline(self, raw: str) -> AnnotationPipelineBuilder:
        """Write ``raw`` verbatim as the pipeline file.

        The escape hatch for pipeline config the steps do not model -- a
        ``preamble:`` / ``annotators:`` mapping, say.  The text is not
        parsed or validated here.  It replaces the steps rather than
        joining them, so calling it on a builder that already has
        :meth:`with_annotator` steps raises ``ResourceValidationError``
        instead of silently dropping them.
        """
        if self.steps:
            raise ResourceValidationError(
                "with_pipeline would drop the steps authored with "
                "with_annotator; author the pipeline one way")
        return dataclasses.replace(self, raw_pipeline=raw)

    def _render_config(self) -> str:
        config: dict[str, Any] = {
            "type": "annotation_pipeline",
            "filename": _PIPELINE_FILENAME,
        }
        return yaml.safe_dump(
            config, default_flow_style=False, sort_keys=False,
        ) + self.render_meta()

    def _render_pipeline(self) -> str:
        if self.raw_pipeline is not None:
            return self.raw_pipeline
        return yaml.safe_dump(
            list(self.steps), default_flow_style=False, sort_keys=False)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this annotation-pipeline resource into ``resource_dir``."""
        setup_directories(resource_dir, {
            GR_CONF_FILE_NAME: self._render_config(),
            _PIPELINE_FILENAME: self._render_pipeline(),
        })

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def an_annotation_pipeline() -> AnnotationPipelineBuilder:
    """Return an immutable annotation-pipeline builder."""
    return AnnotationPipelineBuilder()
