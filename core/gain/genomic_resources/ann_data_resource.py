"""Loading helpers for ``ann_data`` genomic resources."""

import contextlib
import os
from collections.abc import Iterator
from typing import Any

import anndata as ad

from gain import logging
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    Manifest,
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

    # ``os.path.dirname`` of a bare name is ``""``, which scanpy does not
    # read as the working directory the way ``"."`` does.
    return dirname or ".", prefix or None


# The two 10x layouts, keyed by the sidecar whose presence identifies each.
# CellRanger v2 ships the triple as plain text and calls the feature table
# ``genes.tsv``; v3 gzips all three and calls it ``features.tsv.gz``.
_LEGACY_SIDECARS = ("barcodes.tsv", "genes.tsv")
_CURRENT_SIDECARS = ("barcodes.tsv.gz", "features.tsv.gz")

# What ``scanpy.read_10x_mtx`` itself probes for to tell the layouts apart.
_LEGACY_MARKER = "genes.tsv"


def is_10x_matrix_name(file_name: str) -> bool:
    """Return whether ``file_name`` names a 10x matrix-market member."""
    return any(file_name.endswith(suffix) for suffix in _MTX_SUFFIXES)


def _mtx_prefix(file_name: str) -> str:
    """Return the shared prefix of a 10x matrix member's name."""
    dirname, prefix = mtx_file_to_dir_and_prefix(file_name)
    head = "" if dirname == "." else f"{dirname}/"
    return f"{head}{prefix or ''}"


def _sidecars_for_layout(prefix: str, *, legacy: bool) -> set[str]:
    names = _LEGACY_SIDECARS if legacy else _CURRENT_SIDECARS
    return {f"{prefix}{name}" for name in names}


def resolve_10x_sidecars(manifest: Manifest, file_name: str) -> set[str]:
    """Return the sidecars of the 10x matrix ``file_name``, per the manifest.

    The layout is decided the way ``scanpy.read_10x_mtx`` decides it -- by
    asking whether a ``genes.tsv`` sits beside the matrix -- with the
    manifest standing in for scanpy's filesystem probe.  Resolution is
    manifest-driven for the same reason the tabix index is: the manifest is
    already loaded and is protocol-agnostic, whereas probing costs a
    network round trip per candidate on http and s3.
    """
    prefix = _mtx_prefix(file_name)
    legacy = f"{prefix}{_LEGACY_MARKER}" in manifest
    return _sidecars_for_layout(prefix, legacy=legacy)


def resolve_10x_sidecars_for_read(
    resource: GenomicResource, file_name: str,
) -> set[str]:
    """Return the sidecars to read ``file_name`` with, never building.

    Shaped like :func:`resolve_tabix_index_filename_for_read`, and for the
    same reason: a read must stay a pure read, and
    :meth:`GenomicResource.get_manifest` would *build* -- md5-scanning the
    whole resource and writing state files -- for a resource that carries
    no ``.MANIFEST`` (gain#430).  With no manifest at hand, falls back to
    probing the resource for the legacy marker, which is literally
    scanpy's own test.
    """
    manifest = resource.get_loaded_manifest()
    if manifest is not None:
        return resolve_10x_sidecars(manifest, file_name)

    prefix = _mtx_prefix(file_name)
    legacy = False
    try:
        legacy = resource.file_exists(f"{prefix}{_LEGACY_MARKER}")
    except OSError:
        logger.debug(
            "unable to probe the 10x layout of %s in resource %s",
            file_name, resource.resource_id, exc_info=True)
    return _sidecars_for_layout(prefix, legacy=legacy)


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
        # ``parameters:`` is an unrestricted passthrough, and ``backed`` is
        # one of the keys this loader supplies -- a config naming it used to
        # collide with the argument below and die with a duplicate-kwarg
        # TypeError rather than a message about the resource.  A config that
        # states its own value keeps winning, exactly as data_frame's
        # separator does.
        params.setdefault("backed", "r")
        result = ad.read_h5ad(file_path, **params)
    elif file_format == "10x_mtx":
        # scanpy reads the barcodes and the feature table straight off the
        # filesystem, bypassing the protocol entirely, so on a caching GRR
        # they have to be on disk before it is called.  Asking for each
        # sidecar's url is what triggers the fetch -- a caching protocol
        # refreshes the file it is asked to name.  Only the matrix member
        # used to be named, and a lazy load (as against an explicit
        # grr_manage prefetch) then found the sidecars missing.
        #
        # Failing to fetch one is logged rather than raised: an incomplete
        # triple is scanpy's error to report, and it names the member and
        # the directory, which an OSError from the fetch does not.
        for sidecar in sorted(
                resolve_10x_sidecars_for_read(resource, file_name)):
            try:
                resource.get_file_url(sidecar)
            except OSError:
                logger.debug(
                    "unable to fetch the 10x sidecar %s of resource %s",
                    sidecar, resource.resource_id, exc_info=True)
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


@contextlib.contextmanager
def open_ann_data_from_resource(
    resource: GenomicResource | None,
) -> Iterator[ad.AnnData]:
    """Open an AnnData from a resource and close it on exit.

    The preferred entry point, and the one the statistics build uses.
    ``backed="r"`` -- the default, and what keeps a multi-gigabyte X out of
    a dask worker -- leaves an open h5py file behind for as long as the
    AnnData lives.  A repo sweep that opens one per resource and relies on
    a garbage collection that may never come is gain#480's shape, so
    ownership is explicit here, in the spirit of ``with score.open()``.

    :func:`load_ann_data_from_resource` remains for a caller that wants to
    keep the handle; that caller owns the close.
    """
    ann_data = load_ann_data_from_resource(resource)
    try:
        yield ann_data
    finally:
        # A config may turn ``backed`` off, and an in-memory AnnData has no
        # file manager to close.
        if ann_data.isbacked:
            ann_data.file.close()


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
