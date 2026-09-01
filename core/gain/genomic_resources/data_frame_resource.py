"""Loading helpers for ``data_frame`` genomic resources."""

from typing import Any

import pandas as pd
from pandas.io.common import infer_compression

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
    # ``config.get`` hands back the resource's own cached config dict, and
    # the defaults below are applied IN PLACE -- without this copy the tsv
    # separator and the inferred compression would be written into the
    # config itself and seen by every later load through this resource.
    params = dict(config.get("parameters", {}))

    result: Any
    if file_format in {"csv", "tsv"}:
        if file_format == "tsv" and not params.keys() & {"sep", "delimiter"}:
            # Without this, ``format: tsv`` was a synonym for csv and a
            # tab-separated file loaded as a single column whose name was
            # the whole header line.  A config that states its own
            # separator keeps winning -- and it has to be checked under
            # both spellings, because ``delimiter`` is pandas' alias for
            # ``sep`` and read_csv rejects a call carrying both.
            params["sep"] = "\t"
        # A bare url would make pandas build its OWN fsspec filesystem with
        # no storage_options, so an s3 GRR against a non-AWS endpoint
        # (MinIO, Ceph) would be unreachable -- the protocol is the only
        # thing that knows the endpoint.  Reading the raw stream costs the
        # ``.gz`` detection pandas does for a url, so hand pandas the same
        # answer it would have inferred from the name.
        params.setdefault(
            "compression", infer_compression(file_name, "infer"))
        with resource.proto.open_raw_file(
                resource, file_name, mode="rb") as infile:
            result = pd.read_csv(infile, **params)
    elif file_format == "excel":
        with resource.proto.open_raw_file(
                resource, file_name, mode="rb") as infile:
            result = pd.read_excel(infile, **params)
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
        raise ValueError(  # ruff: ignore[type-check-without-type-error]
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
