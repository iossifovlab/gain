# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from typing import Any

import anndata as ad
import numpy as np
import pytest
from gain.genomic_resources import ann_data_resource
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
    mtx_file_to_dir_and_prefix,
)
from gain.genomic_resources.cached_repository import CachingProtocol
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
)
from gain.genomic_resources.testing.ann_data_builder import an_ann_data
from gain.genomic_resources.testing.builders import a_grr


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    return an_ann_data().build_resource(tmp_path)


class _FakeScanpy:
    """Stands in for the optional scanpy extra, recording its call.

    scanpy is deliberately absent from the test environment -- it is the
    ``gain-core[ann_data_10x]`` extra -- so the two 10x readers are reached
    through this seam.  Everything under test here happens strictly ABOVE
    scanpy: which directory and prefix it is handed, and which resource
    files were fetched before it was called.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def read_10x_mtx(
        self, dirname: str, prefix: str | None = None, **params: Any,
    ) -> ad.AnnData:
        self.calls.append((dirname, prefix, params))
        return ad.AnnData(X=np.zeros((2, 2), dtype=np.float32))

    def read_10x_h5(self, path: str, **params: Any) -> ad.AnnData:
        self.calls.append((path, None, params))
        return ad.AnnData(X=np.zeros((2, 2), dtype=np.float32))


@pytest.fixture
def fake_scanpy(monkeypatch: pytest.MonkeyPatch) -> _FakeScanpy:
    scanpy = _FakeScanpy()
    monkeypatch.setattr(
        ann_data_resource, "_import_scanpy", lambda: scanpy)
    return scanpy


def test_loads_an_h5ad_resource(resource: GenomicResource) -> None:
    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.shape == (3, 4)
    assert list(ann_data.obs.columns) == ["cell_type", "n_genes"]
    assert list(ann_data.var.columns) == ["gene_name", "highly_variable"]


def test_rejects_a_missing_resource() -> None:
    with pytest.raises(ValueError, match="missing resource"):
        load_ann_data_from_resource(None)


def test_rejects_a_resource_of_another_type(
    tmp_path: pathlib.Path,
) -> None:
    # pylint: disable=import-outside-toplevel
    from gain.genomic_resources.testing.data_frame_builder import a_data_frame

    resource = a_data_frame().build_resource(tmp_path)

    with pytest.raises(ValueError, match="wrong resource type"):
        load_ann_data_from_resource(resource)


def test_rejects_a_config_without_a_file_key(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_ann_data().without_file_key().build_resource(tmp_path)

    with pytest.raises(ValueError, match="missing file parameter"):
        load_ann_data_from_resource(resource)


def test_rejects_an_unknown_format(tmp_path: pathlib.Path) -> None:
    resource = (
        an_ann_data()
        .with_declared_format("parquet")
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="Unknown format parquet"):
        load_ann_data_from_resource(resource)


def test_format_defaults_to_the_suffix(tmp_path: pathlib.Path) -> None:
    # A config that spells out no ``format:`` is read as whatever its
    # filename suffix implies -- h5ad here.
    resource = (
        an_ann_data().without_format_key().build_resource(tmp_path)
    )

    assert load_ann_data_from_resource(resource).shape == (3, 4)


def test_reports_an_absent_scanpy_as_a_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    # The 10x readers need the optional extra.  Its absence is a
    # misconfigured resource, not an internal import failure, so it is
    # reported as one -- naming the extra that fixes it.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )
    monkeypatch.setattr(ann_data_resource, "_import_scanpy", _raise_import)

    with pytest.raises(ValueError, match="ann_data_10x"):
        load_ann_data_from_resource(resource)


def _raise_import() -> Any:
    raise ValueError(
        "the 10x ann_data formats need scanpy, which is not installed; "
        "install the gain-core[ann_data_10x] extra")


def test_ten_x_is_read_as_a_directory_and_prefix(
    fake_scanpy: _FakeScanpy, tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_prefix("sample1_")
        .build_resource(tmp_path)
    )

    load_ann_data_from_resource(resource)

    (dirname, prefix, _params) = fake_scanpy.calls[0]
    assert prefix == "sample1_"
    assert (pathlib.Path(dirname) / "sample1_barcodes.tsv.gz").exists()


def test_parameters_reach_the_reader(
    fake_scanpy: _FakeScanpy, tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_ann_data()
        .with_format("10x_mtx")
        .with_parameters({"var_names": "gene_ids"})
        .build_resource(tmp_path)
    )

    load_ann_data_from_resource(resource)

    assert fake_scanpy.calls[0][2] == {"var_names": "gene_ids"}


def test_a_config_may_override_backed(tmp_path: pathlib.Path) -> None:
    # ``parameters:`` is a passthrough, and ``backed`` is one of the keys
    # the loader itself supplies -- a config naming it used to collide with
    # the loader's own argument and die with a duplicate-kwarg TypeError
    # rather than a message about the resource.  A config that states its
    # own value keeps winning, exactly as data_frame's separator does.
    resource = (
        an_ann_data()
        .with_parameters({"backed": None})
        .build_resource(tmp_path)
    )

    ann_data = load_ann_data_from_resource(resource)

    assert not ann_data.isbacked


def test_rejects_a_non_file_url(
    tmp_path: pathlib.Path, mocker: Any,
) -> None:
    resource = an_ann_data().build_resource(tmp_path)
    mocker.patch.object(
        resource, "get_file_url", return_value="https://example.org/d.h5ad")

    with pytest.raises(ValueError, match="cannot load the url"):
        load_ann_data_from_resource(resource)


class TestMtxFileToDirAndPrefix:
    """The 10x matrix name is split into what scanpy actually takes."""

    def test_splits_the_directory_and_the_shared_prefix(self) -> None:
        assert mtx_file_to_dir_and_prefix("d/sample1_matrix.mtx.gz") == (
            "d", "sample1_")

    def test_an_empty_prefix_is_scanpys_none(self) -> None:
        # scanpy spells "no prefix" as None rather than "".
        assert mtx_file_to_dir_and_prefix("d/matrix.mtx.gz") == ("d", None)

    def test_the_plain_spelling_is_split_too(self) -> None:
        assert mtx_file_to_dir_and_prefix("d/matrix.mtx") == ("d", None)

    def test_a_bare_name_is_the_working_directory(self) -> None:
        # os.path.dirname of a bare name is "", which scanpy does not read
        # as the working directory the way "." does.
        assert mtx_file_to_dir_and_prefix("matrix.mtx.gz") == (".", None)

    def test_rejects_a_name_that_is_not_a_matrix(self) -> None:
        with pytest.raises(ValueError, match="not a 10x matrix file name"):
            mtx_file_to_dir_and_prefix("d/barcodes.tsv.gz")


class TestOpenAnnDataFromResource:
    """The backed handle has an owner."""

    def test_yields_a_usable_ann_data(
        self, resource: GenomicResource,
    ) -> None:
        with ann_data_resource.open_ann_data_from_resource(
            resource,
        ) as ann_data:
            assert ann_data.shape == (3, 4)

    def test_closes_the_handle_on_exit(
        self, resource: GenomicResource,
    ) -> None:
        # ``backed="r"`` keeps an h5py file open for as long as the AnnData
        # lives.  A repo sweep that opens one per resource and relies on a
        # garbage collection that may never come is gain#480's shape.
        with ann_data_resource.open_ann_data_from_resource(
            resource,
        ) as ann_data:
            assert ann_data.file.is_open

        assert not ann_data.file.is_open

    def test_closes_the_handle_on_an_exception(
        self, resource: GenomicResource,
    ) -> None:
        held = None
        with pytest.raises(RuntimeError), \
                ann_data_resource.open_ann_data_from_resource(  # noqa: PT012
                    resource) as ann_data:
            held = ann_data
            raise RuntimeError("boom")

        assert held is not None
        assert not held.file.is_open

    def test_tolerates_an_unbacked_read(
        self, tmp_path: pathlib.Path,
    ) -> None:
        # A config may turn backed off; there is then no handle to close.
        resource = (
            an_ann_data()
            .with_parameters({"backed": None})
            .build_resource(tmp_path)
        )

        with ann_data_resource.open_ann_data_from_resource(
            resource,
        ) as ann_data:
            assert not ann_data.isbacked


def _caching_resource(
    tmp_path: pathlib.Path, builder: Any,
) -> tuple[GenomicResource, pathlib.Path]:
    """Return a resource reached through a caching protocol, and the cache."""
    remote_dir = tmp_path / "remote"
    cache_dir = tmp_path / "cache"
    a_grr().with_resource("ann/data", builder).build_repo(remote_dir)

    caching_proto = CachingProtocol(
        build_filesystem_test_protocol(remote_dir),
        build_filesystem_test_protocol(cache_dir),
    )
    return caching_proto.get_resource("ann/data"), cache_dir


def test_ten_x_sidecars_are_fetched_into_the_cache(
    fake_scanpy: _FakeScanpy, tmp_path: pathlib.Path,
) -> None:
    # scanpy reads the barcodes and features straight off the filesystem,
    # bypassing the protocol entirely, so a caching GRR has to have them on
    # disk BEFORE it is called.  Only the matrix member used to be
    # refreshed; the two sidecars were fetched by nothing, and a lazy load
    # -- as against an explicit grr_manage prefetch -- found them missing.
    resource, cache_dir = _caching_resource(
        tmp_path, an_ann_data().with_format("10x_mtx"))

    load_ann_data_from_resource(resource)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "matrix.mtx.gz" in cached
    assert "barcodes.tsv.gz" in cached
    assert "features.tsv.gz" in cached


def test_legacy_ten_x_sidecars_are_fetched_into_the_cache(
    fake_scanpy: _FakeScanpy, tmp_path: pathlib.Path,
) -> None:
    resource, cache_dir = _caching_resource(
        tmp_path, an_ann_data().with_format("10x_mtx").with_legacy_layout())

    load_ann_data_from_resource(resource)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "matrix.mtx" in cached
    assert "barcodes.tsv" in cached
    assert "genes.tsv" in cached


def test_an_h5ad_is_fetched_into_the_cache(
    tmp_path: pathlib.Path,
) -> None:
    resource, cache_dir = _caching_resource(tmp_path, an_ann_data())

    assert load_ann_data_from_resource(resource).shape == (3, 4)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "data.h5ad" in cached
