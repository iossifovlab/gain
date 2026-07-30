# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""One memo protocol, one enumeration seam (#515).

``get_all_resources_dict`` memoizes the repository's resources behind
``_all_resources_lock``.  Both fsspec protocols used to carry a full copy of
that protocol -- the lock, the check-then-populate, the keying and the return
-- and differed only in how they enumerated the repository.  The duplication
cost something real: the release-then-read defect of #458 was in *both*
copies while the report named only the read-only one, so a fix that trusted
the report would have left the class ``grr_manage`` runs against broken.

These tests pin the consolidated shape from the outside: a protocol
contributes an enumeration and inherits the memo, the lock, the keying and
the ordering.  They are written against a subclass that overrides nothing but
the seam, because that is the contract a third protocol will lean on.
"""
import pathlib
from collections.abc import Iterable
from typing import cast

import fsspec
import pytest
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadOnlyProtocol,
    FsspecReadWriteProtocol,
)
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

from .conftest import setup_small_repo


class EnumerationOnlySeam:
    """Overrides the enumeration seam and nothing else.

    A mixin rather than a subclass so the same seam can be mixed into either
    protocol without touching construction -- what these tests are about is
    that contributing an enumeration is the *whole* of what a protocol has to
    do to get a memo.

    Deliberately yields its resources out of ``get_full_id()`` order: the
    ordering of the memo is the shared method's business, not the seam's.
    """

    ENUMERATED_IDS = ("beta", "alpha", "gamma")

    #: Counted so a test can tell a memoized read from a re-enumeration.
    #: A class attribute so the mixin needs no ``__init__``; the ``+=`` below
    #: rebinds it per instance on first use.
    enumerations = 0

    def _collect_all_resources(self) -> Iterable[GenomicResource]:
        self.enumerations += 1
        # A config is passed so each resource is complete without a
        # ``genomic_resource.yaml`` on disk: these exist to be enumerated,
        # and none of them is backed by a directory.
        build = cast("FsspecReadOnlyProtocol", self).build_genomic_resource
        return [
            build(resource_id, (0,), config={"type": "basic"})
            for resource_id in self.ENUMERATED_IDS
        ]


class EnumerationOnlyProtocol(EnumerationOnlySeam, FsspecReadOnlyProtocol):
    """A read-only protocol whose only contribution is its enumeration."""


class WritableEnumerationOnlyProtocol(
        EnumerationOnlySeam, FsspecReadWriteProtocol):
    """The same seam, in read-write mode.

    ``FsspecReadWriteProtocol`` is where the duplicated copy of the memo
    protocol lived, and it is the class ``grr_manage`` runs against -- so the
    seam has to reach it too, or the consolidation has fixed the one class
    that was never the problem.
    """


def build_seam_proto(
    protocol_class: type[FsspecReadOnlyProtocol],
    proto_id: str,
    root_path: pathlib.Path,
) -> FsspecReadOnlyProtocol:
    """Build a seam-only protocol over a real, repaired small repository.

    Over a *real* repository on purpose.  The inherited ``.CONTENTS``
    enumeration is fully functional there, so a ``get_all_resources_dict``
    that ignores the seam answers with the repository's own resources instead
    of failing to read anything -- which is what makes these tests pin *which*
    enumeration the memo is built from, rather than merely that one is
    reachable.

    The protocol id must be unique per test: ``_FSSPEC_PROTOCOLS`` memoizes
    one instance per ``(proto_id, url)`` for the life of the process and never
    evicts (#514).

    Returns the base type because ``__new__`` is annotated as returning it;
    each fixture narrows to the class it asked for.
    """
    setup_small_repo(root_path)
    return protocol_class(
        proto_id, str(root_path), filesystem=fsspec.filesystem("file"))


@pytest.fixture
def seam_proto(
    tmp_path: pathlib.Path, request: pytest.FixtureRequest,
) -> EnumerationOnlyProtocol:
    """A read-only seam-only protocol over a repaired small repository."""
    return cast("EnumerationOnlyProtocol", build_seam_proto(
        EnumerationOnlyProtocol,
        f"seam-{request.node.name}", tmp_path / "grr"))


@pytest.fixture
def writable_seam_proto(
    tmp_path: pathlib.Path, request: pytest.FixtureRequest,
) -> WritableEnumerationOnlyProtocol:
    """A read-write seam-only protocol over a repaired small repository."""
    return cast("WritableEnumerationOnlyProtocol", build_seam_proto(
        WritableEnumerationOnlyProtocol,
        f"seam-rw-{request.node.name}", tmp_path / "grr"))


def test_a_seam_only_protocol_serves_its_own_enumeration(
    seam_proto: EnumerationOnlyProtocol,
) -> None:
    """Overriding the enumeration is enough to define the repository."""
    assert sorted(seam_proto.get_all_resources_dict()) == [
        "alpha", "beta", "gamma",
    ]


def test_a_writable_seam_only_protocol_serves_its_own_enumeration(
    writable_seam_proto: WritableEnumerationOnlyProtocol,
) -> None:
    """The read-write protocol inherits the seam, it does not shadow it."""
    assert sorted(writable_seam_proto.get_all_resources_dict()) == [
        "alpha", "beta", "gamma",
    ]


@pytest.mark.parametrize("fixture_name", ["seam_proto", "writable_seam_proto"])
def test_the_seam_runs_once_however_often_the_memo_is_read(
    fixture_name: str, request: pytest.FixtureRequest,
) -> None:
    """Enumerating is what the memo exists to avoid repeating."""
    proto = request.getfixturevalue(fixture_name)

    proto.get_all_resources_dict()
    proto.get_all_resources_dict()
    list(proto.get_all_resources())

    assert proto.enumerations == 1


@pytest.mark.parametrize("fixture_name", ["seam_proto", "writable_seam_proto"])
def test_an_invalidated_memo_is_rebuilt_through_the_seam(
    fixture_name: str, request: pytest.FixtureRequest,
) -> None:
    """``invalidate`` drops the memo; the next read re-enumerates.

    This is the path ``grr_manage`` depends on to read back a repository it
    has just changed, and it now runs through the shared method for both
    protocols rather than through two copies of it.
    """
    proto = request.getfixturevalue(fixture_name)
    proto.get_all_resources_dict()

    proto.invalidate()

    assert sorted(proto.get_all_resources_dict()) == [
        "alpha", "beta", "gamma",
    ]
    assert proto.enumerations == 2


@pytest.mark.parametrize("fixture_name", ["seam_proto", "writable_seam_proto"])
def test_the_memo_is_ordered_by_full_id_whatever_the_seam_yields(
    fixture_name: str, request: pytest.FixtureRequest,
) -> None:
    """Ordering is the shared method's guarantee, not the enumeration's.

    ``EnumerationOnlySeam`` yields ``beta, alpha, gamma``.  Both protocols
    sorted their own dict before #515, so this pins the sort that moved into
    the shared method -- a seam that yields as it finds must not be able to
    cost the repository its ordering.
    """
    proto = request.getfixturevalue(fixture_name)

    assert list(proto.get_all_resources_dict()) == [
        "alpha", "beta", "gamma",
    ]
    assert [
        res.get_full_id() for res in proto.get_all_resources()
    ] == ["alpha", "beta", "gamma"]


def test_the_memo_protocol_is_implemented_exactly_once() -> None:
    """No fsspec protocol may carry a second copy of the memo protocol.

    A structural assertion, deliberately: the duplication is the defect this
    issue is about, and no behavioural test can catch its *return* -- a fresh
    copy of the memo protocol pasted into a subclass passes every test above
    on the day it is written, and then quietly ages out of step with this one,
    which is exactly what #458 met.  ``_FILESYSTEM_KWARGS``' drift guard
    (#514) is here for the same reason.
    """
    memo_holders = [
        protocol_class.__name__
        for protocol_class in (
            FsspecReadOnlyProtocol, FsspecReadWriteProtocol)
        if "get_all_resources_dict" in vars(protocol_class)
    ]
    assert memo_holders == ["FsspecReadOnlyProtocol"]

    # ...and every protocol contributes the part that genuinely differs.
    assert "_collect_all_resources" in vars(FsspecReadOnlyProtocol)
    assert "_collect_all_resources" in vars(FsspecReadWriteProtocol)


def test_the_two_protocols_keep_their_own_enumerations(
    tmp_path: pathlib.Path,
) -> None:
    """Sharing the memo must not merge the enumerations behind it.

    A writable protocol scans, a read-only one reads ``.CONTENTS``, and the
    difference is observable exactly here: a resource added to the directory
    without repairing the repository is visible to the scan and invisible to
    ``.CONTENTS``.  This is what a consolidation that unified the two
    enumerations -- rather than only the memo around them -- would break.
    """
    root_path = tmp_path / "grr"
    setup_small_repo(root_path)
    setup_directories(root_path, {
        "unrecorded": {
            GR_CONF_FILE_NAME: "type: basic\n",
            "data.txt": "alabala",
        },
    })

    # Two ids over one root, because a rebuild may not change mode (#514).
    read_write = build_filesystem_test_protocol(root_path, repair=False)
    read_only = build_filesystem_test_protocol(
        root_path, repair=False, read_only=True)

    assert "unrecorded" in read_write.get_all_resources_dict()
    assert "unrecorded" not in read_only.get_all_resources_dict()
