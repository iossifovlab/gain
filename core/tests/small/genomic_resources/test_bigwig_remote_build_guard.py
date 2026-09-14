# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A bigwig on an uncached remote GRR needs a curl-enabled pyBigWig.

``FsspecReadOnlyProtocol.open_bigwig_file`` hands pyBigWig the url itself
for the remote schemes, which only a libBigWig compiled with libcurl can
open (``pyBigWig.remote == 1``). The PyPI wheel is not, and takes every url
for a local path. These tests pin what a caller is told on that build, and
that the other paths -- a local file, a curl-enabled build -- are left
alone.
"""
import pathlib
from collections.abc import Generator

import pyBigWig
import pytest
import pytest_mock
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_http_test_protocol,
    build_s3_test_protocol,
)
from gain.genomic_resources.testing.builders import a_bigwig_score, a_grr

_BIGWIG_FILE_NAME = "data.bw"


@pytest.fixture
def no_curl_pybigwig(mocker: pytest_mock.MockerFixture) -> None:
    """The PyPI wheel's build, whichever build is actually installed."""
    mocker.patch.object(pyBigWig, "remote", 0)


def _an_http_protocol(
    proto_id: str, base_url: str = "https://127.0.0.1:1/path",
) -> tuple[FsspecReadOnlyProtocol, GenomicResource]:
    proto = build_fsspec_protocol(proto_id, base_url)
    return proto, GenomicResource("sub/res", (1, 0), proto, {})


@pytest.mark.usefixtures("no_curl_pybigwig")
def test_remote_bigwig_open_on_a_no_curl_build_is_refused_before_pybigwig(
    mocker: pytest_mock.MockerFixture,
) -> None:
    proto, resource = _an_http_protocol("i1425-refused")
    opened = mocker.patch.object(pyBigWig, "open")

    with pytest.raises(OSError, match="remote") as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    opened.assert_not_called()
    message = str(excinfo.value)
    assert "cache_dir" in message
    assert "pyBigWig" in message


_SECRET = "s3cr3tDoNotLog"  # ruff: ignore[hardcoded-password-string]
_SIGNATURE = "Ns1gNaTuReDoNoTlOg%3D"


@pytest.mark.usefixtures("no_curl_pybigwig")
def test_refusal_names_the_resource_but_not_the_credentialed_url() -> None:
    # The refusal is raised before the fd 2 bracket and the redactor that
    # guard a real open, so it has to be clean by construction: an authed
    # http GRR carries its credential in the url's userinfo.
    proto, resource = _an_http_protocol(
        "i1425-userinfo", f"https://alice:{_SECRET}@127.0.0.1:1/path")

    with pytest.raises(OSError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    message = str(excinfo.value)
    assert "sub/res" in message
    assert _BIGWIG_FILE_NAME in message
    assert _SECRET not in message
    assert "127.0.0.1" not in message


@pytest.mark.usefixtures("no_curl_pybigwig")
def test_refusal_does_not_carry_a_presigned_signature(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # An s3 GRR's credential sits in the query string of the presigned url
    # ``_get_file_url`` produces, not in userinfo.
    proto, resource = _an_http_protocol("i1425-presigned")
    mocker.patch.object(
        proto, "_get_file_url",
        return_value=(
            f"https://127.0.0.1:1/path/sub/res(1.0)/{_BIGWIG_FILE_NAME}"
            f"?AWSAccessKeyId=AKIAEXAMPLE&Signature={_SIGNATURE}"
            "&Expires=1789000000"))

    with pytest.raises(OSError) as excinfo:
        proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    assert _SIGNATURE not in str(excinfo.value)


@pytest.mark.usefixtures("no_curl_pybigwig")
def test_local_bigwig_still_opens_on_a_no_curl_build(
    tmp_path: pathlib.Path,
) -> None:
    # The wheel's ``fopen`` path is exactly what a ``file`` GRR needs, so the
    # refusal must be scoped to the schemes it cannot serve.
    resource = a_bigwig_score().build_resource(tmp_path)

    bw_file = resource.open_bigwig_file(_BIGWIG_FILE_NAME)

    assert bw_file.intervals("chr1", 0, 10) == ((0, 10, pytest.approx(0.1)),)


def test_remote_bigwig_open_on_a_curl_build_reaches_pybigwig(
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Read per call, not at import: the build is a property of the process,
    # and this is what lets the credential-leak suite drive the fd 2 bracket
    # on the PyPI wheel by declaring the build curl-enabled.
    mocker.patch.object(pyBigWig, "remote", 1)
    proto, resource = _an_http_protocol("i1425-curl")
    opened = mocker.patch.object(pyBigWig, "open")

    proto.open_bigwig_file(resource, _BIGWIG_FILE_NAME)

    opened.assert_called_once_with(
        f"https://127.0.0.1:1/path/sub/res(1.0)/{_BIGWIG_FILE_NAME}")


@pytest.fixture(scope="module")
def bigwig_proto(
    tmp_path_factory: pytest.TempPathFactory, grr_scheme: str,
) -> Generator[FsspecReadOnlyProtocol, None, None]:
    """One bigwig resource, served over the parametrized scheme."""
    root_path = tmp_path_factory.mktemp("bigwig_remote_build")
    a_grr().with_resource("one", a_bigwig_score()).build_repo(root_path)
    if grr_scheme == "file":
        yield build_filesystem_test_protocol(root_path)
        return
    if grr_scheme == "http":
        with build_http_test_protocol(root_path) as proto:
            yield proto
        return
    if grr_scheme == "s3":
        with build_s3_test_protocol(root_path) as proto:
            yield proto
        return
    raise ValueError(f"unexpected protocol scheme: <{grr_scheme}>")


@pytest.mark.grr_full
@pytest.mark.grr_http
def test_bigwig_opens_over_a_scheme_the_build_can_reach(
    bigwig_proto: FsspecReadOnlyProtocol, grr_scheme: str,
) -> None:
    # The one place the two pyBigWig builds meet a real remote file: a curl
    # build reads it over the wire, and a ``file`` GRR reads on either.
    if grr_scheme != "file" and not pyBigWig.remote:  # pylint: disable=I1101
        pytest.skip("this pyBigWig build has no remote-file support")
    resource = bigwig_proto.get_resource("one")

    bw_file = resource.open_bigwig_file(_BIGWIG_FILE_NAME)

    assert bw_file.intervals("chr1", 0, 10) == ((0, 10, pytest.approx(0.1)),)


@pytest.mark.grr_full
@pytest.mark.grr_http
def test_bigwig_over_a_remote_scheme_is_refused_by_a_no_curl_build(
    bigwig_proto: FsspecReadOnlyProtocol, grr_scheme: str,
) -> None:
    if grr_scheme == "file" or pyBigWig.remote:  # pylint: disable=I1101
        pytest.skip("this pyBigWig build can open remote files")
    resource = bigwig_proto.get_resource("one")

    with pytest.raises(OSError, match="no remote-file support"):
        resource.open_bigwig_file(_BIGWIG_FILE_NAME)
