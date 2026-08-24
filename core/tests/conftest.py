# pylint: disable=W0621,C0114,C0116,W0212,W0613
import logging
from collections.abc import Generator, Iterable

import pytest
import pytest_mock
from gain.genomic_resources.genomic_context import (
    get_genomic_context,
)
from gain.genomic_resources.genomic_context_base import GenomicContext
from gain.genomic_resources.resource_types import (
    LEGACY_VOCABULARY_REMOVAL_RELEASE,
    reset_deprecation_notices,
)

# The dask-ownership guard (#851): the hook wraps Client.__init__ once
# per process, the autouse fixture charges each construction to the test
# it happened in. Imported names are how a conftest adopts a plugin
# module's hooks and fixtures.
from tests.dask_guard import (  # noqa: F401  # pylint: disable=unused-import
    dask_clusters_are_owned,
    pytest_configure,
)


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


@pytest.fixture(autouse=True)
def clean_deprecation_notices() -> None:
    """Forget which deprecation warnings this worker already announced.

    ``warn_deprecated_spelling`` announces each distinct message once per
    process, so that a repository-wide statistics sweep names an offending
    resource once rather than once per region task.  That set outlives a
    test, so without this every "the legacy spelling warns" assertion would
    depend on whether an earlier test in the same worker consumed it -- and
    would pass or fail differently under ``-p no:randomly`` or ``-n``.
    """
    reset_deprecation_notices()


LEGACY_VOCABULARY_MARKER = "legacy_vocabulary"


class _DeprecationNoticeRecorder(logging.Handler):
    """Collect the legacy-vocabulary deprecation notices of one test.

    Recognises a notice by the removal release it names, which every one of
    them carries by construction -- ``warn_deprecated_spelling`` renders it
    into the message, and a notice that did not say when the spelling stops
    being accepted would be a defect in its own right.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.notices: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if LEGACY_VOCABULARY_REMOVAL_RELEASE in message:
            self.notices.append(message)


@pytest.fixture(autouse=True)
def deprecation_notices_are_owned(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Fail a test that emits a deprecation notice without owning one.

    The point of gain#538 is that a legacy spelling is announced to the
    person who wrote it.  A suite whose own fixtures declare the legacy
    spelling out of habit turns that announcement into background noise:
    the notices scroll past in CI naming resources nobody can migrate,
    because they exist only inside a test.

    So a test that provokes a notice must say so, with
    ``@pytest.mark.legacy_vocabulary``.  Everything else must use the
    preferred spellings; if this fixture fails a test, the fix is almost
    always to modernise that test's fixture, not to add the marker.
    """
    recorder = _DeprecationNoticeRecorder()
    root_logger = logging.getLogger()
    root_logger.addHandler(recorder)
    try:
        yield
    finally:
        root_logger.removeHandler(recorder)

    if not recorder.notices:
        return
    if request.node.get_closest_marker(LEGACY_VOCABULARY_MARKER) is not None:
        return
    pytest.fail(
        "emitted a legacy-vocabulary deprecation notice without being "
        f"marked '{LEGACY_VOCABULARY_MARKER}'; use the preferred spelling "
        "in this test's configuration, or mark the test if it exists to "
        "exercise the legacy half:\n  "
        + "\n  ".join(recorder.notices))


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
