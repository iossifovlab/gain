"""Fluent, immutable test-data builder for ``ann_data`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders` for the same
reason :mod:`.data_frame_builder` is one: ``builders`` sits two lines under
pylint's ``max-module-lines`` ceiling, so a new builder has nowhere to go
inside it.  The dependency runs ONE WAY -- this module imports the shared
single-realize seam from ``builders`` and ``builders`` does not import back
-- so ``an_ann_data`` is imported from here.

The axis this builder exists to vary is the FORMAT, exactly as
``DataFrameBuilder`` varies csv/tsv/xlsx: one authored pair of annotation
tables realized either as an ``h5ad`` or as a 10x Matrix Market triple, in
whichever of the two 10x layouts a test needs.  Hand-rolling either fixture
per test is what makes ``ann_data`` tests tedious otherwise, and the 10x
triple in particular has to be realized *consistently* across three files
for the sidecar resolution to be worth testing at all.

Like ``DataFrameBuilder``, this exposes NO expected AnnData.  Realizing an
h5ad forces this builder to go through ``anndata`` itself, and handing that
object back as a test's oracle would check anndata against anndata on
exactly the axes an ``ann_data`` test varies -- builder and loader would
have to be wrong in the same way for the test to stay green.  Tests state
their expectations independently.
"""
from __future__ import annotations

import dataclasses
import gzip
import io
import pathlib
from typing import Any

import numpy as np
import pandas as pd
import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_ANN_DATA_FORMATS = ("h5ad", "10x_mtx")

# The realized matrix filename per 10x layout.  CellRanger v3 gzips the
# whole triple; v2 ships it as plain text.  ``file:`` points at the matrix
# member, which is what the ``ann_data`` config names.
_H5AD_FILENAME = "data.h5ad"
_MTX_V3_FILENAME = "matrix.mtx.gz"
_MTX_V2_FILENAME = "matrix.mtx"

# The first column of an authored block is the index -- cell barcodes for
# obs, gene ids for var -- and the rest are annotation columns.
_DEFAULT_OBS_DATA = """
    cell     cell_type  n_genes
    CELL_1   neuron     120
    CELL_2   glia       340
    CELL_3   neuron     250
"""

_DEFAULT_VAR_DATA = """
    gene     gene_name  highly_variable
    ENSG001  ACTB       True
    ENSG002  GAPDH      False
    ENSG003  MALAT1     True
    ENSG004  XIST       False
"""


@dataclasses.dataclass(frozen=True)
class AnnDataBuilder(MetaMixin):
    """Immutable builder for a single ``ann_data`` resource.

    A bare builder realizes a valid minimal readable ``h5ad`` resource.
    """

    obs_data: str | None = None
    var_data: str | None = None
    drop_obs_columns: bool = False
    drop_var_columns: bool = False
    file_format: str = "h5ad"
    legacy_layout: bool = False
    uncompressed_layout: bool = False
    prefix: str = ""
    declared_format: str | None = None
    omit_format_key: bool = False
    filename: str | None = None
    parameters: dict[str, Any] | None = None
    omit_file_key: bool = False

    def with_obs(self, data: str) -> AnnDataBuilder:
        """Author the ``obs`` (per-cell) table as a whitespace block.

        The first column is the index; the block is normalized by
        ``convert_to_tab_separated``.
        """
        return dataclasses.replace(self, obs_data=data)

    def with_var(self, data: str) -> AnnDataBuilder:
        """Author the ``var`` (per-gene) table as a whitespace block.

        Two column names are conventional, because a 10x feature table is
        positional and this block is what has to fill it: ``gene_name`` is
        the gene symbol and ``feature_type`` is the feature type.  Both are
        optional -- see :func:`_feature_table` for what stands in.  An
        ``h5ad`` realization carries them as ordinary ``var`` columns.
        """
        return dataclasses.replace(self, var_data=data)

    def without_obs_columns(self) -> AnnDataBuilder:
        """Keep the index but drop every ``obs`` annotation column.

        The shape that makes ``describe`` of nothing an empty frame, which
        the implementation declines to write as a statistic.
        """
        return dataclasses.replace(self, drop_obs_columns=True)

    def without_var_columns(self) -> AnnDataBuilder:
        """Keep the index but drop every ``var`` annotation column."""
        return dataclasses.replace(self, drop_var_columns=True)

    def with_format(self, file_format: str) -> AnnDataBuilder:
        """Select the realized format AND the declared ``format:`` key.

        One of ``h5ad`` or ``10x_mtx``; the realized filename follows.  To
        declare a format that does NOT match what is on disk, use
        :meth:`with_declared_format`.
        """
        if file_format not in _ANN_DATA_FORMATS:
            raise ResourceValidationError(
                f"unknown ann_data format {file_format!r}; the builder can "
                f"realize {list(_ANN_DATA_FORMATS)}. To DECLARE an "
                f"unrealizable format, use with_declared_format")
        return dataclasses.replace(self, file_format=file_format)

    def with_uncompressed_layout(self) -> AnnDataBuilder:
        """Realize the v3 triple as plain text -- the STARsolo layout.

        ``matrix.mtx``/``barcodes.tsv``/``features.tsv``, with the same
        three-column feature table v3 always has.  This is NOT the legacy
        layout: it still carries feature types, so ``gex_only`` still has
        something to filter on.
        """
        return dataclasses.replace(self, uncompressed_layout=True)

    def with_legacy_layout(self) -> AnnDataBuilder:
        """Realize the 10x triple in the CellRanger v2 layout.

        ``matrix.mtx``/``barcodes.tsv``/``genes.tsv``, all plain text --
        as against v3's gzipped ``matrix.mtx.gz``/``barcodes.tsv.gz``/
        ``features.tsv.gz``.  The distinction is not cosmetic: it is the
        one scanpy itself probes for, and the sidecar resolution has to
        make the same call.
        """
        return dataclasses.replace(self, legacy_layout=True)

    def with_prefix(self, prefix: str) -> AnnDataBuilder:
        """Give the 10x triple a shared filename prefix.

        ``scanpy.read_10x_mtx`` addresses the triple as a directory plus
        the prefix its three members share, so a resource carrying more
        than one matrix distinguishes them this way.
        """
        return dataclasses.replace(self, prefix=prefix)

    def with_declared_format(self, file_format: str) -> AnnDataBuilder:
        """Override the config's ``format:`` only, leaving realization.

        Unvalidated on purpose: this is how a test builds a resource
        declaring an unknown format, or one whose declared format
        disagrees with the bytes on disk.
        """
        return dataclasses.replace(self, declared_format=file_format)

    def with_file(self, filename: str) -> AnnDataBuilder:
        """Override the realized filename (default: per format)."""
        return dataclasses.replace(self, filename=filename)

    def with_parameters(self, parameters: dict[str, Any]) -> AnnDataBuilder:
        """Emit a ``parameters:`` block passed through to the reader."""
        return dataclasses.replace(self, parameters=dict(parameters))

    def without_file_key(self) -> AnnDataBuilder:
        """Omit ``file:`` from the config, keeping the data file."""
        return dataclasses.replace(self, omit_file_key=True)

    def without_format_key(self) -> AnnDataBuilder:
        """Omit ``format:``, exercising the loader's suffix default."""
        return dataclasses.replace(self, omit_format_key=True)

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this ann_data resource into ``resource_dir``."""
        setup_directories(resource_dir, _build_ann_data_content(self))

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def _parse_block(data: str) -> pd.DataFrame:
    """Parse an authored whitespace block, first column as the index."""
    lines = [
        line for line in convert_to_tab_separated(data).split("\n") if line
    ]
    frame = pd.read_csv(
        io.StringIO("".join(f"{line}\n" for line in lines)), sep="\t")
    return frame.set_index(frame.columns[0])


def _annotation_frames(
    builder: AnnDataBuilder,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the authored ``(obs, var)`` frames, columns dropped on ask."""
    obs = _parse_block(
        builder.obs_data if builder.obs_data is not None
        else _DEFAULT_OBS_DATA)
    var = _parse_block(
        builder.var_data if builder.var_data is not None
        else _DEFAULT_VAR_DATA)
    if builder.drop_obs_columns:
        obs = obs[[]]
    if builder.drop_var_columns:
        var = var[[]]
    return obs, var


def _render_h5ad(builder: AnnDataBuilder) -> bytes:
    """Realize the authored annotations as an ``h5ad``.

    Through a temporary file rather than an in-memory buffer: anndata
    writes HDF5, and h5py wants a real path.
    """
    # pylint: disable=import-outside-toplevel
    import tempfile

    import anndata as ad

    obs, var = _annotation_frames(builder)
    # A deterministic X, so two builds of the same builder are byte-stable
    # and a statistics-hash equality test means what it says.
    matrix = np.arange(
        len(obs) * len(var), dtype=np.float32).reshape(len(obs), len(var))
    ann_data = ad.AnnData(X=matrix, obs=obs, var=var)

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = pathlib.Path(tmp_dir) / "realized.h5ad"
        ann_data.write_h5ad(path)
        return path.read_bytes()


def _render_matrix_market(n_genes: int, n_cells: int) -> str:
    """Render a genes x cells Matrix Market coordinate file.

    10x writes features as ROWS and barcodes as COLUMNS -- the transpose of
    the AnnData a reader hands back -- and Matrix Market is 1-indexed.
    Every entry is non-zero so the declared nnz and the body agree; a
    reader that trusts the header would otherwise read past the end.
    """
    entries = [
        (gene, cell, gene * n_cells + cell)
        for gene in range(1, n_genes + 1)
        for cell in range(1, n_cells + 1)
    ]
    lines = [
        "%%MatrixMarket matrix coordinate integer general",
        "%",
        f"{n_genes} {n_cells} {len(entries)}",
    ]
    lines.extend(f"{gene} {cell} {value}" for gene, cell, value in entries)
    return "".join(f"{line}\n" for line in lines)


def _feature_table(var: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Return the authored ``var`` as 10x ``(id, symbol, type)`` rows.

    A 10x feature table is positional, so the authored block names the
    three fields by convention rather than by position: the INDEX is the
    gene id, a ``gene_name`` column is the symbol, and a ``feature_type``
    column is the feature type.  Either column may be absent -- the symbol
    then repeats the id, as it does in real data with no symbol, and the
    type is ``Gene Expression``, which is what makes the bare builder a
    resource ``gex_only`` does not filter.
    """
    def column(name: str, default: pd.Index) -> list[str]:
        values = var[name] if name in var.columns else default
        return [str(value) for value in values]

    ids = [str(gene_id) for gene_id in var.index]
    symbols = column("gene_name", var.index)
    types = column(
        "feature_type", pd.Index(["Gene Expression"] * len(var)))
    return list(zip(ids, symbols, types, strict=True))


def _render_10x_triple(builder: AnnDataBuilder) -> dict[str, Any]:
    """Realize the authored annotations as a 10x Matrix Market triple.

    Both layouts are realized in full, because the resolution under test
    decides between them by probing for ``genes.tsv`` exactly as scanpy
    does -- a triple that is only half-written would let a wrong answer
    look right.  v3 gzips all three members and ships a three-column
    ``features.tsv.gz``; v2 ships plain text and a two-column
    ``genes.tsv``, which carries no feature type at all.
    """
    obs, var = _annotation_frames(builder)
    prefix = builder.prefix

    barcodes = "".join(f"{barcode}\n" for barcode in obs.index)
    matrix = _render_matrix_market(len(var), len(obs))
    features_table = _feature_table(var)

    if builder.legacy_layout:
        genes = "".join(
            f"{gene_id}\t{symbol}\n" for gene_id, symbol, _ in features_table)
        return {
            f"{prefix}{_MTX_V2_FILENAME}": matrix,
            f"{prefix}barcodes.tsv": barcodes,
            f"{prefix}genes.tsv": genes,
        }

    features = "".join(
        f"{gene_id}\t{symbol}\t{feature_type}\n"
        for gene_id, symbol, feature_type in features_table)

    if builder.uncompressed_layout:
        # STARsolo's spelling of v3: the same three-column feature table,
        # no gzip anywhere.
        return {
            f"{prefix}{_MTX_V2_FILENAME}": matrix,
            f"{prefix}barcodes.tsv": barcodes,
            f"{prefix}features.tsv": features,
        }

    return {
        f"{prefix}{_MTX_V3_FILENAME}": gzip.compress(matrix.encode()),
        f"{prefix}barcodes.tsv.gz": gzip.compress(barcodes.encode()),
        f"{prefix}features.tsv.gz": gzip.compress(features.encode()),
    }


def _realized_filename(builder: AnnDataBuilder) -> str:
    """Return the name ``file:`` points at for this builder."""
    if builder.filename is not None:
        return builder.filename
    if builder.file_format == "h5ad":
        return _H5AD_FILENAME
    plain = builder.legacy_layout or builder.uncompressed_layout
    matrix = _MTX_V2_FILENAME if plain else _MTX_V3_FILENAME
    return f"{builder.prefix}{matrix}"


def _build_ann_data_content(builder: AnnDataBuilder) -> dict[str, Any]:
    """Build the pure filesystem content dict for one ann_data resource.

    Validation raises a ``ResourceValidationError``; the caller
    (``GRRBuilder``) annotates it with the resource id, so messages here
    stay id-free.
    """
    if builder.legacy_layout and builder.uncompressed_layout:
        raise ResourceValidationError(
            "with_legacy_layout and with_uncompressed_layout are two "
            "different layouts; a triple is realized in one of them")

    if builder.file_format == "h5ad" and (
            builder.legacy_layout or builder.uncompressed_layout
            or builder.prefix):
        raise ResourceValidationError(
            "with_legacy_layout, with_uncompressed_layout and with_prefix "
            "describe the 10x triple; an h5ad is a single file with none "
            "of them")

    if builder.file_format == "h5ad":
        content: dict[str, Any] = {_realized_filename(builder): _render_h5ad(
            builder)}
    else:
        content = _render_10x_triple(builder)
        if builder.filename is not None:
            raise ResourceValidationError(
                "with_file renames a single realized file, but a 10x "
                "resource is a triple addressed by prefix; use with_prefix")

    config = "type: ann_data\n"
    if not builder.omit_file_key:
        config += f"file: {_realized_filename(builder)}\n"
    if not builder.omit_format_key:
        declared = builder.declared_format or builder.file_format
        config += f"format: {declared}\n"
    if builder.parameters:
        config += yaml.safe_dump(
            {"parameters": builder.parameters},
            default_flow_style=False, sort_keys=False)
    config += builder.render_meta()

    return {GR_CONF_FILE_NAME: config, **content}


def an_ann_data() -> AnnDataBuilder:
    """Return an immutable ann_data builder."""
    return AnnDataBuilder()
