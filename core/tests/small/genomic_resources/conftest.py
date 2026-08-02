# pylint: disable=W0621,C0114,C0116,C0415,W0212,W0613

import gzip
import logging
import os
import pathlib
import threading
from collections.abc import Callable, Generator
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
    FsspecRepositoryProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_http_test_protocol,
    build_inmemory_test_protocol,
    convert_to_tab_separated,
    copy_proto_genomic_resources,
    s3_test_protocol,
    setup_directories,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def alabala_gz() -> bytes:
    return gzip.compress(b"alabala")


@pytest.fixture
def content_fixture(alabala_gz: bytes) -> dict[str, Any]:
    demo_gtf_content = "TP53\tchr3\t300\t200"

    return {
        "one": {
            GR_CONF_FILE_NAME: "",
            "data.txt": "alabala",
            "data.txt.gz": alabala_gz,
        },
        "sub": {
            "two": {
                GR_CONF_FILE_NAME: "",
            },
            "two(1.0)": {
                GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                "genes.gtf": demo_gtf_content,
            },
        },
        "three(2.0)": {
            GR_CONF_FILE_NAME: "",
            "sub1": {
                "a.txt": "a",
            },
            "sub2": {
                "b.txt": "b",
            },
        },
        "xxxxx-genome": {
            "genomic_resource.yaml": "type: genome\nfilename: chr.fa",
            "chr.fa": convert_to_tab_separated(
                """
                    >xxxxx
                    NNACCCAAAC
                    GGGCCTTCCN
                    NNNA
                """),
            "chr.fa.fai": "xxxxx\t24\t7\t10\t11\n",
        },
    }


@pytest.fixture
def fsspec_proto(
    content_fixture: dict[str, Any],
    tmp_path: pathlib.Path,
    grr_scheme: str,
    mocker: pytest_mock.MockerFixture,
) -> Generator[FsspecRepositoryProtocol, None, None]:

    root_path = tmp_path
    setup_directories(root_path, content_fixture)

    if grr_scheme == "file":
        yield build_filesystem_test_protocol(root_path)
        return

    if grr_scheme == "s3":
        mocker.patch.dict(os.environ, {
            "AWS_SECRET_ACCESS_KEY": "minioadmin",
            "AWS_ACCESS_KEY_ID": "minioadmin",
        })
        proto = s3_test_protocol()
        copy_proto_genomic_resources(
            proto,
            build_filesystem_test_protocol(root_path))
        yield proto
        return

    if grr_scheme == "inmemory":
        yield build_inmemory_test_protocol(content=content_fixture)
        return

    if grr_scheme == "http":
        with build_http_test_protocol(root_path) as http_proto:
            yield http_proto
        return

    raise ValueError(f"unexpected protocol scheme: <{grr_scheme}>")


#: Run a callable in N threads whose starts are aligned by a barrier, and
#: return ``(results, errors)``. See the ``run_in_threads`` fixture.
RunInThreads = Callable[..., tuple[list[Any], list[BaseException]]]


@pytest.fixture
def run_in_threads() -> RunInThreads:
    """Run a callable concurrently in several threads, starts aligned.

    The barrier is what makes a check-then-populate race reproducible rather
    than merely likely: every thread is released into ``work`` at the same
    moment. Every thread is joined with a timeout and its liveness asserted,
    so a lock-ordering regression fails the test loudly instead of hanging
    the suite.
    """

    def run(
        work: Callable[[], Any],
        threads_count: int = 8,
        timeout: float = 60.0,
    ) -> tuple[list[Any], list[BaseException]]:
        barrier = threading.Barrier(threads_count)
        guard = threading.Lock()
        results: list[Any] = []
        errors: list[BaseException] = []

        def target() -> None:
            # pylint: disable=broad-exception-caught
            try:
                barrier.wait()
                result = work()
            except BaseException as exc:  # noqa: BLE001
                with guard:
                    errors.append(exc)
            else:
                with guard:
                    results.append(result)

        # Daemon threads on purpose: a thread stuck on a lock would otherwise
        # keep the interpreter alive at exit and hang the whole suite long
        # after this test has already reported its failure.
        threads = [
            threading.Thread(target=target, daemon=True)
            for _ in range(threads_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=timeout)
            assert not thread.is_alive(), \
                "thread did not finish in time -- deadlock?"

        return results, errors

    return run


#: Resource ids of the small repository ``setup_small_repo`` lays out, and
#: that the ``read_only_proto`` / ``read_write_proto`` fixtures serve. Shared
#: by the two modules covering the resource memo's concurrency and
#: invalidation semantics (#458, #513): they need the identical repository,
#: and keeping two copies of the setup is how one of them silently drifted
#: into needing an FTS index the other did not build.
SMALL_REPO_RESOURCE_IDS = ("one", "two", "three")


def setup_small_repo(root_path: pathlib.Path) -> None:
    """Lay out a small repository: manifests, contents and FTS index."""
    setup_directories(root_path, {
        resource_id: {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "alabala",
        }
        for resource_id in SMALL_REPO_RESOURCE_IDS
    })
    # Repairs the repository: writes every manifest and the ``.CONTENTS`` a
    # read-only protocol needs in order to load anything at all.
    rw_proto = build_filesystem_test_protocol(root_path)
    # ``search_resources`` answers from the FTS index, not from ``.CONTENTS``,
    # so a test that goes through it needs the index to exist.
    _create_contents_db(rw_proto)


@pytest.fixture
def read_only_proto(tmp_path: pathlib.Path) -> FsspecReadOnlyProtocol:
    """A read-only protocol over a freshly repaired small repository."""
    root_path = tmp_path / "grr"
    setup_small_repo(root_path)
    return build_filesystem_test_protocol(
        root_path, repair=False, read_only=True)


@pytest.fixture
def read_write_proto(tmp_path: pathlib.Path) -> FsspecReadWriteProtocol:
    """A read-write protocol over a freshly repaired small repository."""
    root_path = tmp_path / "grr"
    setup_small_repo(root_path)
    return build_filesystem_test_protocol(root_path, repair=False)


#: Id of the single ``type: basic`` resource ``BASIC_RESOURCE_LAYOUT`` lays
#: out and the ``resource`` fixture below serves. Shared by the two modules
#: covering the resource itself -- what makes two of them the same value
#: (#524) and its manifest memo surviving a concurrent invalidate (#519):
#: they need the identical resource, and keeping two copies of one setup is
#: how the pair above silently drifted apart.
BASIC_RESOURCE_ID = "one"

BASIC_RESOURCE_LAYOUT = {
    BASIC_RESOURCE_ID: {
        GR_CONF_FILE_NAME: "type: basic\n",
        "data.txt": "alabala",
    },
}


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    """A resource whose manifest is written and loadable."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, BASIC_RESOURCE_LAYOUT)
    # Repairing the repository is what writes the manifest.
    proto = build_filesystem_test_protocol(root_path)
    return proto.get_resource(BASIC_RESOURCE_ID)
