# pylint: disable=W0621,C0114,C0116,C0415,W0212,W0613

import contextlib
import gzip
import logging
import os
import pathlib
import shutil
import threading
from collections.abc import Callable, Generator, Iterable, Iterator
from typing import Any
from unittest import mock

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
    ResourceFileState,
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
            except BaseException as exc:  # ruff: ignore[blind-except]
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
#: assertions vacuous rather than failing. Shared by the four modules
#: covering what a sync must not spend -- three on the download (re-reading
#: the md5 (#865), re-stating the stat (#936), probing directories (#1042))
#: and one on the cache verdict for a file it keeps (#1039) -- for the reason
#: above: a second copy of one list is how the first of them would silently
#: stop describing the fixture. Assert it through
#: :func:`assert_published_one` rather than by hand.
ONE_RESOURCE_FILES = ["data.txt", "data.txt.gz", "genomic_resource.yaml"]

#: The filesystem operations that ask the store about a path without moving
#: any of its bytes -- what a per-file budget counts. Shared for the reason
#: :data:`ONE_RESOURCE_FILES` is: an operation added to one copy of this
#: tuple and not to the other is how a budget stops noticing the call that
#: regressed. Pass it to :func:`record_filesystem_calls`.
METADATA_OPERATIONS = ("info", "exists", "modified", "ls")


@pytest.fixture
def download_dest(
    grr_scheme: str, tmp_path: pathlib.Path,
) -> FsspecReadWriteProtocol:
    """An empty read-write destination on the scheme under test.

    One branch per read-write scheme, and an unknown scheme raises rather
    than falling through to a local protocol -- the same enumeration
    discipline ``fsspec_proto`` above keeps, and for a sharper reason
    here. ``test_grr_scheme_parametrization`` exists to hold the
    ``grr_rw`` set honest against the markers; a silent fallthrough would
    hand every test parametrized on a newly added scheme a ``file``
    protocol and pass, defeating that guard one layer down.

    Empty because the tests that ask for it measure a *first* download; a
    destination that already held the resource would take the cache
    branch and copy nothing.
    """
    if grr_scheme == "s3":
        return s3_test_protocol()
    if grr_scheme == "inmemory":
        return build_inmemory_test_protocol({})
    if grr_scheme == "file":
        dest_root = tmp_path / "dest"
        dest_root.mkdir()
        return build_filesystem_test_protocol(dest_root)

    raise ValueError(f"unexpected read-write scheme: <{grr_scheme}>")


@contextlib.contextmanager
def record_filesystem_calls(
    proto: FsspecReadWriteProtocol,
    operations: Iterable[str],
) -> Iterator[list[tuple[str, str]]]:
    """Log every call in ``operations`` made inside the block, as (op, path).

    Only the outermost call is logged -- see the re-entrancy guard.

    Wrapped on the filesystem the protocol was handed, which ADR 0021
    names as the protocol's real contract boundary -- the same seam the
    fault tests inject at, here reading rather than writing. Counting
    anywhere higher would count the protocol's own vocabulary instead of
    the round trips, which is the thing that costs.

    ``test_s3_test_protocol_population`` records the same shape one layer
    down, at ``AioBaseClient._make_api_call`` -- actual HTTP requests
    rather than protocol calls. That is the sharper instrument and the
    s3-only one; this counts what the protocol asks for, on every
    scheme, which is what a per-field fetch would regress.

    Shared rather than copied per module for the reason
    :data:`ONE_RESOURCE_FILES` is: the re-entrancy guard below is the
    whole correctness of the count, and a second copy of it is how one
    of its consumers would drift into counting a different thing.

    ``mock.patch.object``, not ``monkeypatch.setattr``, because of what
    the wrapped object can be. On the ``inmemory`` scheme
    ``proto.filesystem`` is fsspec's process-global ``MemoryFileSystem``,
    shared with every other in-memory protocol for the whole session.
    Patching an *instance* attribute that is really inherited from the
    class is the case the two libraries treat differently: monkeypatch's
    undo puts the class's bound method back as a permanent instance
    attribute, where ``patch.object`` deletes what was never there and
    leaves the singleton as it found it.

    Note also that on that scheme the recorder cannot attribute a call
    to one protocol: a source and a destination built in the same test
    *are* the same filesystem object, so both protocols' calls land in
    one log and share the one re-entrancy flag. Callers therefore filter
    by path -- see :func:`calls_for` -- and a caller wanting a total
    rather than a per-path count would need to say which scheme it means.
    """
    calls: list[tuple[str, str]] = []
    filesystem = proto.filesystem
    inside = False

    def wrap(operation: str, inner: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(path: str, *args: Any, **kwargs: Any) -> Any:
            nonlocal inside
            if inside:
                # A call one of these makes for itself, not one the
                # protocol asked for: fsspec's local ``modified`` and
                # ``exists`` are both ``info`` underneath, and
                # ``makedirs`` is ``mkdir``, so counting the inner call
                # would make the number a property of the backend rather
                # than of what the protocol requested.
                return inner(path, *args, **kwargs)
            calls.append((operation, str(path)))
            inside = True
            try:
                return inner(path, *args, **kwargs)
            finally:
                inside = False
        return wrapped

    with contextlib.ExitStack() as patches:
        for operation in operations:
            patches.enter_context(mock.patch.object(
                filesystem, operation,
                wrap(operation, getattr(filesystem, operation))))
        yield calls


def calls_for(calls: list[tuple[str, str]], path: str) -> list[str]:
    """The operations asked about ``path``, in the order they were made.

    The one reader of :func:`record_filesystem_calls`'s log format, so
    that the modules asserting over it -- a per-file budget (#936), a
    per-directory one (#1042) -- do not each spell out the filtering.
    """
    return [operation for operation, called in calls if called == path]


def a_source_resource(content_fixture: dict[str, Any]) -> GenomicResource:
    """The ``one`` resource of ``content_fixture``, to be copied from.

    In memory because the tests that copy it measure what the
    *destination* spends; a source that cost round trips of its own
    would be counted by any recorder wrapping a shared filesystem.
    """
    return build_inmemory_test_protocol(content_fixture).get_resource("one")


def assert_published_one(
    proto: FsspecReadWriteProtocol, resource: GenomicResource,
) -> list[str]:
    """Assert the copy published :data:`ONE_RESOURCE_FILES`, and return it.

    Shared for the reason the constant itself is: this assertion is what
    stops the per-file budgets from passing vacuously against a resource
    that quietly lost its files, and it is worth exactly one spelling.
    """
    published = sorted(entry.name for entry in proto.get_manifest(resource))
    assert published == ONE_RESOURCE_FILES, "fixture changed; update this pin"
    return published


def copy_one_resource(
    src_resource: GenomicResource, dest_proto: FsspecReadWriteProtocol,
) -> tuple[GenomicResource, list[str]]:
    """Copy ``src_resource`` in, and return it with its published names.

    Takes the source rather than the fixture dict so a caller that also
    needs it -- a cache verdict is asked against the same remote the
    cache was filled from -- passes the one it holds. Building a second
    source would populate the fixture twice, under two roots, and leave
    the reader to establish that the two are the same thing.
    """
    dest_resource = dest_proto.copy_resource(src_resource)
    return dest_resource, assert_published_one(dest_proto, dest_resource)


def assert_state_matches_accessors(
    proto: FsspecReadWriteProtocol, resource: GenomicResource, name: str,
) -> None:
    """Assert the recorded state is what the accessors report for ``name``.

    The claim every path that assembles a state from fields it holds --
    a download (#936), a cache verdict (#1039) -- has to keep: a field
    that now comes from somewhere else must still read identically, to
    its rounding and to the ``None`` a store without tokens reports.
    Spelled once, because a copy that misses a newly added field stops
    comparing it without failing.
    """
    recorded = proto.load_resource_file_state(resource, name)
    assert recorded == ResourceFileState(
        filename=name,
        size=proto.get_resource_file_size(resource, name),
        timestamp=proto.get_resource_file_timestamp(resource, name),
        md5=proto.compute_md5_sum(resource, name),
        change_token=proto.get_resource_file_change_token(resource, name),
    ), name


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


#: Every value a curator can write for a label that names a resource
#: which cannot be one, paired with how the narrowing reports it.
#: ``None`` is absent rather than unusable and is covered separately.
#:
#: Shared by the three readers gain#1053 narrowed -- the score info
#: page, the score statistics build and the liftover chain -- so a
#: shape added here is pinned at all of them at once.  gain#1050's
#: gene-models file keeps its own copy deliberately: that issue's tests
#: are the evidence that hoisting the helper changed nothing, which is
#: worth more than sharing a list with them.
UNUSABLE_RESOURCE_ID_LABELS = [
    pytest.param(2019, "int", id="an-int"),
    pytest.param(0, "int", id="the-int-zero"),
    pytest.param(False, "bool", id="a-bool"),
    pytest.param(["a", "b"], "list", id="a-list"),
    pytest.param({"k": "v"}, "dict", id="a-nested-mapping"),
    pytest.param("", "empty", id="an-empty-string"),
]


def label_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Only the warnings a ``meta.labels`` read emitted.

    :func:`captured_warnings` is the right assertion where the fixture
    is quiet, and the sharper one: it catches a second warning nobody
    meant to emit.  It cannot be used where the resource under test
    warns about something else as well -- a score table that omits
    ``zero_based`` says so once per open -- and counting those in would
    pin an unrelated message's arity.  Selected on the label prefix
    every such warning starts with, so a narrowing that stops reporting
    still fails the count (gain#1053).
    """
    return [
        message for message in captured_warnings(caplog)
        if "meta.labels." in message
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
