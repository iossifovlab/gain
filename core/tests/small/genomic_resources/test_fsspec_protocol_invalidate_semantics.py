# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``invalidate()`` must not break resources it has already handed out.

The fsspec protocols memoize their resources in ``_all_resources``.
``invalidate`` used to walk that memo and unbind every resource's protocol
(``resource.proto = None``) on the way to clearing it -- but the memo is
handed out by reference, so those are the very objects live callers are
holding.  A caller that legitimately obtained its resources *before* the
invalidation was left with objects that raise ``AttributeError: 'NoneType'
object has no attribute ...`` on first use (#513).

This is not the locking bug: #458 put both sides under the memo lock, which
closed the window where ``get_all_resources_dict`` returned ``None`` and left
this one -- the crash simply moved one step later, from "the dict is ``None``"
to "the resources in the dict are unusable".  It is a question of invalidation
*semantics*: ``invalidate`` invalidates the protocol's own cache, and a
resource already handed out belongs to its caller, not to the memo.
"""
import pathlib

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
    setup_directories,
)

# ``read_only_proto`` / ``read_write_proto`` come from the package conftest,
# over the same repository ``test_fsspec_protocol_memo_lock.py`` uses.
from .conftest import (
    SMALL_REPO_RESOURCE_IDS as RESOURCE_IDS,
)
from .conftest import (
    RunInThreads,
    setup_small_repo,
)


@pytest.fixture
def source_proto(tmp_path: pathlib.Path) -> FsspecReadWriteProtocol:
    """A second repository, to copy a resource *out of*.

    Its own root, so it gets its own protocol:
    ``build_filesystem_test_protocol`` memoizes on ``(proto_id, url)`` and
    would otherwise hand back the very protocol under test.
    """
    root_path = tmp_path / "source"
    setup_directories(root_path, {
        "four": {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "alabala",
        },
    })
    return build_filesystem_test_protocol(root_path)


def test_resource_handed_out_before_invalidate_is_still_usable(
    read_only_proto: FsspecReadOnlyProtocol,
) -> None:
    """The reported crash, reduced to two statements (#513).

    ``resources = proto.get_all_resources_dict()`` is a correct, fully
    synchronised read; the invalidation that follows is another thread's,
    equally correct.  Reading a file through a resource obtained by the
    first must not fail because of the second.
    """
    resource = read_only_proto.get_all_resources_dict()["one"]

    read_only_proto.invalidate()

    assert resource.get_file_content("data.txt") == "alabala"


def test_copy_resource_leaves_a_concurrent_readers_resources_usable(
    read_write_proto: FsspecReadWriteProtocol,
    source_proto: FsspecReadWriteProtocol,
) -> None:
    """The production reach: ``copy_resource`` invalidates mid-read (#513).

    ``ReadWriteRepositoryProtocol.copy_resource`` ends in
    ``self.invalidate()`` -- so a thread writing into a repository used to
    break the resources another thread had already collected from it.  Both
    halves are ordinary public API; neither is doing anything wrong.

    The write is on this thread here.  A held resource does not care *which*
    thread invalidated the memo, and a deterministic test beats one that has
    to win a race in order to mean anything.
    """
    held = list(read_write_proto.get_all_resources())
    assert sorted(res.resource_id for res in held) == sorted(RESOURCE_IDS)

    read_write_proto.copy_resource(source_proto.get_resource("four"))

    for resource in held:
        assert resource.get_file_content("data.txt") == "alabala"


def test_partially_consumed_get_all_resources_generator_stays_usable(
    read_only_proto: FsspecReadOnlyProtocol,
) -> None:
    """A generator suspended across an invalidation still yields live objects.

    ``get_all_resources`` is ``yield from get_all_resources_dict().values()``:
    it captures the memo and then yields lazily, so an invalidation can land
    between two of its yields.  This is the shape the reported
    ``cached_repository`` traceback takes -- ``CachingProtocol`` builds its
    cache twins by iterating exactly this generator, and reached
    ``_get_or_create_cache_proto(remote_resource.proto)`` with a ``proto``
    that had been unbound underneath it::

        cached_repository.py:498  proto_id = proto.proto_id
        AttributeError: 'NoneType' object has no attribute 'proto_id'

    Deterministic, and no threads: the suspended generator holds the old memo
    whichever thread the invalidation comes from (#513).
    """
    resources = read_only_proto.get_all_resources()
    first = next(resources)

    read_only_proto.invalidate()

    rest = list(resources)
    assert sorted(res.resource_id for res in [first, *rest]) \
        == sorted(RESOURCE_IDS)
    for resource in [first, *rest]:
        # ``.proto`` is what ``cached_repository`` reads off the resource,
        # and ``get_manifest`` is what it calls first; both used to be gone.
        assert resource.proto is read_only_proto
        assert resource.get_manifest() is not None


def test_cached_repo_invalidate_leaves_remote_resources_usable(
    tmp_path: pathlib.Path,
) -> None:
    """The two hops of the reported cached-stack traceback (#513).

    ``GenomicResourceCachedRepo.invalidate()`` reaches the unbinding through
    ``CachingProtocol.invalidate()`` -> ``remote_protocol.invalidate()``, so
    invalidating a *cache* used to break resources a caller was holding from
    the repository behind it -- the remote repo it wraps is public, shared,
    and its resources are exactly what ``_to_cache_resource`` reads
    ``.proto`` off of.

    Deterministic on purpose, and it covers a hole the contention test below
    cannot: that one goes through ``search_resources``, so this stays the
    only coverage of an invalidation reaching the remote through
    ``get_all_resources`` -- whose ``_memo_lock`` makes a *concurrent*
    reproduction impossible, and which therefore needs a second holder of
    the remote's resources, which is what this test is.
    """
    root_path = tmp_path / "grr"
    setup_small_repo(root_path)
    remote_repo = build_filesystem_test_repository(root_path)
    repo = GenomicResourceCachedRepo(remote_repo, str(tmp_path / "cache"))

    held_remote = list(remote_repo.get_all_resources())
    assert sorted(res.resource_id for res in held_remote) \
        == sorted(RESOURCE_IDS)
    # Populate the cache's own memo, so the invalidation below has both
    # levels to clear rather than only the remote one.
    assert len(list(repo.get_all_resources())) == len(RESOURCE_IDS)

    repo.invalidate()

    for resource in held_remote:
        assert resource.get_file_content("data.txt") == "alabala"


def test_cached_stack_searchers_and_invalidators_do_not_crash(
    tmp_path: pathlib.Path,
    run_in_threads: RunInThreads,
) -> None:
    """The reported traceback, under real contention (#513).

    ``search_resources`` is the reachable one.  ``GenomicResourceCachedRepo``
    holds ``_memo_lock`` across both ``get_all_resources`` and
    ``invalidate``, so going through *that* pair serialises reader and
    invalidator and cannot reach the window at all -- a contention test built
    on it passes with the bug reinstated, which is no guard.
    ``search_resources`` resolves every FTS hit through the same
    ``_to_cache_resource`` under **no lock**, and reads ``remote_resource
    .proto`` before ``_get_or_create_cache_proto`` takes any::

        cached_repository.py:410  yield self._to_cache_resource(...)
        cached_repository.py:383  self._get_or_create_cache_proto(
                                      remote_resource.proto)
        cached_repository.py:498  proto_id = proto.proto_id
        AttributeError: 'NoneType' object has no attribute 'proto_id'

    That is the issue's second traceback verbatim.  Reinstating the bug fails
    this in 7 of 8 threads; the fix takes it to zero errors.

    It is production, and it is the concurrent tier: the GRR resource-browse
    endpoint (``web_api/web_annotation/resources/views.py:116``) serves
    exactly this call under daphne.  ``run_in_threads`` also joins with a
    timeout and asserts liveness, so a lock-ordering mistake across the two
    hops fails loudly instead of wedging the suite.
    """
    root_path = tmp_path / "grr"
    setup_small_repo(root_path)
    repo = GenomicResourceCachedRepo(
        build_filesystem_test_repository(root_path),
        str(tmp_path / "cache"))

    def search_use_and_invalidate() -> int:
        used = 0
        for _ in range(25):
            for resource in repo.search_resources(search_term="basic"):
                # Using what was handed over is the whole point: a test that
                # only counts its resources passes with every one of them
                # unbound, which is how this defect survived #458's coverage.
                assert resource.get_file_content("data.txt") == "alabala"
                used += 1
            repo.invalidate()
        return used

    counts, errors = run_in_threads(search_use_and_invalidate, 8)

    assert not errors
    assert counts == [25 * len(RESOURCE_IDS)] * 8
