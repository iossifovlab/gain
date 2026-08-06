# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
import pathlib
import re
from typing import Any

import pytest
from gain.genomic_resources import ann_data_resource
from gain.genomic_resources.ann_data_resource import (
    load_ann_data_from_resource,
)
from gain.genomic_resources.cached_repository import CachingProtocol
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
)
from gain.genomic_resources.testing.ann_data_builder import an_ann_data
from gain.genomic_resources.testing.builders import a_grr

# A distinctive constant, asserted absent from every rendering of a url --
# the assertion style of ``test_http_auth_credential_leak``.
_SECRET = "s3cr3t-do-not-log"  # noqa: S105


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    return an_ann_data().build_resource(tmp_path)


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
    # ``10x_h5`` is the one format still read through the optional extra.
    # Its absence is a misconfigured resource, not an internal import
    # failure, so it is reported as one -- naming the extra that fixes it.
    # The file is realized rather than merely declared, so that scanpy's
    # absence is the ONLY reason the read cannot proceed.
    resource = (
        an_ann_data().with_format("10x_h5").build_resource(tmp_path)
    )
    monkeypatch.setattr(ann_data_resource, "_import_scanpy", _raise_import)

    with pytest.raises(ValueError, match="ann_data_10x"):
        load_ann_data_from_resource(resource)


def _raise_import() -> Any:
    """Stand in for ``_import_scanpy`` with the extra absent."""
    raise ValueError(
        "the 10x_h5 ann_data format needs scanpy, which is not "
        "installed; install the gain-core[ann_data_10x] extra")


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

    with pytest.raises(ValueError, match="cannot load the url") as excinfo:
        load_ann_data_from_resource(resource)

    # A url carrying no userinfo is rendered verbatim -- the redaction of
    # a credential-bearing one (#608) is a redaction, not a rewording.
    assert str(excinfo.value) == (
        "cannot load the url https://example.org/d.h5ad "
        f"for the ann_data {resource.resource_id}")


def test_a_rejected_url_does_not_leak_its_credentials(
    tmp_path: pathlib.Path, mocker: Any, caplog: pytest.LogCaptureFixture,
) -> None:
    # ``get_file_url`` deliberately returns the credential-BEARING fetch
    # url -- aiohttp and htslib read basic auth straight off the url
    # string -- so every rendering of it here has to be redacted (#608).
    resource = an_ann_data().build_resource(tmp_path)
    mocker.patch.object(
        resource, "get_file_url",
        return_value=f"https://alice:{_SECRET}@grr.example.com/sub/d.h5ad")

    with caplog.at_level(logging.ERROR), \
            pytest.raises(ValueError, match="cannot load the url") as excinfo:
        load_ann_data_from_resource(resource)

    assert _SECRET not in caplog.text
    assert "alice" not in caplog.text
    assert _SECRET not in str(excinfo.value)
    assert "alice" not in str(excinfo.value)
    assert "https://grr.example.com/sub/d.h5ad" in str(excinfo.value)
    assert "https://grr.example.com/sub/d.h5ad" in caplog.text


def test_rejects_a_10x_mtx_format_on_a_name_that_is_not_a_matrix(
    tmp_path: pathlib.Path,
) -> None:
    # A declared format that the filename cannot support.  The triple is
    # addressed by what its members share, which is the matrix name minus
    # its suffix, so a name carrying no such suffix names no triple -- and
    # the resource is what has to be reported, not an index error.
    resource = (
        an_ann_data()
        .with_declared_format("10x_mtx")
        .build_resource(tmp_path)
    )

    with pytest.raises(ValueError, match="not a 10x matrix file name"):
        load_ann_data_from_resource(resource)


def test_an_h5ad_load_hands_the_handle_to_the_caller(
    resource: GenomicResource,
) -> None:
    # ``backed="r"`` keeps an h5py file open for as long as the AnnData
    # lives, and the loader deliberately does not close it -- a caller that
    # wants the AnnData wants the handle.  Closing in a loop is the
    # CALLER's job, and a repo sweep that does not is gain#480's shape; the
    # statistics build's own close is asserted in test_ann_data_impl.
    ann_data = load_ann_data_from_resource(resource)

    assert ann_data.file.is_open

    ann_data.file.close()
    assert not ann_data.file.is_open


def test_a_ten_x_load_has_no_handle_to_close(
    tmp_path: pathlib.Path,
) -> None:
    # A 10x read is built in memory, so a caller's ``isbacked`` guard is
    # what keeps the close from being an error rather than a no-op.
    resource = (
        an_ann_data().with_format("10x_mtx").build_resource(tmp_path)
    )

    assert not load_ann_data_from_resource(resource).isbacked


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
    tmp_path: pathlib.Path,
) -> None:
    # pandas and scipy read the triple's members straight off the
    # filesystem, bypassing the protocol entirely, so a caching GRR has to
    # have all three on disk BEFORE they are opened.  Only the matrix
    # member used to be refreshed; the two sidecars were fetched by
    # nothing, and a lazy load -- as against an explicit grr_manage
    # prefetch -- found them missing.
    resource, cache_dir = _caching_resource(
        tmp_path, an_ann_data().with_format("10x_mtx"))

    assert load_ann_data_from_resource(resource).shape == (3, 4)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "matrix.mtx.gz" in cached
    assert "barcodes.tsv.gz" in cached
    assert "features.tsv.gz" in cached


def test_legacy_ten_x_sidecars_are_fetched_into_the_cache(
    tmp_path: pathlib.Path,
) -> None:
    resource, cache_dir = _caching_resource(
        tmp_path, an_ann_data().with_format("10x_mtx").with_legacy_layout())

    assert load_ann_data_from_resource(resource).shape == (3, 4)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "matrix.mtx" in cached
    assert "barcodes.tsv" in cached
    assert "genes.tsv" in cached


def test_an_unfetchable_10x_sidecar_is_a_config_error(
    tmp_path: pathlib.Path,
) -> None:
    # Every other way of misconfiguring an ann_data is reported as a
    # ValueError naming the resource, and an incomplete 10x triple is a
    # misconfigured resource too.  It reaches gain as an OSError from the
    # fetch, which names the path and nothing else -- not the resource, and
    # not that the file is one member of a triple.
    resource, _cache_dir = _caching_resource(
        tmp_path, an_ann_data().with_format("10x_mtx"))
    # Removed AFTER the manifest recorded it, so the resolution still names
    # the sidecar and the fetch is what fails.
    (tmp_path / "remote" / "ann" / "data" / "features.tsv.gz").unlink()

    with pytest.raises(
        ValueError, match=re.escape("features.tsv.gz"),
    ) as excinfo:
        load_ann_data_from_resource(resource)

    assert resource.resource_id in str(excinfo.value)


def test_an_h5ad_is_fetched_into_the_cache(
    tmp_path: pathlib.Path,
) -> None:
    resource, cache_dir = _caching_resource(tmp_path, an_ann_data())

    assert load_ann_data_from_resource(resource).shape == (3, 4)

    cached = {path.name for path in cache_dir.rglob("*") if path.is_file()}
    assert "data.h5ad" in cached
