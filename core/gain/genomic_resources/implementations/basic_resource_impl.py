"""Provides LiftOver chain resource."""

from __future__ import annotations

import copy
import logging
from typing import Any, ClassVar

from markdown2 import markdown

from gain.genomic_resources import GenomicResource
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
    """Defines BasicResource implementation."""

    def __init__(self, resource: GenomicResource):

        super().__init__(resource)

    template_name: ClassVar[str] = "basic.jinja"

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
