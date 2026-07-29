"""The ids minted by the testing protocol builders must be cache-safe.

``GenomicResourceCachedRepo`` derives each repository's cache directory by
joining the repository id onto the cache url, so an id that is not a single
path segment decides where the process writes and is refused (#460). The
test builders used to name every protocol after its own absolute root, which
made every builder-minted repository unusable inside a cache -- and each
cache-backed fixture worked around it on its own (#488).
"""
import pathlib

import pytest
from gain.genomic_resources.cached_repository import (
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.fsspec_protocol import FsspecRepositoryProtocol
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    Mode,
    is_safe_repo_id,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
    build_http_test_protocol,
    build_inmemory_test_repository,
    proto_builder,
    setup_directories,
)


def test_default_filesystem_repository_can_be_wrapped_in_a_cache(
    tmp_path: pathlib.Path,
) -> None:
    """The case that raised ``ValueError`` on the first resolve before #488."""
    remote_path = tmp_path / "remote"
    setup_directories(remote_path, {"one": {GR_CONF_FILE_NAME: ""}})
    remote_repo = build_filesystem_test_repository(remote_path)

    cached_repo = GenomicResourceCachedRepo(
        remote_repo, str(tmp_path / "cache"))

    assert cached_repo.get_resource("one").resource_id == "one"
    assert is_safe_repo_id(remote_repo.proto.proto_id)


def test_default_inmemory_repository_can_be_wrapped_in_a_cache(
    tmp_path: pathlib.Path,
) -> None:
    """The in-memory builder mints its own temp root -- same trap, same fix."""
    remote_repo = build_inmemory_test_repository(
        {"one": {GR_CONF_FILE_NAME: ""}})

    cached_repo = GenomicResourceCachedRepo(
        remote_repo, str(tmp_path / "cache"))

    assert cached_repo.get_resource("one").resource_id == "one"
    assert is_safe_repo_id(remote_repo.proto.proto_id)


def test_roots_sharing_a_basename_get_distinct_ids(
    tmp_path: pathlib.Path,
) -> None:
    """Two same-named temp roots must not collide (#445 duplicate child id)."""
    ids = []
    for area in ("first", "second"):
        root_path = tmp_path / area / "grr"
        setup_directories(root_path, {"one": {GR_CONF_FILE_NAME: ""}})
        ids.append(build_filesystem_test_protocol(root_path).proto_id)

    assert ids[0] != ids[1]
    assert all(is_safe_repo_id(proto_id) for proto_id in ids)


def test_the_same_root_yields_the_same_protocol_instance(
    tmp_path: pathlib.Path,
) -> None:
    """The derived id is deterministic, so the ``__new__`` memo still hits."""
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})

    first = build_filesystem_test_protocol(tmp_path)
    second = build_filesystem_test_protocol(tmp_path)

    assert first is second


def test_every_scheme_builder_mints_a_cache_safe_id(
    fsspec_proto: FsspecRepositoryProtocol,
) -> None:
    """One assertion over the whole builder matrix.

    The fixture routes through ``build_filesystem_test_protocol``,
    ``build_inmemory_test_protocol``, ``s3_test_protocol`` and
    ``build_http_test_protocol`` -- the s3 and http legs only when their
    services are enabled, as in CI.
    """
    assert is_safe_repo_id(fsspec_proto.proto_id)


@pytest.mark.grr_full
def test_proto_builder_mints_a_cache_safe_id(grr_scheme: str) -> None:
    """``proto_builder`` reaches the two context-manager builders."""
    with proto_builder(grr_scheme, {"one": {GR_CONF_FILE_NAME: ""}}) as proto:
        assert is_safe_repo_id(proto.proto_id)


@pytest.mark.grr_http
def test_proto_builder_mints_a_cache_safe_id_over_http(
    grr_scheme: str,
) -> None:
    """``grr_full`` covers file+s3; http is parametrized by its own mark."""
    with proto_builder(grr_scheme, {"one": {GR_CONF_FILE_NAME: ""}}) as proto:
        assert is_safe_repo_id(proto.proto_id)


def test_http_protocol_id_is_cache_safe(tmp_path: pathlib.Path) -> None:
    """The http builder names its protocol too -- and had the same trap."""
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})

    with build_http_test_protocol(tmp_path) as proto:
        assert is_safe_repo_id(proto.proto_id)


def test_filesystem_protocol_can_be_built_read_only(
    tmp_path: pathlib.Path,
) -> None:
    """Read-only is the shape the ``.CONTENTS`` containment tests need."""
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})

    proto = build_filesystem_test_protocol(
        tmp_path, repair=False, read_only=True)

    assert proto.mode() == Mode.READONLY
    assert is_safe_repo_id(proto.proto_id)


def test_a_read_only_protocol_cannot_be_asked_to_repair(
    tmp_path: pathlib.Path,
) -> None:
    """Repair writes -- asking for both is refused, not half-honoured."""
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})

    with pytest.raises(ValueError) as excinfo:
        build_filesystem_test_protocol(tmp_path, read_only=True)

    assert "repair=False" in str(excinfo.value)


def test_a_read_only_build_is_not_shadowed_by_a_read_write_one(
    tmp_path: pathlib.Path,
) -> None:
    """A read-only build over a used root must still be read-only.

    The containment tests are security tests whose subject is a read-only
    remote; handing them the read-write protocol another build left in the
    ``(proto_id, url)`` memo would quietly retire what they check.
    """
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})
    read_write = build_filesystem_test_protocol(tmp_path)

    read_only = build_filesystem_test_protocol(
        tmp_path, repair=False, read_only=True)

    assert read_only.mode() == Mode.READONLY
    assert read_only is not read_write


def test_a_read_write_build_is_not_shadowed_by_a_read_only_one(
    tmp_path: pathlib.Path,
) -> None:
    """And the other order: the default build still repairs and writes."""
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})
    read_only = build_filesystem_test_protocol(
        tmp_path, repair=False, read_only=True)

    read_write = build_filesystem_test_protocol(tmp_path)

    assert read_write.mode() == Mode.READWRITE
    assert read_write is not read_only


def test_an_explicit_id_reused_in_the_other_mode_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """The escape hatch cannot silently hand back the wrong mode.

    An explicit id names one memoized instance whatever ``read_only`` says,
    so a mismatch has to be reported where it is asked for rather than as an
    absent write method somewhere downstream.
    """
    setup_directories(tmp_path, {"one": {GR_CONF_FILE_NAME: ""}})
    build_filesystem_test_protocol(tmp_path, proto_id="remote")

    with pytest.raises(ValueError) as excinfo:
        build_filesystem_test_protocol(
            tmp_path, proto_id="remote", repair=False, read_only=True)

    assert "remote" in str(excinfo.value)
