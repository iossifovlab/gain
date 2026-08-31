# pylint: disable=W0621,C0114,C0116,C0415,W0212,W0613

import gzip
import logging
import os
import pathlib
import shutil
import threading
from collections.abc import Callable, Generator
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.cli import _create_contents_db, cli_manage
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
    FsspecRepositoryProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GR_CONTENTS_FILE_NAME,
    GR_INDEX_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
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
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
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

#: The files ``content_fixture``'s ``one`` resource publishes, in manifest
#: order. Pinned rather than derived because the tests that use it assert a
#: per-file cost, and a resource that quietly lost its files would make those
#: assertions vacuous rather than failing. Shared by the two modules covering
#: what a download must not re-read -- the md5 (#865) and the stat (#936) --
#: for the reason above: three copies of one list is how the first of them
#: would silently stop describing the fixture.
ONE_RESOURCE_FILES = ["data.txt", "data.txt.gz", "genomic_resource.yaml"]


@pytest.fixture
def download_dest(
    grr_scheme: str, tmp_path: pathlib.Path,
) -> FsspecReadWriteProtocol:
    """An empty read-write destination on the scheme under test.

    Empty because the tests that ask for it measure a *first* download; a
    destination that already held the resource would take the cache
    branch and copy nothing.
    """
    if grr_scheme == "s3":
        return s3_test_protocol()
    dest_root = tmp_path / "dest"
    dest_root.mkdir()
    return build_filesystem_test_protocol(dest_root)


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


#: Every repository-global artifact the scope contract of gain#760 is
#: about: what a `repo-index` publishes and a `resource-*` command must
#: leave untouched. Shared by every module pinning that contract, so
#: none can drift to a subset. The uncompressed ``.CONTENTS.json`` is
#: not here: since #758 it is a legacy artifact nothing publishes.
GLOBAL_ARTIFACTS = (
    GR_CONTENTS_FILE_NAME,
    GR_SQLITE_META_FILE_NAME,
    GR_INDEX_FILE_NAME,
)


def read_published_contents(repo_path: pathlib.Path) -> str:
    """The text of the repository's published (gzipped) contents index."""
    return gzip.decompress(
        (repo_path / GR_CONTENTS_FILE_NAME).read_bytes()).decode("utf8")


def empty_the_repository(repo_path: pathlib.Path) -> None:
    """Delete every resource of ``settled_repo``, globals left behind.

    The state a repository is in after its last resource was deleted:
    the resource is gone from disk but every published global artifact
    still advertises it.
    """
    shutil.rmtree(repo_path / "sub")
    assert "sub/one" in read_published_contents(repo_path)


def snapshot_globals(repo_path: pathlib.Path) -> dict[str, bytes]:
    """The bytes of every repository-global artifact, to compare against.

    What "left untouched" means for a command that must not publish:
    equal bytes, not merely a file that still exists.
    """
    return {
        artifact: (repo_path / artifact).read_bytes()
        for artifact in GLOBAL_ARTIFACTS
    }


@pytest.fixture(scope="session")
def _settled_grr_template(
        tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A repaired two-score repository, built once per session.

    A full ``repo-repair`` is the most expensive CLI operation the small
    tests run; the per-test ``settled_repo`` below copies this tree
    instead of re-repairing it.
    """
    path = tmp_path_factory.mktemp("settled_grr_template")
    repo = a_grr()
    for rid in ("sub/one", "sub/two"):
        repo = repo.with_resource(
            rid,
            a_position_score()
            .with_score("phastCons", "float")
            .with_data("""
                chrom  pos_begin  phastCons
                1      10         0.1
                1      11         0.2
            """),
        )
    repo.build_repo(path)
    cli_manage(["repo-repair", "-R", str(path), "-j", "1"])
    return path


@pytest.fixture
def settled_repo(
    _settled_grr_template: pathlib.Path,
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """A two-score repository fully repaired, globals included."""
    repo_path = tmp_path / "grr"
    # copy2 (copytree's default) keeps mtimes, so the `.grr` state
    # receipts stay valid in the copy.
    shutil.copytree(_settled_grr_template, repo_path)
    return repo_path


def captured_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The message of every warning-or-worse record captured."""
    return [
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


def a_resource_whose_meta_is(
    tmp_path: pathlib.Path, meta: Any,
) -> GenomicResource:
    """A resource under a real id whose whole ``meta:`` block is ``meta``.

    Built through a GRR rather than as a lone resource: ``build_resource``
    gives the resource an EMPTY id, against which ``id in message`` holds
    for every message ever written and pins nothing.

    Shared by the two files that cover the shape of the block -- the
    ``meta.labels`` level (gain#654) and the ``meta`` level (gain#1004).
    """
    return (
        a_grr()
        .with_resource(
            "scores/broken", a_position_score().with_raw_meta(meta))
        .build_repo(tmp_path)
        .get_resource("scores/broken")
    )


def index_row(resource: GenomicResource) -> dict[str, str]:
    """The resource's FTS index row, keyed by the column it lands in.

    Shared by the files that cover what a row carries -- the shape of the
    ``meta`` block it is collected from (gain#1004) and the values the
    meta-derived columns end up with (gain#1008).
    """
    header, row = build_resource_implementation(resource).collect_index_info()
    return dict(zip(header, row, strict=True))
