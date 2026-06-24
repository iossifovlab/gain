"""Provides LiftOver chain resource."""

from __future__ import annotations

import logging
from threading import Lock, RLock
from typing import Any, cast


from gain.genomic_resources import GenomicResource
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_implementation import (
    ResourceConfigValidationMixin,
    get_base_resource_schema,
)

logger = logging.getLogger(__name__)


class BasicResource(ResourceConfigValidationMixin):
    """Defines the implementation of a basic resource objects."""

    def __init__(self, resource: GenomicResource):

        self.resource = resource
        self.lock = RLock()

        config = resource.get_config()
        if resource.get_type() != "basic":
            logger.error(
                "trying to use genomic resource %s "
                "as a basic resource but its type is %s; %s",
                resource.resource_id, resource.get_type(), config)
            raise ValueError(f"wrong resource type: {config}")

    def close(self) -> None:
        pass

    @property
    def files(self) -> set[str]:
        return {}

    @staticmethod
    def get_schema() -> dict[str, Any]:
        return {
            **get_base_resource_schema()
        }


def build_basic_resource_from_resource(
    resource: GenomicResource,
) -> BasicResource:
    """Load a BasicResource from GRR resource."""
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "basic":
        logger.error(
            "trying to use genomic resource %s "
            "as a basic but its type is %s;",
            resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    return BasicResource(resource)


def build_basic_resource_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> BasicResource:
    if grr is None:
        grr = build_genomic_resource_repository()
    return build_basic_resource_from_resource(
        grr.get_resource(resource_id))
