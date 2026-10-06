from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from gain import logging
from gain.genomic_resources.memo import Memo, config_memo_key

if TYPE_CHECKING:
    from gain.genomic_resources.gene_models.gene_models import GeneModels
    from gain.genomic_resources.repository import (
        GenomicResource,
        GenomicResourceRepo,
    )

logger = logging.getLogger(__name__)

_RESOURCE_CACHE: Memo[tuple[str, str, str], GeneModels] = Memo()
_FILE_CACHE: Memo[tuple[str, str], GeneModels] = Memo()


def _root_relative(path: str) -> str:
    """Return ``path`` as a name relative to the filesystem root.

    This API takes local paths that may point anywhere -- and its three
    paths (the models, the gene mapping and the chromosome mapping) need
    not share a directory, so the ``dirname``/``basename`` split its
    reference-genome and gene-set siblings use does not fit. Rooting the
    synthetic resource at ``/`` instead keeps every name relative to the
    resource, which is what resource file names must be (gain#467).
    """
    return os.path.abspath(path).lstrip("/")


def build_gene_models_from_file(
    file_name: str,
    file_format: str | None = None,
    gene_mapping_file_name: str | None = None,
    chrom_mapping_file_name: str | None = None,
) -> GeneModels:
    """Load gene models from local filesystem."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.fsspec_protocol import (
        build_local_resource,
    )

    from .gene_models import GeneModels

    config: dict[str, Any] = {
        "type": "gene_models",
        "filename": _root_relative(file_name),
    }
    if file_format:
        config["format"] = file_format
    if gene_mapping_file_name:
        config["gene_mapping"] = _root_relative(gene_mapping_file_name)
    if chrom_mapping_file_name is not None:
        config["chrom_mapping"] = {
            "filename": _root_relative(chrom_mapping_file_name),
        }

    # Keyed on the whole config so that every config-shaping
    # argument -- present and future -- participates in the key.
    return _FILE_CACHE.get_or_build(
        (file_name, config_memo_key(config)),
        lambda: GeneModels(build_local_resource("/", config)))


def build_gene_models_from_resource(
    resource: GenomicResource | None,
) -> GeneModels:
    """Load gene models from a genomic resource."""
    # pylint: disable=import-outside-toplevel
    from .gene_models import GeneModels

    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "gene_models":
        logger.error(
            "trying to open a resource %s of type "
            "%s as gene models", resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    return _RESOURCE_CACHE.get_or_build(
        resource.get_memo_key(), lambda: GeneModels(resource))


def build_gene_models_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> GeneModels:
    """Load gene models from a genomic resource id."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    if grr is None:
        grr = build_genomic_resource_repository()

    return build_gene_models_from_resource(grr.get_resource(resource_id))
