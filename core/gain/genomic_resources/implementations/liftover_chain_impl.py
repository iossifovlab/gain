"""Provides LiftOver chain resource."""

from __future__ import annotations

import copy
from typing import Any, ClassVar

from markdown2 import markdown

from gain import logging
from gain.genomic_resources import GenomicResource
from gain.genomic_resources.liftover_chain import (
    build_liftover_chain_from_resource,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
    InfoImplementationMixin,
)
from gain.task_graph.graph import TaskDesc

logger = logging.getLogger(__name__)


class LiftoverChainImplementation(
    GenomicResourceImplementation,
    InfoImplementationMixin,
):
    """Defines Lift Over chain resource implementation."""

    def __init__(self, resource: GenomicResource):

        super().__init__(resource)
        self.liftover_chain = build_liftover_chain_from_resource(self.resource)

    template_name: ClassVar[str] = "liftover_chain.jinja"

    def _get_template_data(self) -> dict[str, Any]:
        info = copy.deepcopy(self.config)

        if self.liftover_chain.chrom_variant_coordinates is not None:
            if "del_prefix" in self.liftover_chain.chrom_variant_coordinates:
                prefix = self.liftover_chain\
                    .chrom_variant_coordinates["del_prefix"]
                info["variant_chrom"] = (
                    f"Deletes chrom prefix {prefix}"
                    " from variants before performing liftover."
                )
            elif "add_prefix" in self.liftover_chain.chrom_variant_coordinates:
                prefix = self.liftover_chain\
                    .chrom_variant_coordinates["add_prefix"]
                info["variant_chrom"] = (
                    f"Adds chrom prefix {prefix}"
                    " to variants before performing liftover."
                )

        if self.liftover_chain.chrom_target_coordinates is not None:
            if "del_prefix" in self.liftover_chain.chrom_target_coordinates:
                prefix = self.liftover_chain\
                    .chrom_target_coordinates["del_prefix"]
                info["target_chrom"] = (
                    f"Deletes chrom prefix {prefix}"
                    " from variants after performing liftover."
                )
            elif "add_prefix" in self.liftover_chain.chrom_target_coordinates:
                prefix = self.liftover_chain\
                    .chrom_target_coordinates["add_prefix"]
                info["target_chrom"] = (
                    f"Adds chrom prefix {prefix}"
                    " to variants after performing liftover."
                )
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
