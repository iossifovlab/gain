"""gain's readers for the two 10x Genomics formats.

The Matrix Market triple and the 10x-Genomics HDF5, both built on
``anndata`` + ``pandas`` + ``h5py`` + ``scipy``, which gain already depends
on.  See :doc:`ADR 0014 </adr/0014-gain-owns-the-10x-readers>` for why the
work is here rather than delegated to scanpy, and for the parameter surface
these readers define.
"""
from __future__ import annotations

import dataclasses
import gzip
import types
import warnings
from collections.abc import Mapping
from typing import Any

import h5py
import numpy as np
import pandas as pd
from anndata import AnnData
from anndata.io import read_mtx
from anndata.utils import make_index_unique
from scipy.sparse import csr_matrix

from gain import logging

logger = logging.getLogger(__name__)

# The feature table's three columns, in the order CellRanger writes them.
# v2 ships only the first two and calls the file ``genes.tsv``.
_FEATURE_ID = 0
_FEATURE_SYMBOL = 1
_FEATURE_TYPE = 2

_BY_SYMBOL = "gene_symbols"
_BY_ID = "gene_ids"

# The feature type CellRanger gives genes; every other value is an assay
# riding along in the same table -- Peaks, Antibody Capture, CRISPR Guide
# Capture, Custom.
_GENE_EXPRESSION = "Gene Expression"

# The two groups a modern ("v3") 10x h5 is built round.  A file whose root
# carries genome-named groups instead is the legacy layout, which gain
# refuses rather than reads (ADR 0014).
_MATRIX_GROUP = "matrix"
_FEATURES_GROUP = "features"

# The ``features/`` datasets that already have a place in the variable
# table, and are therefore not copied into it a second time as metadata.
# ``gene_id`` is here because its PRESENCE selects the probe-barcode
# layout, which gain refuses outright rather than half-reads.
_NAMED_FEATURE_DATASETS = frozenset(
    {"name", "feature_type", "id", "gene_id", "_all_tag_keys"})

# The per-feature metadata the ``genome`` parameter selects on.
_GENOME_COLUMN = "genome"

# The dataset whose PRESENCE identifies a probe-barcode matrix, in which
# ``id`` names the probe rather than the gene.
_PROBE_ID_DATASET = "gene_id"

# ``parameters:`` is a surface gain defines, not a passthrough to somebody
# else's signature.  A key is in exactly one of these three sets, and the
# message a rejected one produces says which and why.
_OFF_REPOSITORY = (
    "it would read bytes that are not in the manifest, not hashed, and "
    "not served by the repository"
)
_SCANPY_CACHE = "it is scanpy's own h5ad cache, which gain does not maintain"

_MTX_DERIVED_PARAMETERS = {
    "prefix": "gain derives it from the resource's file: key",
    "compressed": "gain resolves compression from the resource's manifest",
}
_MTX_REFUSED_PARAMETERS = {
    "cache": _SCANPY_CACHE,
    "cache_compression": _SCANPY_CACHE,
    "backup_url": _OFF_REPOSITORY,
}

# The h5 read takes no prefix and no compression -- the resource names one
# file and HDF5 carries its own -- so nothing is derived, and the only key
# worth refusing by name is the one that reads off-repository bytes.
_H5_REFUSED_PARAMETERS = {
    "backup_url": _OFF_REPOSITORY,
}


@dataclasses.dataclass(frozen=True)
class TenXMtxParameters:
    """The knobs a ``10x_mtx`` resource may set, already validated."""

    var_names: str = _BY_SYMBOL
    make_unique: bool = True
    gex_only: bool = True


def _check_bool(
    name: str, value: Any, resource_id: str, *, file_format: str,
) -> bool:
    if not isinstance(value, bool):
        # A misconfigured resource, not a caller passing the wrong type --
        # and the loader reports every one of those as a ValueError.
        raise ValueError(  # noqa: TRY004
            f"the {file_format} parameter {name!r} of the ann_data "
            f"{resource_id} must be true or false, not {value!r}")
    return value


def _check_parameter_names(
    parameters: Mapping[str, Any],
    resource_id: str,
    *,
    file_format: str,
    knobs: type,
    refused: Mapping[str, str],
    derived: Mapping[str, str] = types.MappingProxyType({}),
) -> None:
    """Refuse every key that is not one of ``knobs``' fields, saying why.

    Three kinds of rejection, and which one a key gets is the whole of what
    the message has to convey: refused on principle, answered by gain from
    the resource itself, or simply not a thing.  Shared by both 10x
    readers so that a resource gets the same account of its
    ``parameters:`` block whichever format it is in.
    """
    for name, reason in refused.items():
        if name in parameters:
            raise ValueError(
                f"the {file_format} parameter {name!r} of the ann_data "
                f"{resource_id} is refused: {reason}")

    for name, reason in derived.items():
        if name in parameters:
            raise ValueError(
                f"the {file_format} parameter {name!r} of the ann_data "
                f"{resource_id} is not accepted: {reason}")

    known = {field.name for field in dataclasses.fields(knobs)}
    unknown = sorted(set(parameters) - known)
    if unknown:
        raise ValueError(
            f"unknown {file_format} parameter(s) {', '.join(unknown)} of "
            f"the ann_data {resource_id}; gain accepts "
            f"{', '.join(sorted(known))}")


def parse_10x_mtx_parameters(
    parameters: Mapping[str, Any], resource_id: str,
) -> TenXMtxParameters:
    """Validate a resource's ``parameters:`` block into the knobs gain has.

    An unrecognised key raises rather than being forwarded, so a typo is
    reported instead of silently doing nothing -- and so is a key that used
    to reach scanpy and now has no meaning here.
    """
    _check_parameter_names(
        parameters, resource_id, file_format="10x_mtx",
        knobs=TenXMtxParameters, refused=_MTX_REFUSED_PARAMETERS,
        derived=_MTX_DERIVED_PARAMETERS)

    var_names = parameters.get("var_names", _BY_SYMBOL)
    if var_names not in (_BY_SYMBOL, _BY_ID):
        raise ValueError(
            f"the 10x_mtx parameter 'var_names' of the ann_data "
            f"{resource_id} must be {_BY_SYMBOL!r} or {_BY_ID!r}, "
            f"not {var_names!r}")

    return TenXMtxParameters(
        var_names=var_names,
        make_unique=_check_bool(
            "make_unique", parameters.get("make_unique", True), resource_id,
            file_format="10x_mtx"),
        gex_only=_check_bool(
            "gex_only", parameters.get("gex_only", True), resource_id,
            file_format="10x_mtx"),
    )


def _keep_gene_expression(
    ann_data: AnnData, resource_id: str,
) -> AnnData:
    """Drop every feature that is not gene expression, saying what went.

    ``gex_only`` defaults to ``True``, which is scanpy's default and what
    every existing resource was built with, so the filter itself is not the
    problem -- its silence was.  A CellRanger-ARC resource carries chromatin
    peaks in the same feature table, and reading one as though it held only
    genes discards most of it without a word.
    """
    feature_types = ann_data.var["feature_types"]
    dropped = feature_types != _GENE_EXPRESSION
    if dropped.any():
        breakdown = ", ".join(
            f"{name} ({count})"
            for name, count in feature_types[dropped].value_counts().items())
        logger.warning(
            "gex_only is on for the ann_data %s, so %d of its %d features "
            "are dropped: %s. Set 'gex_only: false' in the resource's "
            "parameters to keep them.",
            resource_id, int(dropped.sum()), len(feature_types), breakdown)

    with warnings.catch_warnings():
        # Subsetting re-checks the variable index, and anndata advises
        # calling ``var_names_make_unique`` -- which is precisely what a
        # resource setting ``make_unique: false`` has declined to do.
        # scanpy silences the same warning, for the same reason.
        warnings.filterwarnings(
            "ignore", r".*names are not unique", UserWarning)
        return ann_data[:, ~dropped].copy()


def _read_feature_table(
    features_path: str, *, legacy: bool,
) -> pd.DataFrame:
    """Read a feature table, checking it is as wide as its layout promises.

    v2's ``genes.tsv`` carries an id and a symbol; v3's ``features.tsv``
    adds the feature type.  A v3 file with only two columns is a v2 one
    under a v3 name, and reading the missing column otherwise surfaces as
    a bare ``KeyError: 2`` from pandas, naming neither the file nor the
    resource.
    """
    features = pd.read_csv(features_path, header=None, sep="\t")
    wanted = 2 if legacy else 3
    if len(features.columns) < wanted:
        raise ValueError(
            f"the 10x feature table {features_path} has "
            f"{len(features.columns)} column(s); a "
            f"{'v2 genes.tsv' if legacy else 'v3 features.tsv'} has "
            f"{'two columns' if legacy else 'three columns'} "
            f"(gene id, gene symbol"
            f"{'' if legacy else ', feature type'})")

    return features


def _apply_var(
    ann_data: AnnData,
    features: pd.DataFrame,
    parameters: TenXMtxParameters,
    *,
    legacy: bool,
) -> None:
    """Index the variables as asked, keeping the other name beside them."""
    if parameters.var_names == _BY_SYMBOL:
        var_names = pd.Index(features[_FEATURE_SYMBOL].array)
        if parameters.make_unique:
            var_names = make_index_unique(var_names)
        ann_data.var_names = var_names.astype("str")
        ann_data.var[_BY_ID] = features[_FEATURE_ID].array
    else:
        ann_data.var_names = features[_FEATURE_ID].array.astype("str")
        ann_data.var[_BY_SYMBOL] = features[_FEATURE_SYMBOL].array

    if not legacy:
        ann_data.var["feature_types"] = features[_FEATURE_TYPE].array


def _read_matrix_market_shape(matrix_path: str) -> tuple[int, int]:
    """Return a Matrix Market file's declared ``(rows, columns)``.

    The size line is the first that is neither the ``%%MatrixMarket``
    banner nor a ``%`` comment, and it carries the two dimensions and the
    non-zero count.  Reading it costs one line; reading the entries it
    announces costs 16 bytes each through ``scipy.io.mmread``.
    """
    opener = gzip.open if matrix_path.endswith(".gz") else open
    with opener(matrix_path, "rt") as infile:
        for line in infile:
            if line.startswith("%") or not line.strip():
                continue
            rows, columns, _nnz = line.split()
            return int(rows), int(columns)

    raise ValueError(
        f"no Matrix Market size line in {matrix_path}")


def _empty_matrix(matrix_path: str) -> AnnData:
    """Return the declared shape with no entries in it.

    A real sparse matrix rather than ``None``: ``AnnData._gen_repr`` emits
    its ``layers: None (.X)`` line only when ``X`` is set, so an unset one
    would make the statistic differ from the full read's by that line.
    """
    rows, columns = _read_matrix_market_shape(matrix_path)
    return AnnData(csr_matrix((rows, columns), dtype=np.float32))


def read_10x_mtx(
    matrix_path: str,
    barcodes_path: str,
    features_path: str,
    *,
    resource_id: str,
    legacy: bool = False,
    parameters: TenXMtxParameters | None = None,
    matrix_free: bool = False,
) -> AnnData:
    """Read a 10x matrix-market triple into an AnnData.

    The three members are named outright rather than assembled from a
    directory and a prefix: which names they carry is a question about the
    resource's layout, and the resource is what answers it.

    10x writes features as rows and barcodes as columns, so the matrix is
    transposed into the cells x genes an AnnData carries.  ``legacy`` marks
    the CellRanger v2 feature table, which has no feature-type column --
    and therefore nothing for ``gex_only`` to filter on.

    ``resource_id`` names the resource in diagnostics only.

    With ``matrix_free``, ``X`` is an all-zero matrix of the declared shape
    and IS NOT THE RESOURCE'S DATA.  Everything else -- both axis tables,
    the shape, the feature-type filter -- is built by the same code as an
    ordinary read, so anything derived from those is identical.  It exists
    for the statistics build, which reads neither ``X`` nor anything
    computed from it.
    """
    parameters = parameters if parameters is not None else TenXMtxParameters()

    # ``AnnData.T`` is untyped upstream, hence the annotation.
    ann_data: AnnData = (
        _empty_matrix(matrix_path) if matrix_free
        else read_mtx(matrix_path)
    ).T

    features = _read_feature_table(features_path, legacy=legacy)
    _apply_var(ann_data, features, parameters, legacy=legacy)

    # Read the barcodes the way scanpy does -- no separator, so a barcode
    # carrying a tab would not be split, and only the first column is used.
    barcodes = pd.read_csv(barcodes_path, header=None)
    ann_data.obs_names = barcodes[0].array

    if legacy or not parameters.gex_only:
        return ann_data

    return _keep_gene_expression(ann_data, resource_id)


@dataclasses.dataclass(frozen=True)
class TenXH5Parameters:
    """The knobs a ``10x_h5`` resource may set, already validated.

    Fewer than the triple's, because the h5 answers for itself what the
    triple needs telling: it names its own features, so there is no
    ``var_names`` choice to make and nothing to make unique.
    """

    gex_only: bool = True
    genome: str | None = None


def parse_10x_h5_parameters(
    parameters: Mapping[str, Any], resource_id: str,
) -> TenXH5Parameters:
    """Validate a ``10x_h5`` resource's ``parameters:`` block."""
    _check_parameter_names(
        parameters, resource_id, file_format="10x_h5",
        knobs=TenXH5Parameters, refused=_H5_REFUSED_PARAMETERS)

    genome = parameters.get("genome")
    if genome is not None and not isinstance(genome, str):
        raise ValueError(
            f"the 10x_h5 parameter 'genome' of the ann_data {resource_id} "
            f"must be a genome name, not {genome!r}")

    return TenXH5Parameters(
        gex_only=_check_bool(
            "gex_only", parameters.get("gex_only", True), resource_id,
            file_format="10x_h5"),
        genome=genome,
    )


def _h5_matrix(h5: h5py.File, resource_id: str) -> h5py.Group:
    """Return the single ``matrix`` group a modern 10x h5 is built round.

    A file that has none is the legacy CellRanger v2 layout, whose root
    carries a group per genome instead.  gain refuses it rather than
    guessing (ADR 0014): no resource we have is one, so the builder
    realizes no fixture for it, and a reader with no fixture is a reader
    with no way of knowing it got it right.
    """
    if _MATRIX_GROUP not in h5:
        raise ValueError(
            f"the ann_data {resource_id} is a 10x h5 with no "
            f"{_MATRIX_GROUP!r} group, so it is the legacy per-genome "
            f"layout, which gain does not read; its root carries "
            f"{', '.join(sorted(h5))}")

    return h5[_MATRIX_GROUP]


def _h5_features(matrix: h5py.Group, resource_id: str) -> h5py.Group:
    """Return the feature table, refusing the probe-barcode variant.

    A ``gene_id`` dataset is what identifies a probe-barcode matrix, in
    which ``id`` means the probe and ``gene_id`` the gene -- so reading
    it as a feature-barcode file would silently index the variables by
    the wrong thing.  Out of scope for the same reason the legacy layout
    is, and refused for the same reason.
    """
    if _FEATURES_GROUP not in matrix:
        raise ValueError(
            f"the ann_data {resource_id} is a 10x h5 with no "
            f"{_FEATURES_GROUP!r} group, so it describes no variables")

    features = matrix[_FEATURES_GROUP]
    if _PROBE_ID_DATASET in features:
        raise ValueError(
            f"the ann_data {resource_id} is a probe-barcode 10x h5 -- its "
            f"features carry a {_PROBE_ID_DATASET!r} dataset -- and gain "
            f"reads the feature-barcode layout only")

    return features


def _read_x(matrix: h5py.Group, *, matrix_free: bool) -> csr_matrix:
    """Read the stored CSC of features x barcodes as a cells x genes CSR.

    The two are bit-for-bit the same buffers, so nothing is transposed
    here -- only the declared shape is read the other way round.

    CellRanger stores counts as ``int32``.  They are reinterpreted in
    place rather than converted into a second array, because on the
    largest resource the buffer is 605 MB and ``astype`` would hold both
    at once.

    With ``matrix_free`` the shape is all that is read: ``shape`` is two
    integers, and the three datasets it describes are never opened.
    """
    n_cols, n_rows = matrix["shape"][()]

    if matrix_free:
        return csr_matrix((n_rows, n_cols), dtype=np.float32)

    stored = matrix["data"][()]
    data = stored
    if stored.dtype == np.dtype("int32"):
        data = stored.view("float32")
        data[:] = stored

    return csr_matrix(
        (data, matrix["indices"][()], matrix["indptr"][()]),
        shape=(n_rows, n_cols))


def _keep_genome(
    ann_data: AnnData, genome: str, resource_id: str,
) -> AnnData:
    """Keep only the features of ``genome``, refusing an absent one.

    Applied BEFORE the feature-type filter, so a resource asking for both
    gets the same features whichever order it thinks they compose in.
    """
    if _GENOME_COLUMN not in ann_data.var.columns:
        raise ValueError(
            f"the 10x_h5 parameter 'genome' of the ann_data {resource_id} "
            f"selects among per-feature genomes, and its file carries no "
            f"{_GENOME_COLUMN!r} feature dataset to select on")

    genomes = ann_data.var[_GENOME_COLUMN]
    if genome not in set(genomes):
        raise ValueError(
            f"the 10x_h5 parameter 'genome' of the ann_data {resource_id} "
            f"asks for {genome!r}, which its file does not carry; "
            f"available: {', '.join(sorted(set(genomes)))}")

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", r".*names are not unique", UserWarning)
        return ann_data[:, genomes == genome].copy()


def _read_var(features: h5py.Group) -> dict[str, np.ndarray]:
    """Build the variable table from the ``features`` group.

    Three datasets are named by 10x and become the index, ``gene_ids`` and
    ``feature_types``, in that order.  Everything else under ``features/``
    is per-feature metadata whose meaning gain does not need to know, and
    is copied in verbatim behind them -- ``genome`` and ``interval`` in
    the files we have.  A resource's stored ``describe_var.csv`` is a
    header in this order, so the order is part of the format.

    ``_all_tag_keys`` is 10x's own index OF the metadata datasets rather
    than one of them, and is skipped for the same reason ``id`` is: it
    already has a home.
    """
    var: dict[str, np.ndarray] = {
        "var_names": features["name"][()].astype(str),
        "gene_ids": features["id"][()].astype(str),
        "feature_types": features["feature_type"][()].astype(str),
    }
    var.update(
        (
            name,
            # A boolean dataset stays boolean; everything else 10x writes
            # is a byte string, which is only useful decoded.
            dataset[()].astype(bool if dataset.dtype.kind == "b" else str),
        )
        for name, dataset in features.items()
        if isinstance(dataset, h5py.Dataset)
        and name not in _NAMED_FEATURE_DATASETS
    )
    return var


def read_10x_h5(
    file_path: str,
    *,
    resource_id: str,
    parameters: TenXH5Parameters | None = None,
    matrix_free: bool = False,
) -> AnnData:
    """Read a 10x-Genomics HDF5 into an AnnData.

    ``resource_id`` names the resource in diagnostics only.

    With ``matrix_free``, ``X`` is an all-zero matrix of the declared
    shape and IS NOT THE RESOURCE'S DATA.  Everything else -- both axis
    tables, the shape, the genome and feature-type filters -- is built by
    the same code as an ordinary read, so anything derived from those is
    identical.  It exists for the statistics build, which reads neither
    ``X`` nor anything computed from it.
    """
    parameters = parameters if parameters is not None else TenXH5Parameters()

    with h5py.File(file_path, "r") as h5:
        matrix = _h5_matrix(h5, resource_id)

        ann_data = AnnData(
            _read_x(matrix, matrix_free=matrix_free),
            obs={"obs_names": matrix["barcodes"][()].astype(str)},
            var=_read_var(_h5_features(matrix, resource_id)),
        )

    if parameters.genome is not None:
        ann_data = _keep_genome(ann_data, parameters.genome, resource_id)

    if not parameters.gex_only:
        return ann_data

    return _keep_gene_expression(ann_data, resource_id)
