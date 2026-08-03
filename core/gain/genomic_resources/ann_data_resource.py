"""Loading helpers for ``ann_data`` genomic resources."""

import os
from typing import Any

import anndata as ad

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
)

logger = logging.getLogger(__name__)

# The 10x matrix-market triple is addressed by directory plus a shared
# prefix, so the matrix member is what the config names and the suffix is
# what identifies it.  Longest first -- ``matrix.mtx.gz`` also ends with
# neither of the plain-text spellings, but keeping the order explicit means
# a future ``.mtx.bz2`` cannot silently match the wrong branch.
_MTX_SUFFIXES = ("matrix.mtx.gz", "matrix.mtx")

# Suffix -> the format assumed when the config does not spell one out.
_SUFFIX_TO_DEFAULT_FORMAT = {
    ".h5ad": "h5ad",
    "matrix.mtx.gz": "10x_mtx",
    "matrix.mtx": "10x_mtx",
    ".h5": "10x_h5",
}
_FALLBACK_FORMAT = "h5ad"


def mtx_file_to_dir_and_prefix(mtx_file_name: str) -> tuple[str, str | None]:
    """Split a 10x matrix file name into its directory and shared prefix.

    ``scanpy.read_10x_mtx`` takes the directory holding the
    ``barcodes``/``features``/``matrix`` triple plus the prefix the three
    share, and spells "no prefix" as ``None`` rather than ``""`` -- so an
    empty prefix is normalised to ``None`` here.
    """
    dirname = os.path.dirname(mtx_file_name)
    basename = os.path.basename(mtx_file_name)
    for suffix in _MTX_SUFFIXES:
        if basename.endswith(suffix):
            prefix = basename[:-len(suffix)]
            break
    else:
        raise ValueError(
            f"not a 10x matrix file name: {mtx_file_name}; expected a name "
            f"ending in one of {', '.join(_MTX_SUFFIXES)}")

    return dirname, prefix or None


def _import_scanpy() -> Any:
    """Import scanpy on demand, reporting its absence as a config error.

    scanpy is an optional extra (``gain-core[ann_data_10x]``) -- it is
    needed only by the two 10x formats and pulls in a large scientific
    stack, so an ``ann_data`` resource in ``h5ad`` form must stay loadable
    without it.
    """
    # pylint: disable=import-outside-toplevel
    try:
        import scanpy
    except ImportError as exc:
        raise ValueError(
            "the 10x ann_data formats need scanpy, which is not installed; "
            "install the gain-core[ann_data_10x] extra") from exc

    return scanpy


def load_ann_data_from_resource(
    resource: GenomicResource | None,
) -> ad.AnnData:
    """Load an AnnData from an ``ann_data`` genomic resource."""
    if resource is None:
        raise ValueError("missing resource: None")

    if resource.get_type() != "ann_data":
        logger.error(
            "trying to open a resource %s of type "
            "%s as an ann_data", resource.resource_id, resource.get_type())
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

    default_format = _FALLBACK_FORMAT
    for sfx, dff in _SUFFIX_TO_DEFAULT_FORMAT.items():
        if file_name.endswith(sfx):
            default_format = dff
            break

    file_format = config.get("format", default_format)
    params = dict(config.get("parameters", {}))

    file_url = resource.get_file_url(file_name)
    if not file_url.startswith("file://"):
        logger.error(
            "ann_data resources can only be loaded from a file:// url, "
            "and not from %s for the ann_data %s",
            file_url, resource.resource_id)
        raise ValueError(
            f"cannot load the url {file_url} "
            f"for the ann_data {resource.resource_id}")

    file_path = file_url[len("file://"):]

    result: Any
    if file_format == "h5ad":
        result = ad.read_h5ad(file_path, backed="r", **params)
    elif file_format == "10x_mtx":
        dirname, prefix = mtx_file_to_dir_and_prefix(file_path)
        result = _import_scanpy().read_10x_mtx(
            dirname, prefix=prefix, **params)
    elif file_format == "10x_h5":
        result = _import_scanpy().read_10x_h5(file_path, **params)
    else:
        logger.error(
            "unknown format %s for the ann_data %s",
            file_format, resource.resource_id)
        raise ValueError(
            f"Unknown format {file_format} "
            f"for the ann_data {resource.resource_id}")

    if not isinstance(result, ad.AnnData):
        logger.error(
            "the parameters of the ann_data %s produced a %s "
            "instead of an AnnData",
            resource.resource_id, type(result).__name__)
        raise ValueError(  # noqa: TRY004
            f"parameters of {resource.resource_id} produced a "
            f"{type(result).__name__}, not an AnnData")

    return result


def load_ann_data_from_resource_id(
    resource_id: str, grr: GenomicResourceRepo | None = None,
) -> ad.AnnData:
    """Load an ann_data from a genomic resource id."""
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    if grr is None:
        grr = build_genomic_resource_repository()

    return load_ann_data_from_resource(grr.get_resource(resource_id))
