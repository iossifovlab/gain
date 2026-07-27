"""Loading helpers for ``data_frame`` genomic resources."""

from typing import Any

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
    # ``config.get`` hands back the resource's own cached config dict --
    # applying the tsv separator default in place would mutate the config
    # for every later load in the process.  Copy first.
    params = dict(config.get("parameters", {}))

    result: Any
    if file_format in {"csv", "tsv"}:
        if file_format == "tsv":
            # Without this, ``format: tsv`` was a synonym for csv and a
            # tab-separated file loaded as a single column whose name was
            # the whole header line.  An explicit ``parameters.sep`` still
            # wins, so configs that already work around it are unaffected.
            params = {"sep": "\t", **params}
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

    if not isinstance(result, pd.DataFrame):
        # ``parameters`` is an unrestricted passthrough, and some of what it
        # can carry changes the reader's return type: ``chunksize`` or
        # ``iterator`` make read_csv hand back a TextFileReader, and
        # ``sheet_name: null`` makes read_excel hand back a dict of frames.
        # This loader promises a single DataFrame, so say so here rather
        # than let the caller trip over the wrong object later.
        logger.error(
            "the parameters of the data_frame %s produced a %s "
            "instead of a data frame",
            resource.resource_id, type(result).__name__)
        # ValueError, not TypeError: this is a misconfigured resource rather
        # than a caller passing the wrong type, every other rejection in this
        # function is a ValueError, and callers catch that.
        raise ValueError(  # noqa: TRY004
            f"parameters of {resource.resource_id} produced a "
            f"{type(result).__name__}, not a data frame")

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
