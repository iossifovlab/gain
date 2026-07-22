# pylint: disable=W0621,C0114,C0116,W0212,W0613
from collections.abc import Iterable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_context import (
    get_genomic_context,
)
from gain.genomic_resources.genomic_context_base import GenomicContext


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--enable-http-testing", "--http",
        dest="enable_http",
        action="store_true",
        default=False,
        help="enable HTTP unit testing")

    parser.addoption(
        "--enable-s3-testing", "--s3",
        dest="enable_s3",
        action="store_true",
        default=False,
        help="enable S3 unit testing")

    parser.addoption(
        "--enable-process-pool", "--pp",
        dest="enable_pp",
        action="store_true",
        default=False,
        help="enable process pool unit testing")


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "grr_scheme" in metafunc.fixturenames:
        _generate_grr_schemes_fixtures(metafunc)


ALL_GRR_SCHEMES = frozenset({"file", "inmemory", "http", "s3"})


def grr_schemes_for_marks(
    mark_names: Iterable[str],
    *,
    enable_s3: bool = False,
    enable_http: bool = False,
) -> list[str]:
    """Return the GRR schemes to parametrize over, in a defined order.

    The selection itself is set arithmetic, but the result is handed to
    ``metafunc.parametrize``, and pytest-xdist compares the *ordered* list of
    collected test IDs across workers. Iteration order of a ``set`` of ``str``
    follows the strings' hashes, which CPython randomizes per process, so
    every worker would order the parametrized IDs differently and the run
    would abort at collection. Sorting here is what makes collection order
    seed-independent.
    """
    schemes = {"inmemory", "file"}
    if enable_s3:
        schemes.add("s3")
    if enable_http:
        schemes.add("http")

    marked_schemes = {
        name[4:] for name in mark_names if name.startswith("grr_")
    }
    if "rw" in marked_schemes:
        marked_schemes.add("file")
        marked_schemes.add("s3")
        marked_schemes.add("inmemory")
    if "full" in marked_schemes:
        marked_schemes.add("file")
        marked_schemes.add("s3")
    if "tabix" in marked_schemes:
        marked_schemes.add("file")
        marked_schemes.add("s3")
        marked_schemes.add("http")

    marked_schemes = marked_schemes & ALL_GRR_SCHEMES
    if marked_schemes:
        schemes = schemes & marked_schemes

    return sorted(schemes)


def _generate_grr_schemes_fixtures(metafunc: pytest.Metafunc) -> None:
    mark_names = [
        mark.name
        for mark in getattr(
            getattr(metafunc, "function", None), "pytestmark", [])
    ]
    metafunc.parametrize(
        "grr_scheme",
        grr_schemes_for_marks(
            mark_names,
            enable_s3=metafunc.config.getoption("enable_s3"),
            enable_http=metafunc.config.getoption("enable_http"),
        ),
        scope="module")


@pytest.fixture(autouse=True)
def clean_genomic_context(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "gain.genomic_resources.genomic_context._REGISTERED_CONTEXTS",
        [])


@pytest.fixture
def clean_genomic_context_providers(
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "gain.genomic_resources.genomic_context._REGISTERED_CONTEXT_PROVIDERS",
        [])


@pytest.fixture
def context_fixture(
    mocker: pytest_mock.MockerFixture,
) -> GenomicContext:
    mocker.patch(
        "gain.genomic_resources.genomic_context._REGISTERED_CONTEXT_PROVIDERS",
        [])
    mocker.patch(
        "gain.genomic_resources.genomic_context._REGISTERED_CONTEXTS",
        [])
    context = get_genomic_context()
    assert context is not None
    return context
