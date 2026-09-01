"""Loading helpers for ``ann_data`` genomic resources."""

import dataclasses
from collections.abc import Callable, Mapping
from typing import Any

import anndata as ad

from gain import logging
from gain.genomic_resources.ann_data_10x import (
    parse_10x_h5_parameters,
    parse_10x_mtx_parameters,
    read_10x_h5,
    read_10x_mtx,
)
from gain.genomic_resources.fsspec_protocol import _strip_url_userinfo
from gain.genomic_resources.repository import (
    GenomicResource,
    GenomicResourceRepo,
    Manifest,
)

logger = logging.getLogger(__name__)

# The config names the matrix member of a 10x triple, and one of these
# suffixes is what identifies it as one.  Longest first -- keeping the
# order explicit means a future ``.mtx.bz2`` cannot silently match the
# wrong branch.
_MTX_SUFFIXES = ("matrix.mtx.gz", "matrix.mtx")

# Suffix -> the format assumed when the config does not spell one out.
_SUFFIX_TO_DEFAULT_FORMAT = {
    ".h5ad": "h5ad",
    "matrix.mtx.gz": "10x_mtx",
    "matrix.mtx": "10x_mtx",
    ".h5": "10x_h5",
}
_FALLBACK_FORMAT = "h5ad"


# CellRanger v2 ships the triple as plain text and calls the feature table
# ``genes.tsv``, which carries no feature type; v3 calls it ``features.tsv``
# and adds one.  Gzipping is a third, independent axis: CellRanger v3 gzips
# all three members, STARsolo writes the same v3 layout uncompressed.
_LEGACY_SIDECARS = ("barcodes.tsv", "genes.tsv")
_CURRENT_SIDECARS = ("barcodes.tsv", "features.tsv")

# What ``scanpy.read_10x_mtx`` itself probes for to tell v2 from v3.
_LEGACY_MARKER = "genes.tsv"

# What is probed for to tell a compressed v3 triple from a plain one.  The
# gzipped spelling is the default because it is what CellRanger writes, so
# a resource missing its feature table reports the name it ought to have.
_CURRENT_MARKER = "features.tsv"


def is_10x_matrix_name(file_name: str) -> bool:
    """Return whether ``file_name`` names a 10x matrix-market member."""
    return any(file_name.endswith(suffix) for suffix in _MTX_SUFFIXES)


def _mtx_prefix(file_name: str) -> str:
    """Return what a 10x triple's three members share, as a resource name.

    The matrix member's name minus its ``matrix.mtx[.gz]`` suffix, so any
    directory stays attached and the sidecars are named by appending to
    this.  ``data/sample1_matrix.mtx.gz`` shares ``data/sample1_``; a bare
    ``matrix.mtx.gz`` shares nothing and yields ``""``.
    """
    for suffix in _MTX_SUFFIXES:
        if file_name.endswith(suffix):
            return file_name[:-len(suffix)]

    raise ValueError(
        f"not a 10x matrix file name: {file_name}; expected a name "
        f"ending in one of {', '.join(_MTX_SUFFIXES)}")


@dataclasses.dataclass(frozen=True)
class TenXMtxLayout:
    """The three members of a 10x matrix-market triple, by resource name.

    Which names the triple carries is a question about the resource, and
    resolving it once here is what keeps the read, the statistics inputs
    and the cache prefetch from each answering it differently.
    """

    barcodes: str
    features: str
    legacy: bool

    @property
    def sidecars(self) -> set[str]:
        """Return the two non-matrix members."""
        return {self.barcodes, self.features}


def _resolve_layout(
    prefix: str, contains: Callable[[str], bool],
) -> TenXMtxLayout:
    """Resolve a triple's layout by asking ``contains`` about candidates.

    Two independent questions, in order: v2 or v3, which the presence of a
    ``genes.tsv`` answers exactly as scanpy's own probe does; and then, for
    v3 only, gzipped or not.  ``compressed`` is therefore never a config
    key -- the resource already states the answer, and a config that
    disagreed with it could only be wrong.
    """
    if contains(f"{prefix}{_LEGACY_MARKER}"):
        barcodes, features = _LEGACY_SIDECARS
        return TenXMtxLayout(
            f"{prefix}{barcodes}", f"{prefix}{features}", legacy=True)

    barcodes, features = _CURRENT_SIDECARS
    suffix = "" if contains(f"{prefix}{_CURRENT_MARKER}") else ".gz"
    return TenXMtxLayout(
        f"{prefix}{barcodes}{suffix}", f"{prefix}{features}{suffix}",
        legacy=False)


def resolve_10x_layout(manifest: Manifest, file_name: str) -> TenXMtxLayout:
    """Return the layout of the 10x matrix ``file_name``, per the manifest.

    Resolution is manifest-driven for the same reason the tabix index is:
    the manifest is already loaded and is protocol-agnostic, whereas
    probing costs a network round trip per candidate on http and s3.

    This and :func:`resolve_10x_layout_for_read` are the pair, and they
    differ only in where the answer comes from -- exactly as
    :func:`resolve_tabix_index_filename` and its ``_for_read`` twin do.
    A caller that only wants the two names takes ``.sidecars``.
    """
    return _resolve_layout(
        _mtx_prefix(file_name), lambda name: name in manifest)


def resolve_10x_layout_for_read(
    resource: GenomicResource, file_name: str,
) -> TenXMtxLayout:
    """Return the layout to read ``file_name`` with, never building.

    Shaped like :func:`resolve_tabix_index_filename_for_read`, and for the
    same reason: a read must stay a pure read, and
    :meth:`GenomicResource.get_manifest` would *build* -- md5-scanning the
    whole resource and writing state files -- for a resource that carries
    no ``.MANIFEST`` (gain#430).  With no manifest at hand, falls back to
    probing the resource itself for the same two markers.
    """
    manifest = resource.get_loaded_manifest()
    if manifest is not None:
        return resolve_10x_layout(manifest, file_name)

    def probe(name: str) -> bool:
        try:
            return resource.file_exists(name)
        except OSError:
            logger.debug(
                "unable to probe for %s in resource %s",
                name, resource.resource_id, exc_info=True)
            return False

    return _resolve_layout(_mtx_prefix(file_name), probe)


def resolve_ann_data_format(config: Mapping[str, Any]) -> str:
    """Return the format an ``ann_data`` config is read as.

    A declared ``format:`` wins; otherwise the ``file:`` suffix decides, and
    a name matching none of them falls back to ``h5ad``.  A config with no
    ``file:`` gets the fallback too -- the missing key is the loader's error
    to report, and this is also reached from the statistics hash, which
    degrades rather than raising.

    The loader and the statistics hash both resolve through here so that the
    format a resource is *read* as and the format its hash *records* cannot
    disagree.  They did: the hash used to state the ``h5ad`` fallback
    outright, so an explicit ``format: h5ad`` added to a 10x config changed
    the read without changing the hash, and the statistics never rebuilt.
    """
    if "format" in config:
        return str(config["format"])

    file_name = config.get("file")
    if isinstance(file_name, str):
        for suffix, file_format in _SUFFIX_TO_DEFAULT_FORMAT.items():
            if file_name.endswith(suffix):
                return file_format

    return _FALLBACK_FORMAT


def _local_file_path(resource: GenomicResource, file_name: str) -> str:
    """Return the local path of ``file_name``, fetching it if cached.

    Asking for the url is what triggers a caching protocol's fetch, so this
    is also how a member is brought on disk before a reader that bypasses
    the protocol -- pandas and scipy both take paths -- is handed it.

    A file the resource cannot produce is reported the way every other
    misconfigured ``ann_data`` is, as a ``ValueError`` naming the resource.
    The fetch raises an ``OSError`` that names a path and nothing else, and
    all three members of a 10x triple come through here, so an incomplete
    triple would otherwise surface as a bare filesystem error with no clue
    which resource it came from.
    """
    try:
        file_url = resource.get_file_url(file_name)
    except OSError as exc:
        logger.exception(
            "unable to read %s of the ann_data %s",
            file_name, resource.resource_id)
        raise ValueError(
            f"cannot read {file_name} of the ann_data "
            f"{resource.resource_id}: {exc}") from exc

    if not file_url.startswith("file://"):
        # ``get_file_url`` returns the credential-BEARING fetch url on
        # purpose -- aiohttp and htslib read URL-embedded basic auth
        # straight off the url string -- so every user-visible rendering of
        # it has to drop the ``user:pass@`` userinfo first (#608).  The
        # unredacted url keeps driving the scheme test and the local path
        # below, which are internal.
        display_url = _strip_url_userinfo(file_url)
        logger.error(
            "ann_data resources can only be loaded from a file:// url, "
            "and not from %s for the ann_data %s",
            display_url, resource.resource_id)
        raise ValueError(
            f"cannot load the url {display_url} "
            f"for the ann_data {resource.resource_id}")

    return file_url[len("file://"):]


def load_ann_data_from_resource(
    resource: GenomicResource | None,
    *,
    matrix_free: bool = False,
) -> ad.AnnData:
    """Load an AnnData from an ``ann_data`` genomic resource.

    **The caller owns the file handle.**  An ``h5ad`` is read
    ``backed="r"`` -- the default, and what keeps a multi-gigabyte ``X``
    out of a dask worker -- which leaves an open h5py file behind for as
    long as the AnnData lives.  A repo sweep that loads one per resource
    and relies on a garbage collection that may never come is gain#480's
    shape, so a caller that loads in a loop closes with
    ``ann_data.file.close()`` when ``ann_data.isbacked``.  A 10x read is
    in memory and has no handle.

    ``matrix_free`` asks for a read that does not materialise the data
    matrix.  **The resulting ``X`` IS NOT THE RESOURCE'S DATA** -- it is an
    all-zero matrix of the right shape.  Everything else is built by the
    same code as an ordinary read, so both axis tables, the shape and the
    feature-type filter are identical, which is what lets the statistics
    build use it: ``AnnData._gen_repr`` skips ``X`` and ``describe`` reads
    ``obs`` and ``var``.  Reading the real matrix costs about 10 GB on the
    largest 10x resource to write 221 bytes of statistics.

    It is honoured only where it means something.  Both 10x formats
    honour it; ``h5ad`` ignores it -- ``backed="r"`` already keeps ``X``
    off the heap, and reproducing anndata's repr for an arbitrary h5ad
    would mean reimplementing its reader for no memory benefit (ADR
    0014).
    """
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

    file_format = resolve_ann_data_format(config)
    params = dict(config.get("parameters", {}))

    file_path = _local_file_path(resource, file_name)

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
        # The two sidecars are read by pandas, which takes a path and
        # bypasses the protocol entirely, so on a caching GRR they have to
        # be on disk first -- which is what asking for their local path
        # does, a caching protocol refreshing the file it is asked to name.
        layout = resolve_10x_layout_for_read(resource, file_name)
        result = read_10x_mtx(
            file_path,
            _local_file_path(resource, layout.barcodes),
            _local_file_path(resource, layout.features),
            resource_id=resource.resource_id,
            legacy=layout.legacy,
            parameters=parse_10x_mtx_parameters(
                params, resource.resource_id),
            matrix_free=matrix_free)
    elif file_format == "10x_h5":
        result = read_10x_h5(
            file_path,
            resource_id=resource.resource_id,
            parameters=parse_10x_h5_parameters(
                params, resource.resource_id),
            matrix_free=matrix_free)
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
        raise ValueError(  # ruff: ignore[type-check-without-type-error]
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
