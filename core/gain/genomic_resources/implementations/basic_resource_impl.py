"""Provides the catch-all ``basic`` resource implementation."""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from markdown2 import markdown

import gain.logging as logging
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc

logger = logging.getLogger(__name__)


class BasicResourceImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """Implementation for resources without a more specific type.

    A resource whose config carries no ``type`` resolves to the ``basic``
    type (see ``GenomicResource.get_type``). It has no schema or statistics of
    its own; it only renders a minimal info page and -- crucially -- exposes
    every data file via ``files`` so caching covers the whole resource, the
    same way untyped resources were cached before they had an implementation
    (gain#78).
    """

    template_name: ClassVar[str] = "basic.jinja"

    @property
    def files(self) -> set[str]:
        # No type-specific file list: a basic resource ships arbitrary files,
        # so every manifest entry (other than the config that
        # _enumerate_resource_files adds itself, and lockfiles) is part of it.
        return {
            entry.name
            for entry in self.resource.get_manifest()
            if entry.name != "genomic_resource.yaml"
            and not entry.name.endswith(".lockfile")
        }

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if "meta" in info:
            info["meta"] = markdown(str(info["meta"]))
        return info

    def get_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_info(self)

    def get_statistics_info(self, **kwargs: Any) -> str:  # noqa: ARG002
        return InfoImplementationMixin.get_statistics_info(self)

    def calc_info_hash(self) -> bytes:
        return b"placeholder"

    def calc_statistics_hash(self) -> bytes:
        return b"placeholder"

    def create_statistics_build_tasks(
        self, **kwargs: Any,  # noqa: ARG002
    ) -> list[TaskDesc]:
        return []
