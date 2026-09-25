# pylint: disable=C0116
"""Where the s3 test helpers look for the S3 fixture server.

``S3_HOST`` names the server. ``MINIO_HOST`` -- its name while the fixture
was MinIO -- is still honoured when ``S3_HOST`` is unset, because gpf
imports these helpers and its CI passes ``MINIO_HOST`` until gpf#1029
moves it over (gain#1708).
"""
import pytest
from gain.genomic_resources.testing import s3_test_server_endpoint


@pytest.fixture(autouse=True)
def _no_host_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_HOST", raising=False)
    monkeypatch.delenv("MINIO_HOST", raising=False)


def test_the_local_dev_port_is_the_default() -> None:
    assert s3_test_server_endpoint() == "http://localhost:29000"


@pytest.mark.parametrize(("host", "expected"), [
    pytest.param("s3:9000", "http://s3:9000", id="host-and-port"),
    pytest.param("s3", "http://s3:9000", id="bare-host-gets-9000"),
])
def test_s3_host_names_the_server(
    monkeypatch: pytest.MonkeyPatch, host: str, expected: str,
) -> None:
    monkeypatch.setenv("S3_HOST", host)

    assert s3_test_server_endpoint() == expected


@pytest.mark.parametrize(("host", "expected"), [
    pytest.param("minio:9000", "http://minio:9000", id="host-and-port"),
    pytest.param("minio", "http://minio:9000", id="bare-host-gets-9000"),
])
def test_minio_host_is_still_honoured_on_its_own(
    monkeypatch: pytest.MonkeyPatch, host: str, expected: str,
) -> None:
    monkeypatch.setenv("MINIO_HOST", host)

    assert s3_test_server_endpoint() == expected


def test_s3_host_wins_over_minio_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_HOST", "s3:9000")
    monkeypatch.setenv("MINIO_HOST", "minio:9000")

    assert s3_test_server_endpoint() == "http://s3:9000"
