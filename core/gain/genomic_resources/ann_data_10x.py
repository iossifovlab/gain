"""gain's reader for the 10x Genomics Matrix Market triple.

Built on ``anndata`` + ``pandas`` + ``scipy``, which gain already depends
on.  See :doc:`ADR 0014 </adr/0014-gain-owns-the-10x-readers>` for why the
work is here rather than delegated to scanpy, and for the parameter surface
this reader defines.
"""
from __future__ import annotations

import dataclasses
import gzip
import warnings
from collections.abc import Mapping
from typing import Any

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

# ``parameters:`` is a surface gain defines, not a passthrough to somebody
# else's signature.  A key is in exactly one of these three sets, and the
# message a rejected one produces says which and why.
_DERIVED_PARAMETERS = {
    "prefix": "gain derives it from the resource's file: key",
    "compressed": "gain resolves compression from the resource's manifest",
}
_REFUSED_PARAMETERS = {
    "cache": "it is scanpy's own h5ad cache, which gain does not maintain",
    "cache_compression":
        "it is scanpy's own h5ad cache, which gain does not maintain",
    "backup_url":
        "it would read bytes that are not in the manifest, not hashed, and "
        "not served by the repository",
}


@dataclasses.dataclass(frozen=True)
class TenXMtxParameters:
    """The knobs a ``10x_mtx`` resource may set, already validated."""

    var_names: str = _BY_SYMBOL
    make_unique: bool = True
    gex_only: bool = True


def _check_bool(name: str, value: Any, resource_id: str) -> bool:
    if not isinstance(value, bool):
        # A misconfigured resource, not a caller passing the wrong type --
        # and the loader reports every one of those as a ValueError.
        raise ValueError(  # noqa: TRY004
            f"the 10x_mtx parameter {name!r} of the ann_data "
            f"{resource_id} must be true or false, not {value!r}")
    return value


def parse_10x_mtx_parameters(
    parameters: Mapping[str, Any], resource_id: str,
) -> TenXMtxParameters:
    """Validate a resource's ``parameters:`` block into the knobs gain has.

    An unrecognised key raises rather than being forwarded, so a typo is
    reported instead of silently doing nothing -- and so is a key that used
    to reach scanpy and now has no meaning here.
    """
    for name, reason in _REFUSED_PARAMETERS.items():
        if name in parameters:
            raise ValueError(
                f"the 10x_mtx parameter {name!r} of the ann_data "
                f"{resource_id} is refused: {reason}")

    for name, reason in _DERIVED_PARAMETERS.items():
        if name in parameters:
            raise ValueError(
                f"the 10x_mtx parameter {name!r} of the ann_data "
                f"{resource_id} is not accepted: {reason}")

    known = {field.name for field in dataclasses.fields(TenXMtxParameters)}
    unknown = sorted(set(parameters) - known)
    if unknown:
        raise ValueError(
            f"unknown 10x_mtx parameter(s) {', '.join(unknown)} of the "
            f"ann_data {resource_id}; gain accepts {', '.join(sorted(known))}")

    var_names = parameters.get("var_names", _BY_SYMBOL)
    if var_names not in (_BY_SYMBOL, _BY_ID):
        raise ValueError(
            f"the 10x_mtx parameter 'var_names' of the ann_data "
            f"{resource_id} must be {_BY_SYMBOL!r} or {_BY_ID!r}, "
            f"not {var_names!r}")

    return TenXMtxParameters(
        var_names=var_names,
        make_unique=_check_bool(
            "make_unique", parameters.get("make_unique", True), resource_id),
        gex_only=_check_bool(
            "gex_only", parameters.get("gex_only", True), resource_id),
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
