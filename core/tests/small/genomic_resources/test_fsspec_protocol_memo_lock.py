# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The fsspec protocols' resource memo must survive a concurrent invalidate.

``get_all_resources_dict`` memoizes the repository's resources behind
``_all_resources_lock``.  It used to build the memo inside the lock and then
return ``self._all_resources`` from *outside* it -- a second, unsynchronised
read of the attribute -- while ``invalidate`` cleared that attribute taking no
lock at all.  An invalidation landing between the two made the method return
``None``, in violation of its declared ``dict[str, GenomicResource]`` return
type, and its callers crash on the ``None`` (#458).

Locking only.  What the returned resources are still good *for* after an
invalidation is #513, in
``test_fsspec_protocol_invalidate_semantics.py``.
"""
import threading
from collections.abc import Callable, Generator
from types import TracebackType
from typing import Any

import pytest
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
)

# ``read_only_proto`` / ``read_write_proto`` come from the package conftest,
# over the same repository ``test_fsspec_protocol_invalidate_semantics.py``
# uses.
from .conftest import (
    SMALL_REPO_RESOURCE_IDS as RESOURCE_IDS,
)
from .conftest import (
    RunInThreads,
)


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


ArmInvalidateOnRelease = Callable[[Any], InvalidateOnRelease]


@pytest.fixture
def arm_invalidate_on_release() -> Generator[
    ArmInvalidateOnRelease, None, None,
]:
    """Arm a protocol's memo lock with the trap, and always disarm it.

    A fixture rather than a plain function because the protocol outlives the
    test: ``build_filesystem_test_protocol`` memoizes on ``(proto_id, url)``,
    so a trap left installed would hand a later test a lock that invalidates
    the repository as it is released.  Nothing depends on that today -- every
    test here gets a unique ``tmp_path``, hence a unique derived id -- but it
    is one explicit ``proto_id`` away from an unexplainable failure.
    """
    disarm: list[tuple[Any, Any]] = []

    def arm(proto: Any) -> InvalidateOnRelease:
        original = proto._all_resources_lock
        trap = InvalidateOnRelease(original, proto)
        disarm.append((proto, original))
        proto._all_resources_lock = trap
        return trap

    yield arm

    for proto, original in disarm:
        proto._all_resources_lock = original


def test_get_all_resources_dict_survives_an_invalidate_at_lock_release(
    read_only_proto: FsspecReadOnlyProtocol,
    arm_invalidate_on_release: ArmInvalidateOnRelease,
) -> None:
    """The memo is returned as built, not re-read after the lock (#458).

    Scope: this pins the *dict*, not the resources in it.  That the resources
    in it survive an invalidation is #513's separate concern about
    invalidation semantics -- ``invalidate`` used to unbind every resource's
    ``proto``, so this test passed with an intact dict full of unusable
    resources.  Guarded now by
    ``test_fsspec_protocol_invalidate_semantics.py``.
    """
    trap = arm_invalidate_on_release(read_only_proto)

    resources = read_only_proto.get_all_resources_dict()

    assert trap.fired
    assert resources is not None
    assert sorted(resources) == sorted(RESOURCE_IDS)


def test_read_write_get_all_resources_dict_survives_invalidate_at_release(
    read_write_proto: FsspecReadWriteProtocol,
    arm_invalidate_on_release: ArmInvalidateOnRelease,
) -> None:
    """The class ``grr_manage`` runs against carries the guarantee too.

    ``FsspecReadWriteProtocol`` used to override ``get_all_resources_dict``
    with its own copy of the release-then-read shape, so fixing only the base
    class left this -- the class ``grr_manage`` actually runs against --
    broken (#458).  It now inherits the memo protocol and contributes only its
    enumeration (#515), which is what makes the two cases here one
    guarantee rather than two; this exercises it through the subclass, since
    inheriting a fix is a claim worth checking rather than assuming.
    """
    trap = arm_invalidate_on_release(read_write_proto)

    resources = read_write_proto.get_all_resources_dict()

    assert trap.fired
    assert resources is not None
    assert sorted(resources) == sorted(RESOURCE_IDS)


def test_search_resources_survives_an_invalidate_at_lock_release(
    read_only_proto: FsspecReadOnlyProtocol,
    arm_invalidate_on_release: ArmInvalidateOnRelease,
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
    running = threading.Event()
    invalidated = threading.Event()

    def invalidate() -> None:
        running.set()
        proto.invalidate()
        invalidated.set()

    thread = threading.Thread(target=invalidate)
    with proto._all_resources_lock:
        thread.start()
        # Wait for the thread to actually be running before timing it.
        # Without this the assertion below passes on a loaded machine
        # whenever the thread simply was not scheduled inside the window --
        # a silent false negative rather than a flake, which is the worse
        # of the two.
        assert running.wait(10.0), "the invalidating thread never started"
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
