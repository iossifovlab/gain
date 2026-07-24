"""Loading helpers for ``data_frame`` genomic resources."""

import pandas as pd

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

logger = logging.getLogger(__name__)


def load_data_frame_from_resource(
    resource: GenomicResource | None,
) -> pd.DataFrame:
    """Load a pandas DataFrame from a ``data_frame`` genomic resource."""
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
        logger.exception(
            "the data_frame resource %s needs a file parameter",
            resource.resource_id)
        raise ValueError(
            f"missing file parameter for: {resource.resource_id}") from exc

    file_format = config.get("format", "csv")
    params = config.get("parameters", {})

    result: pd.DataFrame
    if file_format in {"csv", "tsv"}:
        result = pd.read_csv(
            resource.get_file_url(file_name), **params)
    elif file_format == "excel":
        result = pd.read_excel(
            resource.get_file_url(file_name), **params)
    else:
        logger.error(
            "unknown format %s for the data_frame %s",
            file_format, resource.resource_id)
        raise ValueError(
            f"Unknown format {file_format} "
            f"for the dataframe {resource.resource_id}")

    return result


def load_data_frame_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> pd.DataFrame:
    """Load a data_frame from a genomic resource id."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    if grr is None:
        grr = build_genomic_resource_repository()

    return load_data_frame_from_resource(grr.get_resource(resource_id))
