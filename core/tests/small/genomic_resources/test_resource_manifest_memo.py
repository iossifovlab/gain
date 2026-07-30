# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A resource's manifest memo must survive a concurrent invalidate.

``GenomicResource`` memoizes its manifest on ``_manifest``, and ``invalidate``
clears that attribute holding no lock.  The accessors must therefore return the
manifest they obtained rather than a second read of the attribute: an
invalidation landing between the store and such a read makes ``get_manifest``
return ``None``, in violation of its declared ``-> Manifest`` (#519).

The two sides meet on one object.  ``save_manifest`` invalidates the resource
it is handed, and ``copy_resource``/``update_resource`` reach that resource
through ``get_or_create_resource`` -> ``find_resource``, which returns the
instance out of the protocol's memo.

``GenomicResource`` holds no lock and needs none: the memo is a benign race,
last writer wins, and two threads each building a manifest is acceptable.  What
these tests pin is narrower -- a caller never observes the ``None``, and an
invalidation that lands still wins.
"""
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    Manifest,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

RESOURCE_ID = "one"

RESOURCE_LAYOUT = {
    RESOURCE_ID: {
        GR_CONF_FILE_NAME: "type: basic\n",
        "data.txt": "alabala",
    },
}


@pytest.fixture
def resource(tmp_path: pathlib.Path) -> GenomicResource:
    """A resource whose manifest is written and loadable."""
    root_path = tmp_path / "grr"
    setup_directories(root_path, RESOURCE_LAYOUT)
    # Repairing the repository is what writes the manifest.
    proto = build_filesystem_test_protocol(root_path)
    return proto.get_resource(RESOURCE_ID)


@pytest.fixture
def unrepaired_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """A resource with no stored manifest, on a read-write protocol."""
    root_path = tmp_path / "grr-unrepaired"
    setup_directories(root_path, RESOURCE_LAYOUT)
    proto = build_filesystem_test_protocol(root_path, repair=False)
    return proto.get_resource(RESOURCE_ID)


class InvalidateOnManifestStore(GenomicResource):
    """Fire ``invalidate()`` in the instant the manifest memo is stored.

    The window these tests target lies between the memo store and the return,
    and nothing in the language runs there -- it is two adjacent bytecodes.
    So the trap substitutes the store itself, which is the one seam Python
    does offer, and lands the invalidation in the window deterministically.

    A stress test cannot stand in for it.  On a GIL build ``STORE_ATTR`` is
    followed directly by ``LOAD_FAST``/``LOAD_ATTR``/``RETURN_VALUE`` with no
    call and no backward jump between them, so the interpreter inserts no
    eval-breaker check and cannot preempt the thread there at all; the window
    opens under free-threading, which is the configuration this guards.
    Invalidating from *inside* the manifest build does not reach it either --
    that runs before the store, which then overwrites the cleared value.

    Fires once, so that the ``invalidate`` it triggers -- which assigns
    ``_manifest`` again -- does not recurse.
    """

    armed = False
    fired = False

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if (
            name == "_manifest"
            and value is not None
            and self.armed
            and not self.fired
        ):
            super().__setattr__("fired", True)
            self.invalidate()


def _arm(resource: GenomicResource) -> InvalidateOnManifestStore:
    """Return the same resource, armed to invalidate itself as it memoizes."""
    trapped = InvalidateOnManifestStore(
        resource.resource_id,
        resource.version,
        resource.proto,
        config=resource.config,
    )
    trapped.armed = True
    return trapped


@pytest.fixture
def trapped_resource(resource: GenomicResource) -> InvalidateOnManifestStore:
    return _arm(resource)


@pytest.fixture
def trapped_unrepaired_resource(
    unrepaired_resource: GenomicResource,
) -> InvalidateOnManifestStore:
    return _arm(unrepaired_resource)


def test_get_manifest_survives_an_invalidate_at_the_memo_store(
    trapped_resource: InvalidateOnManifestStore,
) -> None:
    """``get_manifest`` returns what it obtained, not a re-read of the memo."""
    manifest = trapped_resource.get_manifest()

    assert trapped_resource.fired
    assert manifest is not None, \
        "get_manifest() returned None, violating its -> Manifest annotation"
    assert isinstance(manifest, Manifest)
    assert "data.txt" in manifest


def test_get_manifest_survives_the_invalidate_while_building(
    trapped_unrepaired_resource: InvalidateOnManifestStore,
) -> None:
    """The building branch is held to the same contract as the loading one.

    With no manifest on disk, ``get_manifest`` falls through to building one
    -- an md5 scan of the resource that also writes ``.grr/*.state``.  That is
    the branch ``grr_manage repo-repair`` drives and the slower of the two, so
    it is the one a concurrent invalidation is likeliest to land in.
    """
    manifest = trapped_unrepaired_resource.get_manifest()

    assert trapped_unrepaired_resource.fired
    assert manifest is not None, \
        "get_manifest() returned None instead of the manifest it built"
    assert "data.txt" in manifest


def test_get_loaded_manifest_survives_an_invalidate_at_the_memo_store(
    trapped_resource: InvalidateOnManifestStore,
) -> None:
    """``get_loaded_manifest`` is held to the same contract.

    Its ``None`` carries meaning -- "no stored manifest" -- so a re-read
    caught by an invalidation does more than break an annotation: it makes a
    resource that has a manifest indistinguishable from one that does not,
    and the read paths that call this instead of :meth:`get_manifest` take
    the no-manifest branch.
    """
    manifest = trapped_resource.get_loaded_manifest()

    assert trapped_resource.fired
    assert manifest is not None, \
        "get_loaded_manifest() returned None for a resource that has one"
    assert isinstance(manifest, Manifest)
    assert "data.txt" in manifest


def test_get_loaded_manifest_still_reports_a_missing_manifest(
    unrepaired_resource: GenomicResource,
) -> None:
    """A resource with no stored manifest still reads back as ``None``.

    The counterpart to the tests above, and the reason the
    ``FileNotFoundError`` branch returns directly rather than through the
    local: ``get_loaded_manifest`` must keep answering ``None`` when the
    manifest genuinely is absent.  Making the memo race-proof by handing back
    a freshly built manifest here would satisfy them and break this.
    """
    assert unrepaired_resource.get_loaded_manifest() is None


@pytest.mark.parametrize("accessor", [
    "get_manifest",
    "get_loaded_manifest",
])
def test_the_invalidate_still_wins(
    trapped_resource: InvalidateOnManifestStore,
    accessor: str,
) -> None:
    """Surviving the invalidation must not mean undoing it.

    The accessor returns the manifest it obtained, and the memo it stored is
    gone again by the time it returns -- an invalidation that lands is not
    replayed away.  ``save_manifest`` invalidates precisely so the next read
    reloads the file it just wrote; a memo restored after the fact would keep
    a resource serving its pre-save manifest for the rest of the process,
    while every assertion about the returned value still held.
    """
    assert getattr(trapped_resource, accessor)() is not None

    assert trapped_resource.fired
    assert trapped_resource._manifest is None, \
        "the memo was restored after the invalidate, defeating it"
