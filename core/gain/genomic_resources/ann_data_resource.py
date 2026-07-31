"""Loading helpers for ``add_data`` genomic resources."""

from typing import Any

# anndata version 0.13.2   pyhd8ed1ab_0    conda-forge
import anndata as ad
import os

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

logger = logging.getLogger(__name__)


def mtx_file_to_dir_and_prefix(mtx_file_name: str) -> tuple[str, str]:
    dr = os.path.dirname(mtx_file_name)
    fn = os.path.basename(mtx_file_name)
    if fn.endswith("matrix.mtx.gz"):
        pref = fn[:-13]
    elif fn.endswith("matrix.mtx"):
        pref = fn[:-10]
    else:
        raise Exception("wrong suffice for an mtx file.")
    if pref == "":
        pref = None
    return dr, pref


def load_ann_data_from_resource(
    resource: GenomicResource | None,
) -> ad.AnnData:
    """Load a AnnData from a ``data_frame`` genomic resource."""
    if resource is None:
        raise ValueError(f"missing resource {resource}")

    if resource.get_type() != "ann_data":
        logger.error(
            "trying to open a resource %s of type "
            "%s as a ann_data", resource.resource_id, resource.get_type())
        raise ValueError(f"wrong resource type: {resource.resource_id}")

    config = resource.get_config()

    try:
        file_name = config["file"]
    except KeyError as exc:
        logger.exception(
            "the ann_data resource %s needs a file parameter",
            resource.resource_id)
        raise ValueError(
            f"missing file parameter for: {resource.resource_id}") from exc

    sfx_to_default_format = {
        ".h5ad": "h5ad",
        "matrix.mtx.gz": "10x_mtx",
        "matrix.mtx": "10x_mtx",
        ".h5": "10x_h5"
    }
    default_format = "h5ad"
    for sfx, dff in sfx_to_default_format.items():
        if file_name.endswith(sfx):
            default_format = dff
            break

    file_format = config.get("format", default_format)
    params = dict(config.get("parameters", {}))

    file_url = resource.get_file_url(file_name)
    if not file_url.startswith("file://"):
        logger.error(
            "ann_data resrouces can only be loaded for a file:// url, "
            "and not from %s for the ann_data %s",
            file_url, resource.resource_id)
        raise ValueError(
            f"Can't not load the url {file_url}"
            f"for the ann_data {resource.resource_id}")

    file_path = file_url[7:]

    result: Any = "gosho"
    if file_format == "h5ad":
        result = ad.read_h5ad(file_path, backed="r", **params)
    elif file_format == "10x_mtx":
        drr, pfx = mtx_file_to_dir_and_prefix(file_path)
        import scanpy as sc
        result = sc.read_10x_mtx(drr, prefix=pfx, **params)
    elif file_format == "10x_h5":
        # The scanpy version: 1.12.3  pyhd8ed1ab_0    conda-forge
        import scanpy as sc
        result = sc.read_10x_h5(file_path, **params)
    else:
        logger.error(
            "unknown format %s for the ann_data %s",
            file_format, resource.resource_id)
        raise ValueError(
            f"Unknown format {file_format} "
            f"for the dataframe {resource.resource_id}")

    if not isinstance(result, ad.AnnData):
        logger.error(
            "the parameters of the data_frame %s produced a %s "
            "instead of an AnnData",
            resource.resource_id, type(result).__name__)
        raise ValueError(  # noqa: TRY004
            f"parameters of {resource.resource_id} produced a "
            f"{type(result).__name__}, not a AnnData")

    return result


def load_ann_data_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> ad.AnnData:
    """Load a ann_data from a genomic resource id."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    if grr is None:
        grr = build_genomic_resource_repository()

    return load_ann_data_from_resource(grr.get_resource(resource_id))
