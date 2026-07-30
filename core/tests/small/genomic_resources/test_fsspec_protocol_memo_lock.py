# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The fsspec protocols' resource memo must survive a concurrent invalidate.

``get_all_resources_dict`` memoizes the repository's resources behind
``_all_resources_lock``.  It used to build the memo inside the lock and then
return ``self._all_resources`` from *outside* it -- a second, unsynchronised
read of the attribute -- while ``invalidate`` cleared that attribute taking no
lock at all.  An invalidation landing between the two made the method return
``None``, in violation of its declared ``dict[str, GenomicResource]`` return
type, and its callers crash on the ``None`` (#458).
"""
import pathlib
import threading
from types import TracebackType
from typing import Any

import pytest
from gain.genomic_resources.cli import _create_contents_db
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

from .conftest import RunInThreads

RESOURCE_IDS = ("one", "two", "three")


def _setup_repo(root_path: pathlib.Path) -> None:
    """Lay out a small repository and give it manifests, contents and index."""
    setup_directories(root_path, {
        resource_id: {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "alabala",
        }
        for resource_id in RESOURCE_IDS
    })
    # Repairs the repository: writes every manifest and the ``.CONTENTS``
    # a read-only protocol needs in order to load anything at all.
    rw_proto = build_filesystem_test_protocol(root_path)
    # ``search_resources`` answers from the FTS index, not from ``.CONTENTS``,
    # so a test that goes through it needs the index to exist.
    _create_contents_db(rw_proto)


@pytest.fixture
def read_only_proto(tmp_path: pathlib.Path) -> FsspecReadOnlyProtocol:
    root_path = tmp_path / "grr"
    _setup_repo(root_path)
    return build_filesystem_test_protocol(
        root_path, repair=False, read_only=True)


@pytest.fixture
def read_write_proto(tmp_path: pathlib.Path) -> FsspecReadWriteProtocol:
    root_path = tmp_path / "grr"
    _setup_repo(root_path)
    return build_filesystem_test_protocol(root_path, repair=False)


class InvalidateOnRelease:
    """Fire ``invalidate()`` in the instant the memo lock is released.

    The defect is a re-read of the memo *attribute* after the lock has been
    released, so reproducing it deterministically means placing an
    invalidation exactly in that instant -- after the ``with`` block ends and
    before the ``return`` reads the attribute again.  No public seam of the
    protocol runs there, which is why this substitutes the lock itself: a
    stress test can only make the window *likely*, and a test that is merely
    likely to fail is no guard at all.

    It fires once.  ``invalidate`` acquires this same lock once the defect is
    fixed, and a second firing would re-enter it.
    """

    def __init__(
        self, lock: threading.Lock, proto: FsspecReadOnlyProtocol,
    ) -> None:
        self._lock = lock
        self._proto = proto
        self.fired = False

    def __enter__(self) -> bool:
        return self._lock.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._lock.__exit__(exc_type, exc_value, traceback)
        if not self.fired:
            self.fired = True
            self._proto.invalidate()


def arm_invalidate_on_release(proto: Any) -> InvalidateOnRelease:
    """Make the protocol invalidate itself as it releases the memo lock."""
    trap = InvalidateOnRelease(proto._all_resources_lock, proto)
    proto._all_resources_lock = trap
    return trap


def test_get_all_resources_dict_survives_an_invalidate_at_lock_release(
    read_only_proto: FsspecReadOnlyProtocol,
) -> None:
    """The memo is returned as built, not re-read after the lock (#458)."""
    trap = arm_invalidate_on_release(read_only_proto)

    resources = read_only_proto.get_all_resources_dict()

    assert trap.fired
    assert resources is not None
    assert sorted(resources) == sorted(RESOURCE_IDS)


def test_read_write_get_all_resources_dict_survives_invalidate_at_release(
    read_write_proto: FsspecReadWriteProtocol,
) -> None:
    """The read-write override carries the same memo, and the same bug.

    ``FsspecReadWriteProtocol`` subclasses the read-only protocol and
    overrides ``get_all_resources_dict`` with its own copy of the
    release-then-read shape, so fixing only the base class leaves the class
    that ``grr_manage`` actually runs against broken (#458).
    """
    trap = arm_invalidate_on_release(read_write_proto)

    resources = read_write_proto.get_all_resources_dict()

    assert trap.fired
    assert resources is not None
    assert sorted(resources) == sorted(RESOURCE_IDS)


def test_search_resources_survives_an_invalidate_at_lock_release(
    read_only_proto: FsspecReadOnlyProtocol,
) -> None:
    """``search_resources`` is the memo's second, unreported crash site.

    It resolves every FTS index hit through ``get_all_resources_dict``, so a
    memo that came back ``None`` took the search endpoint down with
    ``'NoneType' object has no attribute 'get'`` -- a different traceback
    from the reported one, on a path no test covered (#458).
    """
    trap = arm_invalidate_on_release(read_only_proto)

    found = list(read_only_proto.search_resources(search_term="basic"))

    assert trap.fired
    assert sorted(res.resource_id for res in found) == sorted(RESOURCE_IDS)


@pytest.mark.parametrize("proto_fixture", [
    "read_only_proto",
    "read_write_proto",
])
def test_invalidate_waits_for_the_memo_lock(
    proto_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    """``invalidate`` must not clear the memo while a builder holds it.

    Returning the memo from inside the lock is only half of the guarantee:
    an ``invalidate`` that takes no lock at all can still clear the attribute
    in the middle of a populating read, so the two have to be mutually
    exclusive (#458).
    """
    proto = request.getfixturevalue(proto_fixture)
    proto.get_all_resources_dict()
    invalidated = threading.Event()

    def invalidate() -> None:
        proto.invalidate()
        invalidated.set()

    thread = threading.Thread(target=invalidate)
    with proto._all_resources_lock:
        thread.start()
        assert not invalidated.wait(0.2), \
            "invalidate() cleared the memo while the memo lock was held"
        assert proto._all_resources is not None

    thread.join(timeout=10.0)
    assert not thread.is_alive()
    assert invalidated.is_set()
    assert proto._all_resources is None


@pytest.mark.parametrize("proto_fixture", [
    "read_only_proto",
    "read_write_proto",
])
def test_readers_and_invalidators_contend_without_deadlocking(
    proto_fixture: str,
    request: pytest.FixtureRequest,
    run_in_threads: RunInThreads,
) -> None:
    """Real contention on the memo lock must not hang or lose resources.

    This guards the *fix*, not the defect.  ``invalidate`` now takes the lock
    that ``get_all_resources_dict`` holds, so a lock-ordering mistake here
    would hang rather than misbehave -- ``run_in_threads`` joins with a
    timeout and asserts liveness, which turns that into a failure instead of
    a wedged suite.

    It deliberately does NOT stand in for the deterministic tests above:
    measured against the unfixed code this passes, because 25 rounds is far
    too few to land in a window the original report needed a ten-second
    harness to hit.  A stress test that only *might* fail is not a guard.
    """
    proto = request.getfixturevalue(proto_fixture)

    def read_and_invalidate() -> int:
        seen = 0
        for _ in range(25):
            seen += len(list(proto.get_all_resources()))
            proto.invalidate()
        return seen

    counts, errors = run_in_threads(read_and_invalidate, 8)

    assert not errors
    # Every read sees the whole repository: a reader never observes a
    # half-built memo, and never observes a cleared one.
    assert counts == [25 * len(RESOURCE_IDS)] * 8
