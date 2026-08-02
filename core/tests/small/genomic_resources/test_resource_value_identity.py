# pylint: disable=W0621,C0114,C0116
"""What makes two ``GenomicResource`` objects the same value.

A resource's value identity is *what it is* -- its id, its version and its
configuration.  Two things are deliberately excluded.

The manifest is a lazily-built cache.  Equality does not read it, so a
resource compares the same whether or not it is holding one, and
``invalidate`` never changes what a resource equals (#524).

The protocol is *where* the resource was reached, not what it is.  A
cache-backed resource and the remote twin it mirrors denote the same resource
and compare equal; identity, not equality, is what tells them apart -- see
``test_search_resources_yields_the_memoized_resource``.

``__hash__`` therefore hashes a strict subset of the fields ``__eq__``
compares, which is what makes ``a == b`` imply ``hash(a) == hash(b)``.
"""
import pathlib
from unittest import mock

import pytest
from gain.genomic_resources.cached_repository import CachingProtocol
from gain.genomic_resources.repository import (
    GenomicResource,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    setup_directories,
)

# ``resource`` comes from the package conftest, over the same single
# ``type: basic`` resource ``test_resource_manifest_memo.py`` uses.
from .conftest import (
    BASIC_RESOURCE_ID as RESOURCE_ID,
)
from .conftest import (
    BASIC_RESOURCE_LAYOUT as RESOURCE_LAYOUT,
)


def _same_resource_cold(resource: GenomicResource) -> GenomicResource:
    """Return a second object denoting the same resource, manifest unread.

    Passing no ``manifest`` is what makes it cold; there is no public way to
    *observe* that without warming it, since ``get_loaded_manifest`` falls
    back to loading one from the protocol.
    """
    return GenomicResource(
        resource.resource_id,
        resource.version,
        resource.proto,
        config=resource.config,
    )


@pytest.fixture
def twin_resources(
    tmp_path: pathlib.Path,
) -> tuple[GenomicResource, GenomicResource]:
    """The same resource reached through two protocols.

    What a group repository hands out when two of its children serve the same
    resource: equal values, different locations.
    """
    left_root = tmp_path / "grr-left"
    right_root = tmp_path / "grr-right"
    setup_directories(left_root, RESOURCE_LAYOUT)
    setup_directories(right_root, RESOURCE_LAYOUT)
    return (
        build_filesystem_test_protocol(left_root).get_resource(RESOURCE_ID),
        build_filesystem_test_protocol(right_root).get_resource(RESOURCE_ID),
    )


def test_equal_regardless_of_which_side_loaded_its_manifest(
    resource: GenomicResource,
) -> None:
    """Warming one side's manifest cache does not change what it equals."""
    cold = _same_resource_cold(resource)
    resource.get_manifest()

    assert resource == cold


def test_equal_resources_hash_equal(
    twin_resources: tuple[GenomicResource, GenomicResource],
) -> None:
    """The data-model invariant: ``a == b`` requires ``hash(a) == hash(b)``.

    Two resources reached through different protocols denote the same value,
    so they compare equal -- and must therefore hash alike, or no hash
    container can hold them correctly.
    """
    left, right = twin_resources

    assert left == right
    assert hash(left) == hash(right)


def test_invalidate_does_not_change_what_a_resource_equals(
    resource: GenomicResource,
) -> None:
    """Dropping a cached manifest is not a change of value."""
    cold = _same_resource_cold(resource)
    assert resource == cold

    resource.get_manifest()
    resource.invalidate()

    assert resource == cold


def test_resource_stays_retrievable_as_a_dict_key(
    resource: GenomicResource,
) -> None:
    """A resource put in a dict is found again, whatever its cache does.

    A lookup needs both legs: the hash finds the slot, equality confirms the
    key.  The manifest memo only ever reached the second, which is why the
    miss in #524 came with an unchanged hash.
    """
    lookup = _same_resource_cold(resource)
    resource.get_manifest()
    container = {resource: "value"}

    assert container.get(lookup) == "value"

    lookup.get_manifest()
    assert container.get(lookup) == "value"

    resource.invalidate()
    assert container.get(lookup) == "value"


def test_cached_resource_equals_the_remote_it_mirrors(
    tmp_path: pathlib.Path,
) -> None:
    """A cache-backed resource denotes the same resource as its remote twin."""
    remote_root = tmp_path / "remote"
    setup_directories(remote_root, RESOURCE_LAYOUT)
    remote_proto = build_filesystem_test_protocol(remote_root)
    caching_proto = CachingProtocol(
        remote_proto,
        build_filesystem_test_protocol(tmp_path / "cache"),
    )

    remote = remote_proto.get_resource(RESOURCE_ID)
    cached = caching_proto.get_resource(RESOURCE_ID)

    assert cached is not remote
    assert cached == remote
    assert hash(cached) == hash(remote)


def test_comparison_with_a_foreign_type_is_symmetric(
    resource: GenomicResource,
) -> None:
    """A resource defers to the other operand rather than refusing outright.

    Answering ``False`` to an unrecognised type suppresses the reflected
    comparison, so the answer would depend on which side of ``==`` the
    resource sat -- as it does for any test double that equals everything.
    """
    assert resource == mock.ANY
    # Deliberately reflected: that this direction agrees with the one above
    # is the whole property under test.
    assert mock.ANY == resource  # noqa: SIM300
    assert resource != "not a resource"


def test_resources_differing_in_config_are_not_equal(
    resource: GenomicResource,
) -> None:
    """Configuration is part of what a resource is."""
    other = GenomicResource(
        resource.resource_id,
        resource.version,
        resource.proto,
        config={"type": "something_else"},
    )

    assert resource != other


def test_two_versions_of_a_resource_are_not_the_same_value(
    resource: GenomicResource,
) -> None:
    """Version is part of what a resource is.

    Nothing else separates them now that neither dunder reads the protocol,
    so equality is the only thing keeping two versions of one resource apart.
    """
    older = GenomicResource(
        resource.resource_id, (1,), resource.proto, config=resource.config)
    newer = GenomicResource(
        resource.resource_id, (2,), resource.proto, config=resource.config)

    assert older != newer


def test_differently_named_resources_are_not_the_same_value(
    resource: GenomicResource,
) -> None:
    """The resource id is part of what a resource is."""
    other = GenomicResource(
        "another", resource.version, resource.proto, config=resource.config)

    assert resource != other
