# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""gain's own reader for the 10x Matrix Market triple.

Every test here runs in the small tier, where scanpy is deliberately absent
-- it is the optional ``gain-core[ann_data_10x]`` extra.  That absence is
the point: these assert what gain reads, not that gain called somebody
else.  The live comparison against scanpy lives in
``tests/integration/test_ann_data_10x_layout.py``, which answers a
different question (did upstream move?) in the one tier that installs it.
"""
import gzip
import logging
import pathlib

import pytest
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
    resolve_10x_layout,
)
from gain.genomic_resources.testing.ann_data_builder import an_ann_data
from gain.genomic_resources.testing.builders import a_grr


def test_reads_a_current_layout_triple_without_scanpy(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    # 10x writes features as rows and barcodes as columns; an AnnData is
    # cells x genes, so the read transposes.
    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs_names) == ["CELL_1", "CELL_2", "CELL_3"]


def test_var_names_are_the_gene_symbols_by_default(
    tmp_path: pathlib.Path,
) -> None:
    # ``var_names="gene_symbols"`` is scanpy's default and stays gain's, so
    # the variables are indexed by symbol and the accession is kept beside
    # them under ``gene_ids``.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.var_names) == ["ACTB", "GAPDH", "MALAT1", "XIST"]
    assert list(ann_data.var["gene_ids"]) == [
        "ENSG001", "ENSG002", "ENSG003", "ENSG004"]


def test_var_names_may_be_the_gene_ids(tmp_path: pathlib.Path) -> None:
    # The mirror image of the default: indexed by accession, symbols kept
    # beside them -- under ``gene_symbols``, not ``gene_ids``.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_parameters({"var_names": "gene_ids"})
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.var_names) == [
        "ENSG001", "ENSG002", "ENSG003", "ENSG004"]
    assert list(ann_data.var["gene_symbols"]) == [
        "ACTB", "GAPDH", "MALAT1", "XIST"]


def test_rejects_an_unknown_var_names_choice(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_parameters({"var_names": "gene_descriptions"})
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="var_names") as excinfo:
        load_ann_data_from_resource(resource)

    assert resource.resource_id in str(excinfo.value)


# Two distinct accessions carrying the same symbol, which is the ordinary
# reason a 10x feature table has duplicate gene names.
_DUPLICATE_SYMBOLS = """
    gene     gene_name
    ENSG001  ACTB
    ENSG002  ACTB
    ENSG003  GAPDH
"""


def test_duplicate_gene_symbols_are_made_unique_by_default(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_var(_DUPLICATE_SYMBOLS)
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.var_names) == ["ACTB", "ACTB-1", "GAPDH"]


def test_duplicate_gene_symbols_may_be_left_alone(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_var(_DUPLICATE_SYMBOLS)
        .with_parameters({"make_unique": False})
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert list(ann_data.var_names) == ["ACTB", "ACTB", "GAPDH"]


# A CellRanger-ARC multiome shape: chromatin peaks alongside genes, in one
# feature table.  Two of the benchmark GRR's resources look like this.
# ``||`` is the authored block's escape for a space.
_MULTIOME_FEATURES = """
    gene     gene_name  feature_type
    ENSG001  ACTB       Gene||Expression
    ENSG002  GAPDH      Gene||Expression
    PEAK001  chr1:1-99  Peaks
    PEAK002  chr2:1-99  Peaks
"""


def test_gex_only_drops_the_other_feature_types_by_default(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_var(_MULTIOME_FEATURES)
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 2)
    assert list(ann_data.var_names) == ["ACTB", "GAPDH"]


def test_dropping_feature_types_is_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    # The default is scanpy's and stays, so existing resources are read
    # identically -- but discarding 76% of a resource without saying so is
    # what made the benchmark GRR's two multiome resources look like
    # gene-expression ones.  Whether that is right is a curation call, and
    # it cannot be made by someone who was never told.
    #
    # Through a repo rather than ``build_resource``, which gives a resource
    # the EMPTY id -- an ``id in caplog.text`` assertion would then hold
    # against any message at all.
    repo = (
        a_grr()
        .with_resource(
            "brain/multiome",
            an_ann_data().with_format("10x_mtx").with_var(_MULTIOME_FEATURES))
        .build_repo(tmp_path)
    )
    resource = repo.get_resource("brain/multiome")

    with caplog.at_level(logging.WARNING):
        load_ann_data_from_resource(resource)

    assert "brain/multiome" in caplog.text
    assert "gex_only" in caplog.text
    assert "Peaks (2)" in caplog.text
    assert "2 of its 4 features" in caplog.text


def test_a_single_feature_type_is_not_reported(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    # Nothing is dropped, so there is nothing to say.  A warning on every
    # ordinary resource is a warning nobody reads.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        load_ann_data_from_resource(resource)

    assert "gex_only" not in caplog.text


def test_gex_only_may_be_turned_off(tmp_path: pathlib.Path) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_var(_MULTIOME_FEATURES)
        .with_parameters({"gex_only": False})
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.var["feature_types"]) == [
        "Gene Expression", "Gene Expression", "Peaks", "Peaks"]


class TestTheParameterSurface:
    """``parameters:`` is a surface gain defines, not a passthrough.

    Every key is in exactly one of three sets, and which one it is in
    decides the message.  A key gain does not act on must never be accepted
    silently: the config would then state something the read ignores.
    """

    @staticmethod
    def _load(tmp_path: pathlib.Path, parameters: dict) -> None:
        resource = (
            an_ann_data()
            .with_format("10x_mtx")
            .with_parameters(parameters)
            .build_resource(tmp_path)
        )
        load_ann_data_from_resource(resource)

    def test_refuses_backup_url(self, tmp_path: pathlib.Path) -> None:
        # The one refusal that is a matter of principle rather than of
        # tidiness: it downloads the file from an arbitrary url when it is
        # not on disk.  A resource that can silently read off-repository
        # bytes -- unhashed, unmanifested, unserved -- is not a resource.
        with pytest.raises(ValueError, match="refused") as excinfo:
            self._load(tmp_path, {"backup_url": "https://example.org/m.mtx"})

        assert "backup_url" in str(excinfo.value)
        assert "manifest" in str(excinfo.value)

    @pytest.mark.parametrize("name", ["cache", "cache_compression"])
    def test_refuses_scanpys_own_cache(
        self, tmp_path: pathlib.Path, name: str,
    ) -> None:
        with pytest.raises(ValueError, match="refused") as excinfo:
            self._load(tmp_path, {name: True})

        assert name in str(excinfo.value)

    @pytest.mark.parametrize("name", ["prefix", "compressed"])
    def test_refuses_what_it_derives_itself(
        self, tmp_path: pathlib.Path, name: str,
    ) -> None:
        # Accepting these would let a config contradict the resource's own
        # layout, which is the thing the manifest already answers.
        with pytest.raises(ValueError, match="not accepted") as excinfo:
            self._load(tmp_path, {name: "sample1_"})

        assert name in str(excinfo.value)

    def test_refuses_an_unrecognised_key(
        self, tmp_path: pathlib.Path,
    ) -> None:
        with pytest.raises(ValueError, match="unknown") as excinfo:
            self._load(tmp_path, {"gex_onlyy": True})

        # The message lists what IS accepted, because the usual cause is a
        # typo and the fix is one character away.
        assert "gex_onlyy" in str(excinfo.value)
        assert "gex_only" in str(excinfo.value)
        assert "make_unique" in str(excinfo.value)
        assert "var_names" in str(excinfo.value)

    def test_refuses_a_non_boolean_flag(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # yaml turns a bare ``yes`` into True but quotes into a string, so
        # this is a real way to misconfigure a resource.
        with pytest.raises(ValueError, match="must be true or false"):
            self._load(tmp_path, {"gex_only": "yes"})


def test_reads_an_uncompressed_current_layout_triple(
    tmp_path: pathlib.Path,
) -> None:
    # STARsolo writes the v3 triple -- three-column feature table and all
    # -- without gzipping it.  That is a v3 layout, not a legacy one, so it
    # still carries feature types; only the suffixes differ.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_uncompressed_layout()
        .with_var(_MULTIOME_FEATURES)
        .with_parameters({"gex_only": False})
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.var["feature_types"]) == [
        "Gene Expression", "Gene Expression", "Peaks", "Peaks"]


def test_an_uncompressed_triples_sidecars_are_resolved(
    tmp_path: pathlib.Path,
) -> None:
    # The statistics inputs and the cache prefetch both come from here, so
    # a layout the resolution cannot name is a layout whose sidecars are
    # hashed by nothing and fetched by nothing.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_uncompressed_layout()
        .build_resource(tmp_path)
    )

    assert resolve_10x_layout(
        resource.get_manifest(), "matrix.mtx").sidecars == {
            "barcodes.tsv", "features.tsv"}


class TestTheMatrixFreeRead:
    """The read the statistics build takes, which never materialises X.

    Nothing a statistic reports comes from the data matrix:
    ``AnnData._gen_repr`` skips ``X`` outright and ``describe`` reads the
    two axis tables.  On the benchmark GRR's largest 10x resource --
    884,736 cells by 38,705 genes, 320,534,319 non-zeros -- materialising
    it costs about 10 GB to produce 221 bytes, and the dask worker is
    killed before it can.

    This is not a second implementation of the read.  The sidecars, the
    axis tables and the feature-type filter are one construction shared by
    both; only the ``X`` strategy differs.
    """

    def test_it_describes_the_resource_identically(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # The multiome fixture on purpose: gex_only subsets both axes, so
        # this covers the filter as well as the plain read.
        resource = (
            an_ann_data()
            .with_format("10x_mtx")
            .with_var(_MULTIOME_FEATURES)
            .build_resource(tmp_path)
        )
        full = load_ann_data_from_resource(resource)

        lean = load_ann_data_from_resource(resource, matrix_free=True)

        assert str(lean) == str(full)
        assert lean.obs.equals(full.obs)
        assert lean.var.equals(full.var)

    def test_it_carries_the_shape_but_no_entries(
        self, tmp_path: pathlib.Path,
    ) -> None:
        resource = (
            an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
        )

        assert load_ann_data_from_resource(resource).X.nnz == 12

        lean = load_ann_data_from_resource(resource, matrix_free=True)

        # A real sparse matrix of the right shape, not None: the repr's
        # ``layers: None (.X)`` line disappears with X=None, and the
        # statistic is then not byte-identical to the full read's.
        assert lean.shape == (3, 4)
        assert lean.X.nnz == 0

    def test_it_never_reads_the_matrix_body(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # A matrix whose header is honest and whose body is absent.  The
        # full read fails on it outright, which is what makes this a
        # statement about the body being unread rather than about it being
        # read cheaply.
        resource = (
            an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
        )
        matrix = next(tmp_path.rglob("matrix.mtx.gz"))
        header = gzip.decompress(matrix.read_bytes()).split(b"\n")[:3]
        matrix.write_bytes(gzip.compress(b"\n".join([*header, b""])))

        with pytest.raises(ValueError, match="Truncated file"):
            load_ann_data_from_resource(resource)

        lean = load_ann_data_from_resource(resource, matrix_free=True)

        assert lean.shape == (3, 4)
        assert list(lean.var_names) == ["ACTB", "GAPDH", "MALAT1", "XIST"]

    def test_an_h5ad_ignores_it_and_stays_backed(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # h5ad needs no matrix-free variant -- ``backed="r"`` already keeps
        # X off the heap -- so the flag is a no-op there and X remains the
        # resource's real data, lazily read.
        resource = an_ann_data().build_resource(tmp_path)

        ann_data = load_ann_data_from_resource(resource, matrix_free=True)

        assert ann_data.isbacked
        assert ann_data.shape == (3, 4)
        ann_data.file.close()


def test_reports_a_feature_table_that_is_too_narrow(
    tmp_path: pathlib.Path,
) -> None:
    # A v3 layout promises three columns.  Two means the file is a v2
    # genes.tsv under a v3 name, and gain now owns the message -- reading
    # a column that is not there otherwise surfaces as a bare KeyError
    # from pandas, which names neither the file nor the resource.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_uncompressed_layout()
        .build_resource(tmp_path)
    )
    features = tmp_path / "features.tsv"
    features.write_text("".join(
        line.rsplit("\t", 1)[0] + "\n"
        for line in features.read_text().splitlines()))

    with pytest.raises(ValueError, match="three columns") as excinfo:
        load_ann_data_from_resource(resource)

    assert "features.tsv" in str(excinfo.value)


def test_reads_a_legacy_layout_triple(tmp_path: pathlib.Path) -> None:
    # CellRanger v2 ships the triple as plain text and its feature table
    # carries no third column, so a legacy read has no ``feature_types``.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_legacy_layout()
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs_names) == ["CELL_1", "CELL_2", "CELL_3"]
    assert "gene_ids" in ann_data.var.columns
    assert "feature_types" not in ann_data.var.columns


def test_reads_a_triple_addressed_by_a_shared_prefix(
    tmp_path: pathlib.Path,
) -> None:
    # A resource carrying more than one matrix distinguishes them by the
    # prefix its three members share, so the sidecars have to be found
    # under that prefix and not beside it.
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_prefix("sample1_")
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs_names) == ["CELL_1", "CELL_2", "CELL_3"]
