# pylint: disable=W0621,C0116
"""``scripts/bioconda_lag_check.py`` decides whether bioconda lags PyPI.

The ``Check bioconda lag`` stage of ``Jenkinsfile.nightly`` runs the
script. The tests replace the fetch of the two JSON bodies and the
current time with ``monkeypatch``, so no test reaches the network.
"""
import datetime
import email.message
import importlib.util
import io
import json
import pathlib
import sys
import urllib.error
from collections.abc import Callable
from types import ModuleType

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "bioconda_lag_check.py"

PYPI_URL = "https://pypi.org/pypi/gain-core/json"
BIOCONDA_URL = "https://api.anaconda.org/package/bioconda/gain-core"

UPLOADED = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=datetime.UTC)


def pypi_body(version: str = "2026.11.0") -> dict:
    return {
        "info": {"version": version},
        "releases": {
            "2026.10.2": [
                {"upload_time_iso_8601": "2026-10-20T09:00:00.000000Z"},
            ],
            version: [
                {"upload_time_iso_8601": "2026-11-03T12:30:00.000000Z"},
                {"upload_time_iso_8601": "2026-11-03T12:00:00.000000Z"},
            ],
        },
    }


def bioconda_body(version: str = "2026.11.0") -> dict:
    return {"name": "gain-core", "latest_version": version}


@pytest.fixture(scope="module")
def lag_check() -> ModuleType:
    if not SCRIPT.exists():
        pytest.fail(f"{SCRIPT} is missing")
    spec = importlib.util.spec_from_file_location(
        "bioconda_lag_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serve(
    lag_check: ModuleType, monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., None]:
    """Answer the two URLs with the given bodies at the given time.

    A body of type ``bytes`` goes out as is. Any other body goes out as
    JSON. A body that is an exception is raised by the fetch.
    """
    def _serve(
        pypi: object, bioconda: object, age: datetime.timedelta,
    ) -> None:
        bodies = {PYPI_URL: pypi, BIOCONDA_URL: bioconda}

        def fetch(url: str) -> bytes:
            body = bodies[url]
            if isinstance(body, BaseException):
                raise body
            if isinstance(body, bytes):
                return body
            return json.dumps(body).encode()

        monkeypatch.setattr(lag_check, "fetch", fetch)
        monkeypatch.setattr(lag_check, "now", lambda: UPLOADED + age)
    return _serve


def test_equal_versions_with_an_old_release_exit_0(
    lag_check: ModuleType, serve: Callable[..., None],
) -> None:
    serve(pypi_body(), bioconda_body(), datetime.timedelta(days=30))

    assert lag_check.main() == 0


@pytest.mark.parametrize("age", [
    datetime.timedelta(days=1),
    datetime.timedelta(days=7),
])
def test_different_versions_with_a_young_release_exit_0(
    lag_check: ModuleType, serve: Callable[..., None],
    age: datetime.timedelta,
) -> None:
    serve(pypi_body(), bioconda_body("2026.10.2"), age)

    assert lag_check.main() == 0


@pytest.mark.parametrize("age", [
    # The later upload of the release is 30 minutes younger, so the age
    # counts from the earliest upload only at this age.
    datetime.timedelta(days=7, minutes=10),
    datetime.timedelta(days=30),
])
def test_different_versions_with_an_old_release_exit_1(
    lag_check: ModuleType, serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str], age: datetime.timedelta,
) -> None:
    serve(pypi_body(), bioconda_body("2026.10.2"), age)

    code = lag_check.main()

    out = capsys.readouterr().out
    assert code == 1
    assert "2026.11.0" in out
    assert "2026.10.2" in out
    assert f"{age.days} days old" in out


def copy_and_parent(
    body: dict, path: tuple[str | int, ...],
) -> tuple[dict, dict]:
    """Return a deep copy of ``body`` and the parent of ``path`` in it."""
    copy: dict = json.loads(json.dumps(body))
    parent = copy
    for key in path[:-1]:
        parent = parent[key]
    return copy, parent


def without(body: dict, *path: str | int) -> dict:
    """Return a deep copy of ``body`` without the key at ``path``."""
    copy, parent = copy_and_parent(body, path)
    del parent[path[-1]]
    return copy


# Each body differs from bioconda_body("2026.10.2") in its version, and
# the release is old, so a script that ignores the fault exits 1.
OLD = datetime.timedelta(days=30)


@pytest.mark.parametrize(("pypi", "bioconda"), [
    (without(pypi_body(), "info"), bioconda_body("2026.10.2")),
    (without(pypi_body(), "info", "version"), bioconda_body("2026.10.2")),
    (without(pypi_body(), "releases"), bioconda_body("2026.10.2")),
    (without(pypi_body(), "releases", "2026.11.0"),
     bioconda_body("2026.10.2")),
    (without(pypi_body(), "releases", "2026.11.0", 0,
             "upload_time_iso_8601"),
     bioconda_body("2026.10.2")),
    (pypi_body(), without(bioconda_body("2026.10.2"), "latest_version")),
], ids=[
    "pypi-info", "pypi-version", "pypi-releases", "pypi-release",
    "pypi-upload-time", "bioconda-latest-version",
])
def test_a_missing_key_exits_2(
    lag_check: ModuleType, serve: Callable[..., None],
    pypi: dict, bioconda: dict,
) -> None:
    serve(pypi, bioconda, OLD)

    assert lag_check.main() == 2


def with_value(body: dict, value: object, *path: str | int) -> dict:
    """Return a deep copy of ``body`` with ``value`` at ``path``."""
    copy, parent = copy_and_parent(body, path)
    parent[path[-1]] = value
    return copy


@pytest.mark.parametrize(("pypi", "bioconda"), [
    (b"<html>502 Bad Gateway</html>", bioconda_body("2026.10.2")),
    (pypi_body(), b"<html>502 Bad Gateway</html>"),
    (with_value(pypi_body(), ["2026.11.0"], "releases"),
     bioconda_body("2026.10.2")),
    (with_value(pypi_body(), [], "releases", "2026.11.0"),
     bioconda_body("2026.10.2")),
    (with_value(pypi_body(), 20261103, "releases", "2026.11.0", 0,
                "upload_time_iso_8601"),
     bioconda_body("2026.10.2")),
    (pypi_body(), with_value(bioconda_body(), None, "latest_version")),
    (pypi_body(), with_value(bioconda_body(), 2026, "latest_version")),
    (pypi_body(), ["2026.10.2"]),
], ids=[
    "pypi-not-json", "bioconda-not-json", "releases-not-a-dict",
    "release-without-files", "upload-time-not-a-string",
    "latest-version-null",
    "latest-version-not-a-string", "bioconda-not-an-object",
])
def test_a_body_of_the_wrong_shape_exits_2(
    lag_check: ModuleType, serve: Callable[..., None],
    pypi: object, bioconda: object,
) -> None:
    serve(pypi, bioconda, OLD)

    assert lag_check.main() == 2


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, code, "error", email.message.Message(), io.BytesIO())


@pytest.mark.parametrize(("pypi", "bioconda"), [
    (urllib.error.URLError("Name or service not known"),
     bioconda_body("2026.10.2")),
    (pypi_body(), urllib.error.URLError("Connection refused")),
    (TimeoutError("timed out"), bioconda_body("2026.10.2")),
    (http_error(PYPI_URL, 503), bioconda_body("2026.10.2")),
    (pypi_body(), http_error(BIOCONDA_URL, 404)),
], ids=[
    "pypi-dns", "bioconda-refused", "pypi-timeout", "pypi-http-503",
    "bioconda-http-404",
])
def test_a_failed_fetch_exits_2(
    lag_check: ModuleType, serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str], pypi: object, bioconda: object,
) -> None:
    serve(pypi, bioconda, OLD)

    code = lag_check.main()

    assert code == 2
    assert "cannot decide" in capsys.readouterr().err


def test_an_upload_time_without_a_zone_exits_2(
    lag_check: ModuleType, serve: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Every file of the release lacks the zone. With one aware file,
    # min() raises TypeError before the zone check.
    pypi = pypi_body()
    for item in pypi["releases"]["2026.11.0"]:
        item["upload_time_iso_8601"] = "2026-11-03T12:00:00"
    serve(pypi, bioconda_body("2026.10.2"), OLD)

    code = lag_check.main()

    assert code == 2
    assert "upload time without a zone" in capsys.readouterr().err
