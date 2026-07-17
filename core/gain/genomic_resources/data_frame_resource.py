import pandas as pd
from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

# def build_gene_models_from_resource(
#     resource: GenomicResource | None,
# ) -> GeneModels:
#     """Load gene models from a genomic resource."""
#     # pylint: disable=import-outside-toplevel
#     from .gene_models import GeneModels

#     if resource is None:
#         raise ValueError(f"missing resource {resource}")

#     if resource.get_type() != "gene_models":
#         logger.error(
#             "trying to open a resource %s of type "
#             "%s as gene models", resource.resource_id, resource.get_type())
#         raise ValueError(f"wrong resource type: {resource.resource_id}")

#     cache_id = (resource.get_full_id(), resource.get_repo_url())
#     with _INMEMORY_CACHE_LOCK:
#         if cache_id in _INMEMORY_CACHE:
#             return _INMEMORY_CACHE[cache_id]

#         gene_models = GeneModels(resource)
#         _INMEMORY_CACHE[cache_id] = gene_models
#         return gene_models


# def build_gene_models_from_resource_id(
#     resource_id: str, grr: GenomicResourceRepo | None = None,
# ) -> GeneModels:
#     """Load gene models from a genomic resource id."""
#     # pylint: disable=import-outside-toplevel
#     from gain.genomic_resources.repository_factory import (
#         build_genomic_resource_repository,
#     )
#     if grr is None:
#         grr = build_genomic_resource_repository()

#     return build_gene_models_from_resource(grr.get_resource(resource_id))


logger = logging.getLogger(__name__)


def load_data_frame_from_resource(resource: GenomicResource | None) -> pd.DataFrame:
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "data_frame":
        logger.error(
            "trying to open a resource %s of type "
            "%s as a data_frame", resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    config = resource.get_config()

    try:
        file_name = config["file"]
    except KeyError as exc:
        logger.error(f"The data_frame resource {resource.resource_id} need a file parameter")
        raise ValueError(f"missing file parameter for: {resource.resource_id}") from exc

    file_format = config.get("format", "csv")
    params = config.get("parameters", {})

    if file_format in {'csv', 'tsv'}:
        return pd.read_csv(file_name, **params)
    else:
        logger.error(f"Unknown format {file_format} for the dataframe {resource.resource_id}")
        raise ValueError(f"Unknown format {file_format} for the dataframe {resource.resource_id}")


def load_data_frame_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> pd.DataFrame:
    """Load data_frame from a genomic resource id."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    if grr is None:
        grr = build_genomic_resource_repository()

    return load_data_frame_from_resource(grr.get_resource(resource_id))
