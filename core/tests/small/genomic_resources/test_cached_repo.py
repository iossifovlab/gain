# pylint: disable=W0621,C0114,C0116,W0212,W0613
import contextlib
import logging
import os
import pathlib
import textwrap
import threading
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager
from typing import Any, cast

import pytest
from gain.genomic_resources.cached_repository import (
    CachingProtocol,
    GenomicResourceCachedRepo,
    cache_resources,
)
from gain.genomic_resources.cli import (
    _create_contents_db,
    cli_manage,
)
from gain.genomic_resources.cli_list import run_list_command
from gain.genomic_resources.fsspec_protocol import (
    FsspecReadWriteProtocol,
    build_fsspec_protocol,
)
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.group_repository import GenomicResourceGroupRepo
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
    GenomicResourceProtocolRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_protocol,
    build_filesystem_test_repository,
    build_inmemory_test_repository,
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_vcf_info_score,
)
from pytest_mock import MockerFixture

from .conftest import RunInThreads


def test_create_definition_with_cache(tmp_path: pathlib.Path) -> None:
    repo = build_genomic_resource_repository(
        {
            "cache_dir": str(tmp_path / "cache"),
            "id": "bla",
            "type": "embedded",
            "content": {
                "one": {"genomic_resource.yaml": ""},
            },
        })
    assert isinstance(repo, GenomicResourceCachedRepo)

    res = repo.get_resource("one")
    assert res.resource_id == "one"


@pytest.mark.parametrize("bad_cache_dir", [
    "s3://test-bucket/grr-cache",
    "http://example.com/grr-cache",
    "memory://grr-cache",
    "file:///grr-cache",
    # A URL scheme needs only a ``:``: these two are URLs as well, and a
    # ``://`` check let them through -- ``s3:/bucket/...`` is a plausible
    # typo for ``s3://bucket/...``, and both used to end up as
    # ``file://s3:/bucket/...``, i.e. a local directory named ``s3:``.
    "s3:/bucket/grr-cache",
    "s3:bucket/grr-cache",
])
def test_definition_with_a_url_cache_dir_is_rejected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_cache_dir: str,
) -> None:
    """#473: ``cache_dir`` names a local directory, never a URL.

    A URL used to be interpolated into ``file://{cache_dir}``, which
    silently made a local directory literally named ``s3:`` instead of the
    remote cache the author asked for.
    """
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        build_genomic_resource_repository({
            "cache_dir": bad_cache_dir,
            "id": "bla",
            "type": "embedded",
            "content": {"one": {"genomic_resource.yaml": ""}},
        })

    message = str(excinfo.value)
    assert "cache_dir" in message
    assert "local filesystem" in message
    # Nothing at all is created, and in particular not the local directory
    # named after the scheme that this check exists to prevent.
    assert not (tmp_path / "s3:").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cache_dir", [
    "/data/grr-cache",
    # A ``:`` inside a path segment is not a scheme...
    "/data/c:d/grr-cache",
    # ...nor is a leading ``~`` or a relative path a URL...
    "~/grr_cache",
    "rel/path",
    # ...and ``//host/share`` parses with an empty scheme too.
    "//srv/share",
])
def test_definition_with_a_local_cache_dir_is_accepted(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_dir: str,
) -> None:
    """#473: only a URL scheme is refused -- local paths keep working.

    Building the repository creates no cache directory (the cache-side
    protocols are built lazily), so these are safe to name verbatim.
    """
    monkeypatch.chdir(tmp_path)

    repo = build_genomic_resource_repository({
        "cache_dir": cache_dir,
        "id": "bla",
        "type": "embedded",
        "content": {"one": {"genomic_resource.yaml": ""}},
    })

    assert isinstance(repo, GenomicResourceCachedRepo)
    assert list(tmp_path.iterdir()) == []


def test_directory_definition_with_a_url_cache_dir_creates_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """#473: a bad ``cache_dir`` is refused before any side effect.

    ``cache_dir`` used to be validated on the way out of the build, after
    the source repository had been constructed -- so a ``directory``
    repository with a refused ``cache_dir`` still created its root
    directory on disk before raising.
    """
    source_dir = tmp_path / "source"

    with pytest.raises(ValueError) as excinfo:
        build_genomic_resource_repository({
            "cache_dir": "s3://test-bucket/grr-cache",
            "id": "bla",
            "type": "directory",
            "directory": str(source_dir),
        })

    assert "cache_dir" in str(excinfo.value)
    assert not source_dir.exists()
    assert list(tmp_path.iterdir()) == []


def test_group_definition_with_a_url_cache_dir_is_rejected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#473: the same rule applies to a group's ``cache_dir``."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        build_genomic_resource_repository({
            "cache_dir": "s3://test-bucket/grr-cache",
            "id": "group",
            "type": "group",
            "children": [{
                "id": "bla",
                "type": "embedded",
                "content": {"one": {"genomic_resource.yaml": ""}},
            }],
        })

    message = str(excinfo.value)
    assert "cache_dir" in message
    assert "local filesystem" in message
    assert list(tmp_path.iterdir()) == []


def test_cached_repo_rejects_a_non_local_cache_url() -> None:
    """#473: reject a remote cache when the repository is built.

    The cache-side protocols are created lazily, one per child repository,
    so without this check a remote cache url is only noticed at the first
    resource access -- long after the configuration could be corrected.
    """
    remote_repo = build_inmemory_test_repository(
        {"one": {GR_CONF_FILE_NAME: ""}})

    with pytest.raises(ValueError) as excinfo:
        GenomicResourceCachedRepo(remote_repo, "s3://test-bucket/grr-cache")

    message = str(excinfo.value)
    assert "s3" in message
    assert "s3://test-bucket/grr-cache" in message
    assert "local filesystem" in message


def test_cached_repo_rejection_does_not_leak_cache_url_credentials() -> None:
    """#473: the rejection message must not carry a secret.

    A cache url can embed ``user:pass@`` userinfo, and this message lands in
    logs; both sibling messages (the caching protocol's and the repository
    factory's) already redact.
    """
    remote_repo = build_inmemory_test_repository(
        {"one": {GR_CONF_FILE_NAME: ""}})

    with pytest.raises(ValueError) as excinfo:
        GenomicResourceCachedRepo(
            remote_repo, "s3://AKIAEXAMPLE:sup3rs3cret@bucket/cache")

    message = str(excinfo.value)
    assert "sup3rs3cret" not in message
    assert "AKIAEXAMPLE" not in message
    assert "local filesystem" in message
    assert "s3://bucket/cache" in message


def test_cached_repo_accepts_a_bare_local_path_as_cache_url(
    tmp_path: pathlib.Path,
) -> None:
    """A cache url without a scheme is a local directory and stays valid."""
    remote_repo = build_inmemory_test_repository(
        {"one": {GR_CONF_FILE_NAME: ""}})

    repo = GenomicResourceCachedRepo(remote_repo, str(tmp_path / "cache"))

    assert repo.get_resource("one").resource_id == "one"


CacheRepositoryBuilder = Callable[
    [dict[str, Any]], AbstractContextManager[GenomicResourceCachedRepo]]


@pytest.fixture
def cache_repository(
    tmp_path: pathlib.Path,
) -> CacheRepositoryBuilder:
    # Not parametrized over the GRR schemes: a GRR cache must be on the
    # local filesystem (#473). The remote side is an in-memory repository,
    # so no remote-scheme coverage is lost here.

    @contextlib.contextmanager
    def builder(
        content: dict[str, Any],
    ) -> Generator[GenomicResourceCachedRepo, None, None]:
        remote_repo = build_inmemory_test_repository(content)
        yield GenomicResourceCachedRepo(
            remote_repo,
            f"file://{tmp_path}/cache_repo_testing.caching")

    return builder


def test_get_cached_resource(
        cache_repository: CacheRepositoryBuilder) -> None:

    with cache_repository(
            {"one": {"genomic_resource.yaml": ""}}) as cache_repo:

        res = cache_repo.get_resource("one")
        assert res.resource_id == "one"


def test_cached_repo_get_all_resources(
        cache_repository: CacheRepositoryBuilder) -> None:

    demo_gtf_content = "TP53\tchr3\t300\t200"
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
            },
            "sub": {
                "two-unstable(1.0)": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "genes.txt": demo_gtf_content,
                },
                "two(1.0)": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "genes.txt": demo_gtf_content,
                },
            }}) as cache_repo:

        assert len(list(cache_repo.get_all_resources())) == 3

        resource = cache_repo.get_resource("sub/two")
        assert resource is not None


def test_cached_resource_after_access(
        cache_repository: CacheRepositoryBuilder) -> None:

    demo_gtf_content = "TP53\tchr3\t300\t200"
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
            },
            "sub": {
                "two-unstable(1.0)": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "genes.txt": demo_gtf_content,
                },
                "two(1.0)": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "genes.txt": demo_gtf_content,
                },
            }}) as cache_repo:

        src_gr = cache_repo.child.get_resource("sub/two")
        cache_gr = cache_repo.get_resource("sub/two")

        assert src_gr.get_manifest() == cache_gr.get_manifest()
        assert len(list(cache_repo.get_all_resources())) == 3
        cache_proto = cache_gr.proto

        filesystem = cast(CachingProtocol, cache_proto)\
            .local_protocol.filesystem
        base_url = cast(CachingProtocol, cache_proto).local_protocol.url

        assert not filesystem.exists(
            os.path.join(base_url, "one", "data.txt"))
        assert not filesystem.exists(
            os.path.join(base_url, "sub/two-unstable(1.0)", "genes.txt"))
        assert not filesystem.exists(
            os.path.join(base_url, "sub/two(1.0)", "genes.txt"))


def test_cache_all(
        cache_repository: CacheRepositoryBuilder) -> None:

    demo_gtf_content = "TP53\tchr3\t300\t200"
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "type: gene_models\nfilename: genes.gtf",
                "genes.gtf": demo_gtf_content,
            },
            "sub": {
                "two-unstable(1.0)": {
                    GR_CONF_FILE_NAME:
                    "type: gene_models\nfilename: genes.gtf",
                    "genes.gtf": demo_gtf_content,
                },
                "two(1.0)": {
                    GR_CONF_FILE_NAME:
                    "type: gene_models\nfilename: genes.gtf",
                    "genes.gtf": demo_gtf_content,
                },
            }}) as cache_repo:

        cache_resources(cache_repo, None, workers=1)

        resource = cache_repo.get_resource("one")
        cache_proto = resource.proto
        filesystem = cast(CachingProtocol, cache_proto)\
            .local_protocol.filesystem
        base_url = cast(CachingProtocol, cache_proto).local_protocol.url

        assert filesystem.exists(
            os.path.join(base_url, "one", "genes.gtf"))
        assert filesystem.exists(
            os.path.join(base_url, "sub/two-unstable(1.0)", "genes.gtf"))
        assert filesystem.exists(
            os.path.join(base_url, "sub/two(1.0)", "genes.gtf"))


def test_cached_repository_resource_update_delete(
        cache_repository: CacheRepositoryBuilder) -> None:

    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
                "alabala.txt": "alabala",
            }}) as cache_repo:

        src_repo = cast(GenomicResourceProtocolRepo, cache_repo.child)

        gr1 = src_repo.get_resource("one")

        gr2 = cache_repo.get_resource("one")

        assert gr1.get_manifest() == gr2.get_manifest()

        with gr2.open_raw_file("alabala.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        cast(FsspecReadWriteProtocol, src_repo.proto)\
            .delete_resource_file(gr1, "alabala.txt")

        manifest = cast(FsspecReadWriteProtocol, src_repo.proto)\
            .build_manifest(gr1)
        cast(FsspecReadWriteProtocol, src_repo.proto)\
            .save_manifest(gr1, manifest)

        gr2 = cache_repo.get_resource("one")

        assert not gr2.file_exists("alabala.txt")


def test_cached_repository_file_level_cache(
        cache_repository: CacheRepositoryBuilder) -> None:

    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "config",
                "data.txt": "data",
                "alabala.txt": "alabala",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        assert resource is not None

        cache_proto = cache_repo.get_resource("one").proto
        filesystem = cast(CachingProtocol, cache_proto)\
            .local_protocol.filesystem
        base_url = cast(CachingProtocol, cache_proto)\
            .local_protocol.url

        assert not filesystem.exists(
            os.path.join(base_url, "one", GR_CONF_FILE_NAME))
        assert not filesystem.exists(
            os.path.join(base_url, "one", "data.txt"))
        assert not filesystem.exists(
            os.path.join(base_url, "one", "alabala.txt"))

        with resource.open_raw_file("alabala.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        assert not filesystem.exists(
            os.path.join(base_url, "one", "data.txt"))
        assert filesystem.exists(
            os.path.join(base_url, "one", "alabala.txt"))


def test_filesystem_lock_implementation(
    cache_repository: CacheRepositoryBuilder,
) -> None:
    # The ``[s3]`` case that used to xfail here is gone: a cache must now be
    # on the local filesystem (#473), so there is no scheme left for which
    # the lock is a no-op.
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "config",
                "data.txt": "data",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        assert resource is not None

        cache_proto = cast(CachingProtocol, resource.proto)
        lock1 = cache_proto.local_protocol.obtain_resource_file_lock(
            resource, "data.txt")
        with lock1:
            lock2 = cache_proto.local_protocol.obtain_resource_file_lock(
                resource, "data.txt", timeout=0.1)
            with pytest.raises(TimeoutError), lock2:
                pass


def test_filesystem_caching_lock_implementation(
    mocker: MockerFixture,
    cache_repository: CacheRepositoryBuilder,
) -> None:
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "config",
                "data.txt": "data",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        assert resource is not None

        obtain_lock_spy = mocker.spy(
            FsspecReadWriteProtocol, "obtain_resource_file_lock",
        )

        with resource.open_raw_file("data.txt"):
            obtain_lock_spy.assert_called_once()


def test_cached_repository_locks_file_when_caching(
        cache_repository: CacheRepositoryBuilder) -> None:
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "config",
                "data.txt": "data",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        assert resource is not None

        finished = threading.Barrier(2)
        resource_locked = threading.Barrier(2)

        orig = cast(CachingProtocol, resource.proto)\
            .local_protocol.update_resource_file

        times_called = 0

        def blocking_wrapper(*args: Any) -> None:
            nonlocal times_called
            orig(*args)
            times_called += 1
            resource_locked.wait()
            finished.wait()

        cast(CachingProtocol, resource.proto) \
            .local_protocol\
            .update_resource_file = blocking_wrapper  # type: ignore

        x = threading.Thread(target=resource.open_raw_file,
                             args=("data.txt",))
        y = threading.Thread(target=resource.open_raw_file,
                             args=("data.txt",))
        y.start()
        x.start()

        for i in (1, 2):
            resource_locked.wait()
            assert times_called == i
            # make sure the next thread calls blocking_wrapper only
            # AFTER the assertion for times_called has happened
            finished.wait()

        x.join()
        y.join()


def test_get_resource_cached_files(
        cache_repository: CacheRepositoryBuilder) -> None:
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data1.txt": "alabala",
                "data2.txt": "alabala",
                "data3.txt": "alabala",
            }}) as cache_repo:
        cache_gr = cache_repo.get_resource("one")

        cache_proto = cache_gr.proto

        filesystem = cast(CachingProtocol, cache_proto)\
            .local_protocol.filesystem
        base_url = cast(CachingProtocol, cache_proto)\
            .local_protocol.url

        assert not filesystem.exists(
            os.path.join(base_url, "one", "data1.txt"))
        assert cache_repo.get_resource_cached_files("one") == set()

        with cache_gr.open_raw_file("data1.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        assert filesystem.exists(
            os.path.join(base_url, "one", "data1.txt"))
        assert cache_repo.get_resource_cached_files("one") == {"data1.txt"}

        with cache_gr.open_raw_file("data2.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        assert filesystem.exists(
            os.path.join(base_url, "one", "data2.txt"))
        assert cache_repo.get_resource_cached_files("one") == {
            "data1.txt", "data2.txt",
        }


def test_cached_repo_list_cli(
        cache_repository: CacheRepositoryBuilder,
        capsys: pytest.CaptureFixture) -> None:
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "genomic_resource.yaml": "",
                "data1.txt": "alabala",
                "data2.txt": "alabala"}}) as cache_repo:
        cache_repo._repo_id = "test_grr"

        res = cache_repo.get_resource("one")
        assert res.resource_id == "one"

        with res.open_raw_file("data1.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        run_list_command(cache_repo, [])  # type: ignore
        out, err = capsys.readouterr()
        print(out)
        assert err == ""
        assert out == \
            "basic                0        1/ 3 14.0 B       test_grr one\n"


def test_cached_repo_nested_list_cli(
        cache_repository: CacheRepositoryBuilder,
        capsys: pytest.CaptureFixture) -> None:
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
            },
            "sub": {
                "two": {
                    GR_CONF_FILE_NAME: "type: gene_models\nfile: genes.gtf",
                    "data2.txt": "alabala2",
                },
            }}) as cache_repo:
        cache_repo._repo_id = "test_grr"

        res = cache_repo.get_resource("sub/two")
        assert res.resource_id == "sub/two"

        with res.open_raw_file("data2.txt") as infile:
            content = infile.read()
            assert content == "alabala2"

        res = cache_repo.get_resource("one")
        assert res.resource_id == "one"

        with res.open_raw_file("data.txt") as infile:
            content = infile.read()
            assert content == "alabala"

        run_list_command(cache_repo, [])  # type: ignore
        out, err = capsys.readouterr()
        print(out)
        assert err == ""
        assert out == (
            "basic                0        1/ 2 7.0 B        test_grr one\n"
            "gene_models          0        1/ 2 41.0 B       test_grr "
            "sub/two\n"
        )


def test_cached_repo_invalidate(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test that invalidate() clears cached resources."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "alabala",
            }}) as cache_repo:

        # Access resource to populate cache
        result = list(cache_repo.get_all_resources())
        assert len(result) == 1

        # Cache should be populated
        assert cache_repo._all_resources is not None

        # Invalidate cache
        cache_repo.invalidate()

        # Cache should be cleared
        assert cache_repo._all_resources is None


def test_cache_resource_wrapper(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test CacheResource wraps remote resource correctly."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "type: gene_models",
                "data.txt": "test data",
            }}) as cache_repo:

        cache_res = cache_repo.get_resource("one")
        remote_res = cache_repo.child.get_resource("one")

        # CacheResource should have same resource_id and version
        assert cache_res.resource_id == remote_res.resource_id
        assert cache_res.version == remote_res.version
        assert cache_res.config == remote_res.config
        assert cache_res.get_manifest() == remote_res.get_manifest()


def test_caching_protocol_public_url(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test CachingProtocol uses correct public URL."""
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: ""}}) as cache_repo:

        resource = cache_repo.get_resource("one")
        cache_proto = cast(CachingProtocol, resource.proto)

        # Public URL should be from remote protocol
        assert cache_proto.get_public_url() is not None
        assert cache_proto.get_url() == \
            cache_proto.remote_protocol.get_url()


def test_caching_protocol_invalidate(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test CachingProtocol invalidate() clears both protocols."""
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: ""}}) as cache_repo:

        resource = cache_repo.get_resource("one")
        cache_proto = cast(CachingProtocol, resource.proto)

        # Populate cache
        list(cache_proto.get_all_resources())
        assert cache_proto._all_resources is not None

        # Invalidate
        cache_proto.invalidate()

        # Cache should be cleared
        assert cache_proto._all_resources is None


def test_find_resource_with_version_constraint(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test find_resource with version constraints."""
    with cache_repository({
            "one(1.0)": {GR_CONF_FILE_NAME: ""},
            "one(2.0)": {GR_CONF_FILE_NAME: ""},
            "one(3.0)": {GR_CONF_FILE_NAME: ""},
        }) as cache_repo:

        # Find latest version
        res = cache_repo.find_resource("one")
        assert res is not None
        assert res.version == (3, 0)

        # Find with version constraint
        res = cache_repo.find_resource("one", ">=2.0")
        assert res is not None
        assert res.version == (3, 0)

        res = cache_repo.find_resource("one", "=1.0")
        assert res is not None
        assert res.version == (1, 0)


def test_find_resource_nonexistent(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test find_resource returns None for nonexistent resources."""
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: ""}}) as cache_repo:

        res = cache_repo.find_resource("nonexistent")
        assert res is None


def test_cached_repo_get_resource_url(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test getting resource and file URLs through cache."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "content",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        cache_proto = cast(CachingProtocol, resource.proto)

        # Get resource URL
        resource_url = cache_proto.get_resource_url(resource)
        assert resource_url is not None

        # Get file URL (should trigger caching)
        file_url = cache_proto.get_resource_file_url(resource, "data.txt")
        assert file_url is not None
        assert "data.txt" in file_url


def test_caching_protocol_readonly(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test that CachingProtocol is read-only."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "content",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        cache_proto = cast(CachingProtocol, resource.proto)

        # Attempting to open file for writing should fail
        with pytest.raises(OSError, match="Read-Only"):
            cache_proto.open_raw_file(resource, "data.txt", mode="wt")


def test_cache_resources_with_specific_ids(
    cache_repository: CacheRepositoryBuilder,
) -> None:
    """Test cache_resources with specific resource IDs."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data1.txt": "content1",
            },
            "two": {
                GR_CONF_FILE_NAME: "",
                "data2.txt": "content2",
            }}) as cache_repo:

        # Cache only "one"
        cache_resources(cache_repo, ["one"], workers=1)

        # Verify "one" is cached
        assert cache_repo.get_resource_cached_files("one") == {"data1.txt"}

        # Verify "two" is not cached
        assert cache_repo.get_resource_cached_files("two") == set()


def test_empty_resource_caching(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test caching of empty resources (no data files)."""
    with cache_repository({
            "empty": {GR_CONF_FILE_NAME: ""}}) as cache_repo:

        resource = cache_repo.get_resource("empty")
        assert resource.resource_id == "empty"

        # No files to cache besides config
        cached_files = cache_repo.get_resource_cached_files("empty")
        assert cached_files == set()


def test_caching_protocol_file_exists(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test file_exists through caching protocol."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "exists.txt": "content",
            }}) as cache_repo:

        resource = cache_repo.get_resource("one")
        cache_proto = cast(CachingProtocol, resource.proto)

        # File should exist (triggers caching)
        assert cache_proto.file_exists(resource, "exists.txt")

        # Nonexistent file should not exist
        assert not cache_proto.file_exists(resource, "nonexistent.txt")


def test_concurrent_resource_access(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test concurrent access to same resource from multiple threads."""
    # The ``[s3]`` case that used to xfail here is gone with #473: the cache
    # is always local now, so the per-file lock this races against is always
    # a real ``FileLock``.
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data.txt": "test content",
            }}) as cache_repo:

        results: list[str] = []
        errors: list[BaseException] = []

        def read_resource() -> None:
            # pylint: disable=broad-exception-caught
            try:
                resource = cache_repo.get_resource("one")
                with resource.open_raw_file("data.txt") as f:
                    content = f.read()
                    results.append(content)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=read_resource) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # All threads should succeed
        assert len(errors) == 0
        assert len(results) == 5
        assert all(r == "test content" for r in results)


def test_cache_resources_continues_after_failure_and_raises(
        cache_repository: CacheRepositoryBuilder,
        mocker: MockerFixture) -> None:
    # One resource that always fails must not abort the whole run: every
    # other resource is still cached, and the run raises a summary naming the
    # failure so CI goes red. See gain#43.
    with cache_repository({
            "good1": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            "bad": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            "good2": {GR_CONF_FILE_NAME: "", "data.txt": "c"},
            }) as cache_repo:

        real_download = CachingProtocol.download_cached_resource_file

        def flaky_download(
                self: CachingProtocol,
                resource: Any, filename: str,
                *, on_bytes: Any = None) -> tuple[str, str]:
            if resource.resource_id == "bad":
                raise OSError("simulated permanent download failure")
            return real_download(self, resource, filename, on_bytes=on_bytes)

        mocker.patch.object(
            CachingProtocol, "download_cached_resource_file",
            autospec=True, side_effect=flaky_download)

        with pytest.raises(RuntimeError, match="bad"):
            cache_resources(cache_repo, None, workers=1)

        # The healthy resources were cached despite "bad" failing.
        assert cache_repo.get_resource_cached_files("good1") == {"data.txt"}
        assert cache_repo.get_resource_cached_files("good2") == {"data.txt"}


def test_cache_resources_progress_reports_failures(
        cache_repository: CacheRepositoryBuilder,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture) -> None:
    # The byte-mode milestone progress lines (used off a TTY) carry a
    # running failed tally, so a captured CI log shows trouble accumulating
    # without waiting for the end-of-run summary. The tally is sampled at
    # byte-percentage crossings, so the exact intermediate count is not
    # asserted -- only that a failed=N tally surfaces and the final line
    # reflects the total. See gain#59 / gain#79.
    caplog.set_level(
        logging.INFO, logger="gain.genomic_resources.cached_repository")
    with cache_repository({
            "good1": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            "bad": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            "good2": {GR_CONF_FILE_NAME: "", "data.txt": "c"},
            }) as cache_repo:

        real_download = CachingProtocol.download_cached_resource_file

        def flaky_download(
                self: CachingProtocol,
                resource: Any, filename: str,
                *, on_bytes: Any = None) -> tuple[str, str]:
            if resource.resource_id == "bad":
                raise OSError("simulated permanent download failure")
            return real_download(self, resource, filename, on_bytes=on_bytes)

        mocker.patch.object(
            CachingProtocol, "download_cached_resource_file",
            autospec=True, side_effect=flaky_download)

        with pytest.raises(RuntimeError, match="bad"):
            cache_resources(cache_repo, None, workers=1)

    progress_lines = [
        rec.message for rec in caplog.records
        if "caching progress" in rec.message
    ]
    assert progress_lines
    # A failed=N tally surfaces on a milestone line.
    assert any("failed=" in line for line in progress_lines)
    # "bad" contributes two failed files; the final line reflects the total.
    assert "failed=2" in progress_lines[-1]


def test_cache_resources_terminal_failure_byte_bar_reaches_100(
        cache_repository: CacheRepositoryBuilder,
        mocker: MockerFixture,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch) -> None:
    # A download that always fails still drives the byte bar to 100% (via the
    # terminal-failure top-up) and the run raises the summary with the failed
    # tally. See gain#79 / gain#43.
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    caplog.set_level(
        logging.INFO, logger="gain.genomic_resources.cached_repository")
    with cache_repository({
            "bad": {GR_CONF_FILE_NAME: "", "data.txt": "payload-bytes"},
            }) as cache_repo:

        def always_fail(
                self: CachingProtocol,
                resource: Any, filename: str,
                *, on_bytes: Any = None) -> tuple[str, str]:
            # Mimic slice-1 rollback: credit then roll back this attempt's
            # bytes, then hard-fail, so the top-up is what reaches 100%.
            if on_bytes is not None:
                on_bytes(5)
                on_bytes(-5)
            raise OSError("simulated permanent download failure")

        mocker.patch.object(
            CachingProtocol, "download_cached_resource_file",
            autospec=True, side_effect=always_fail)

        with pytest.raises(RuntimeError, match="failed to cache"):
            cache_resources(cache_repo, None, workers=1)

    progress_lines = [
        rec.message for rec in caplog.records
        if "caching progress" in rec.message
    ]
    assert progress_lines
    assert any("(100%)" in line for line in progress_lines)
    assert "failed=" in progress_lines[-1]


def test_cache_resources_continues_after_classify_failure_and_raises(
        cache_repository: CacheRepositoryBuilder,
        mocker: MockerFixture) -> None:
    # A failure in the lock-free classify pre-pass must not abort the whole
    # run: other resources are still classified and downloaded, and the run
    # raises a summary naming the failure. Preserves the gain#43 "one file
    # failing must not discard the run" contract across the two-phase
    # refactor (gain#78).
    with cache_repository({
            "good1": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            "bad": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            "good2": {GR_CONF_FILE_NAME: "", "data.txt": "c"},
            }) as cache_repo:

        real_classify = CachingProtocol.classify_cached_resource_file

        def flaky_classify(
                self: CachingProtocol,
                resource: Any, filename: str) -> Any:
            if resource.resource_id == "bad":
                raise OSError("simulated classify failure")
            return real_classify(self, resource, filename)

        mocker.patch.object(
            CachingProtocol, "classify_cached_resource_file",
            autospec=True, side_effect=flaky_classify)

        with pytest.raises(RuntimeError, match="bad"):
            cache_resources(cache_repo, None, workers=1)

        # The healthy resources were still classified and cached despite
        # "bad" failing classification.
        assert cache_repo.get_resource_cached_files("good1") == {"data.txt"}
        assert cache_repo.get_resource_cached_files("good2") == {"data.txt"}


def test_cache_resources_raises_when_all_classification_fails(
        cache_repository: CacheRepositoryBuilder,
        mocker: MockerFixture) -> None:
    # When every file fails classification the work-list is empty and the
    # download phase is skipped entirely -- but the run must still raise the
    # classify-failure summary rather than silently returning. Covers the
    # empty-work-list early-return raise branch. See gain#43 / gain#79.
    with cache_repository({
            "bad": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            }) as cache_repo:

        def always_fail(
                _self: CachingProtocol,
                _resource: Any, _filename: str) -> Any:
            raise OSError("simulated classify failure")

        mocker.patch.object(
            CachingProtocol, "classify_cached_resource_file",
            autospec=True, side_effect=always_fail)

        with pytest.raises(RuntimeError, match="bad"):
            cache_resources(cache_repo, None, workers=1)

        # Nothing was cached -- every file failed classification.
        assert cache_repo.get_resource_cached_files("bad") == set()


def test_cache_resources_parallel_workers(
        cache_repository: CacheRepositoryBuilder) -> None:
    """Test cache_resources with parallel workers."""
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "",
                "data1.txt": "content1",
                "data2.txt": "content2",
                "data3.txt": "content3",
            }}) as cache_repo:

        # Cache with multiple workers
        cache_resources(cache_repo, ["one"], workers=3)

        # All files should be cached
        cached = cache_repo.get_resource_cached_files("one")
        assert "data1.txt" in cached
        assert "data2.txt" in cached
        assert "data3.txt" in cached


@pytest.mark.parametrize(
    "resource_id_version,expected_version", [
        ("one(1.0)", (1, 0)),
        ("one(1.1)", (1, 1)),
        ("one(0)", (0,)),
    ],
)
def test_cache_find_resource_with_version(
    cache_repository: CacheRepositoryBuilder,
    resource_id_version: str,
    expected_version: tuple[int, ...],
) -> None:

    demo_gtf_content = "TP53\tchr3\t300\t200"
    with cache_repository({
            "one": {
                GR_CONF_FILE_NAME: "type: gene_models\nfilename: genes.gtf",
                "genes.gtf": demo_gtf_content,
            },
            "one(1.0)": {
                GR_CONF_FILE_NAME: "type: gene_models\nfilename: genes.gtf",
                "genes.gtf": demo_gtf_content,
            },
            "one(1.1)": {
                GR_CONF_FILE_NAME: "type: gene_models\nfilename: genes.gtf",
                "genes.gtf": demo_gtf_content,
            }}) as cache_repo:

        cache_resources(cache_repo, None, workers=1)

        resource = cache_repo.get_resource(resource_id_version)
        assert resource.version == expected_version
        assert resource.resource_id == "one"

        cache_proto = resource.proto
        filesystem = cast(CachingProtocol, cache_proto)\
            .local_protocol.filesystem
        base_url = cast(CachingProtocol, cache_proto).local_protocol.url

        resource_path = resource_id_version
        if expected_version == (0,):
            resource_path = "one"

        assert filesystem.exists(
            os.path.join(base_url, resource_path, "genes.gtf"))


# ---------------------------------------------------------------------------
# Every resource a cached repository produces must be cache-backed (#428),
# and repository_id must actually narrow the search (#429).
# ---------------------------------------------------------------------------


def _setup_search_remote(root_path: pathlib.Path) -> None:
    """Lay out two searchable position scores under ``root_path``."""
    setup_directories(
        root_path,
        {
            "scores/res_a": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: Example position score A
                        labels:
                            domain: domain_a
                    table:
                        filename: data.txt
                    scores:
                        - id: score
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   100        1.5
                """),
            },
            "scores/res_b": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: position_score
                    meta:
                        description: Example position score B
                        labels:
                            domain: domain_b
                    table:
                        filename: data.txt
                    scores:
                        - id: score
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   200        0.7
                """),
            },
        },
    )


@pytest.fixture
def indexed_cache_repository(
    tmp_path: pathlib.Path,
) -> GenomicResourceCachedRepo:
    """Cached repo over a filesystem remote that carries an FTS index.

    Deliberately separate from ``cache_repository``: that fixture builds an
    in-memory remote with no ``.CONTENTS.sqlite3``, so a *filtered*
    search_resources() raises ValueError in the protocol
    (`fsspec_protocol.open_repository_sqlite3_metadata_db`) long before it
    reaches the code under test. ``_create_contents_db`` also works against
    real filesystem paths (``proto.root_path``), hence a filesystem remote
    rather than the in-memory one. Building the index into ``cache_repository``
    itself would tax the ~25 tests that consume it for a capability only these
    tests need.
    """
    remote_path = tmp_path / "remote"
    _setup_search_remote(remote_path)
    cli_manage(["repo-manifest", "-R", str(remote_path)])
    # ``repair=False``: ``repo-manifest`` above has already written the
    # manifests and the contents file.
    proto = build_filesystem_test_protocol(remote_path, repair=False)
    _create_contents_db(proto)
    return GenomicResourceCachedRepo(
        GenomicResourceProtocolRepo(proto),
        f"file://{tmp_path}/cache")


#: Every ``GenomicResourceCachedRepo`` method that hands out resources. The
#: table is the point: ``search_resources`` was the one member that forgot to
#: wrap (#428), and a new resource-producing method should be added here so
#: the invariant is enforced class-wide rather than per-method.
RESOURCE_PRODUCERS: list[
    tuple[str, Callable[[GenomicResourceCachedRepo], list[GenomicResource]]]
] = [
    (
        "get_all_resources",
        lambda repo: list(repo.get_all_resources()),
    ),
    (
        "get_resource",
        lambda repo: [repo.get_resource("scores/res_a")],
    ),
    (
        "find_resource",
        lambda repo: [
            cast(GenomicResource, repo.find_resource("scores/res_a")),
        ],
    ),
    (
        "search_resources_unfiltered",
        lambda repo: list(repo.search_resources()),
    ),
    (
        "search_resources_by_type",
        lambda repo: list(repo.search_resources(
            resource_type="position_score")),
    ),
    (
        "search_resources_by_term",
        lambda repo: list(repo.search_resources(search_term="domain_a")),
    ),
]


@pytest.mark.parametrize(
    "label,produce", RESOURCE_PRODUCERS,
    ids=[label for label, _ in RESOURCE_PRODUCERS],
)
def test_produced_resources_are_cache_backed(
    indexed_cache_repository: GenomicResourceCachedRepo,
    label: str,
    produce: Callable[[GenomicResourceCachedRepo], list[GenomicResource]],
) -> None:
    resources = produce(indexed_cache_repository)

    assert resources, label
    for res in resources:
        assert isinstance(res.proto, CachingProtocol), \
            f"{label} yielded a non-cache-backed {res.resource_id}"


def test_search_resources_yields_the_memoized_resource(
    indexed_cache_repository: GenomicResourceCachedRepo,
) -> None:
    """A search hit is the same object get_all_resources() hands out.

    ``GenomicResource.__eq__`` ignores ``proto`` and ``__hash__`` uses
    ``proto.get_url()``, which the caching protocol forwards to the remote --
    so a cache-backed resource and its remote twin compare equal. Identity is
    what actually distinguishes them.
    """
    by_id = {
        res.resource_id: res
        for res in indexed_cache_repository.get_all_resources()
    }

    searched = list(
        indexed_cache_repository.search_resources(search_term="domain_a"))

    assert len(searched) == 1
    assert searched[0] is by_id["scores/res_a"]


def test_reading_a_search_hit_populates_the_cache(
    indexed_cache_repository: GenomicResourceCachedRepo,
) -> None:
    """The behaviour #428 is actually about: search hits go through the cache.

    Before the fix the hit carried the remote protocol, so this read went
    straight to the remote and the cache directory stayed empty.
    """
    searched = list(
        indexed_cache_repository.search_resources(search_term="domain_a"))
    assert len(searched) == 1
    resource = searched[0]

    proto = cast(CachingProtocol, resource.proto)
    filesystem = proto.local_protocol.filesystem
    base_url = proto.local_protocol.url
    cached_file = os.path.join(base_url, "scores/res_a", "data.txt")

    assert not filesystem.exists(cached_file)

    with resource.open_raw_file("data.txt") as infile:
        assert "chr1" in infile.read()

    assert filesystem.exists(cached_file)


@pytest.fixture
def cached_group_repository(
    tmp_path: pathlib.Path,
) -> GenomicResourceCachedRepo:
    """Cached repo over a group of two named filesystem repos.

    ``repo_a`` and ``repo_b`` both hold ``one``, at different versions, with
    ``repo_a`` first in the group. This is the topology
    ``repository_factory`` builds whenever a group definition carries a
    ``cache_dir``.
    """
    children: list[Any] = []
    for repo_id, version, content in (
        ("repo_a", "1.0", "from repo_a"),
        ("repo_b", "2.0", "from repo_b"),
    ):
        root = tmp_path / repo_id
        setup_directories(root, {
            f"one({version})": {
                GR_CONF_FILE_NAME: "",
                "data.txt": content,
            },
        })
        proto = cast(
            FsspecReadWriteProtocol,
            build_fsspec_protocol(repo_id, str(root)))
        proto.build_content_file()
        children.append(GenomicResourceProtocolRepo(proto))

    return GenomicResourceCachedRepo(
        GenomicResourceGroupRepo(children, "group_repo"),
        f"file://{tmp_path}/cache")


@pytest.mark.parametrize("repository_id,expected", [
    ("repo_a", (1, 0)),
    ("repo_b", (2, 0)),
])
def test_repository_id_narrows_to_the_named_child(
    cached_group_repository: GenomicResourceCachedRepo,
    repository_id: str,
    expected: tuple[int, ...],
) -> None:
    """repository_id selects a specific child through the cached+group stack.

    Before #429 this matched nothing: the group compared its child ids
    against the *resource* id, and the cached repo compared the caching
    protocol's ``<id>.cached`` proto id.
    """
    found = cached_group_repository.find_resource(
        "one", repository_id=repository_id)
    assert found is not None
    assert found.version == expected
    assert isinstance(found.proto, CachingProtocol)

    got = cached_group_repository.get_resource(
        "one", repository_id=repository_id)
    assert got.version == expected


def test_repository_id_of_unknown_child_finds_nothing(
    cached_group_repository: GenomicResourceCachedRepo,
) -> None:
    assert cached_group_repository.find_resource(
        "one", repository_id="no_such_repo") is None


def test_find_and_get_resource_agree_on_group_precedence(
    cached_group_repository: GenomicResourceCachedRepo,
) -> None:
    """First child wins, and both accessors say so.

    Group child order is a priority list. ``get_resource`` already delegated
    to the child (so it honoured that order), while ``find_resource``
    enumerated everything and returned the highest version -- so the two could
    return different versions of the same id. See #429.
    """
    found = cached_group_repository.find_resource("one")
    got = cached_group_repository.get_resource("one")

    assert found is not None
    assert found.version == (1, 0)
    assert got.version == (1, 0)
    assert found.version == got.version


def _proto_repo(
    root: pathlib.Path, proto_id: str, content: dict[str, Any],
) -> GenomicResourceProtocolRepo:
    setup_directories(root, content)
    proto = cast(
        FsspecReadWriteProtocol, build_fsspec_protocol(proto_id, str(root)))
    proto.build_content_file()
    return GenomicResourceProtocolRepo(proto)


@pytest.fixture
def colliding_proto_id_repository(
    tmp_path: pathlib.Path,
) -> GenomicResourceCachedRepo:
    """Cached repo whose two group children share one (empty) proto id.

    Assembled from protocols directly, bypassing the factory: a definition
    can no longer produce this shape, since ``GroupRepoDefinition`` refuses
    duplicate child ids and synthesises a distinct id for a child that omits
    one (#445). Caching protocols are keyed by ``proto_id`` and their cache
    directory is derived from it, so both children would share one caching
    protocol and one cache directory.
    """
    children: list[Any] = [
        _proto_repo(tmp_path / "a", "", {
            "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
        }),
        _proto_repo(tmp_path / "b", "", {
            "two(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
        }),
    ]
    return GenomicResourceCachedRepo(
        GenomicResourceGroupRepo(children, "top"),
        f"file://{tmp_path}/cache")


@pytest.fixture
def same_id_colliding_repository(
    tmp_path: pathlib.Path,
) -> GenomicResourceCachedRepo:
    """Colliding proto ids where both children hold the *same* resource id.

    The corrupting shape: the two resources also collide inside the shared
    cache directory, so reads of one can return the other's bytes.
    """
    children: list[Any] = [
        _proto_repo(tmp_path / "a", "", {
            "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "AAAA-from-a"},
        }),
        _proto_repo(tmp_path / "b", "", {
            "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "BBBB-from-b"},
        }),
    ]
    return GenomicResourceCachedRepo(
        GenomicResourceGroupRepo(children, "top"),
        f"file://{tmp_path}/cache")


@pytest.mark.parametrize("produce", [
    lambda repo: list(repo.get_all_resources()),
    lambda repo: list(repo.search_resources()),
], ids=["get_all_resources", "search_resources"])
def test_colliding_repository_ids_are_refused(
    same_id_colliding_repository: GenomicResourceCachedRepo,
    produce: Callable[[GenomicResourceCachedRepo], Any],
) -> None:
    """Two remotes sharing a proto id must fail loudly, not silently alias.

    They would otherwise share a caching protocol *and* a cache directory:
    the second child's resources get bound to the first child's remote, so
    a resource id present in both reads the wrong file's bytes. Before this
    was refused, ``search_resources`` on this repository returned two hits
    that were the same object and both read ``AAAA-from-a``.
    """
    with pytest.raises(ValueError, match="used by more than one repository"):
        produce(same_id_colliding_repository)


def test_single_lookup_under_colliding_ids_is_not_aliased(
    same_id_colliding_repository: GenomicResourceCachedRepo,
) -> None:
    """A lone lookup sees one protocol, so there is nothing to alias.

    It resolves to the first child by group precedence and is bound to that
    child's remote -- correct, not corrupt. The collision is only detectable
    once a second protocol shows up, which is why the refusal lives in
    _get_or_create_cache_proto rather than at every entry point.
    """
    resource = same_id_colliding_repository.get_resource("one")

    assert isinstance(resource.proto, CachingProtocol)
    with resource.open_raw_file("data.txt") as infile:
        assert infile.read() == "AAAA-from-a"

    # ...and the moment the second protocol is reached, it is refused.
    with pytest.raises(ValueError, match="used by more than one repository"):
        list(same_id_colliding_repository.get_all_resources())


def test_colliding_repository_ids_name_both_urls(
    colliding_proto_id_repository: GenomicResourceCachedRepo,
) -> None:
    """The error must be actionable: which id, and which two repositories."""
    with pytest.raises(ValueError) as excinfo:
        list(colliding_proto_id_repository.get_all_resources())

    message = str(excinfo.value)
    assert "/a" in message
    assert "/b" in message
    assert "'id'" in message


@pytest.mark.parametrize("unsafe_proto_id", [
    "../../escaped", "sub/dir", "..", ".", "/absolute/gain460",
    "..\n", "\n..", "..\t", "..\r", "a\nb", "\x00",
], ids=[
    "traversal", "separator", "parent-dir", "current-dir", "absolute",
    "parent-newline", "newline-parent", "parent-tab", "parent-cr",
    "embedded-newline", "nul",
])
def test_unsafe_repository_id_is_refused_by_the_cache(
    tmp_path: pathlib.Path, unsafe_proto_id: str,
) -> None:
    """The cache directory is derived from the id, so it must be a segment.

    Assembled from protocols directly, bypassing the factory -- the shape a
    definition can no longer produce, since an unsafe ``id`` is now rejected
    at validation. A group assembled programmatically never sees that
    validation, so the guard has to hold here too (#460).

    The absolute id is the shape the shared testing helpers mint
    (``build_inmemory_test_repository`` names a protocol by its own
    ``/tmp/...`` directory), and it is the one that discards the cache url
    rather than merely climbing out of it. The control-character ids are
    the ones a segment check alone waves through: ``urlsplit`` deletes tab,
    CR and LF from the url the cache path is parsed out of, so ``"..\\n"``
    resolves to ``..`` and ``"a\\nb"`` resolves to ``ab``.
    """
    repo = GenomicResourceCachedRepo(
        GenomicResourceGroupRepo([_proto_repo(
            tmp_path / "remote", unsafe_proto_id, {
                "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            })], "top"),
        f"file://{tmp_path}/cache")

    with pytest.raises(ValueError, match="single path segment"):
        list(repo.get_all_resources())


def test_two_ids_a_url_folds_together_are_refused(
    tmp_path: pathlib.Path,
) -> None:
    """Distinct ids that name ONE cache directory are the #445 corruption.

    ``"ab"`` and ``"a\\nb"`` are different strings, so the duplicate-id
    guard sees no collision -- but ``urlsplit`` deletes the newline from the
    url the cache path comes from, so both resolve to the cache directory
    ``ab``. The second repository's resources would then be served the
    first's bytes, which is exactly what keying the cache by id is supposed
    to prevent. Refused (#460).
    """
    repo = GenomicResourceCachedRepo(
        GenomicResourceGroupRepo([
            _proto_repo(tmp_path / "first", "ab", {
                "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "first"},
            }),
            _proto_repo(tmp_path / "second", "a\nb", {
                "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "second"},
            }),
        ], "top"),
        f"file://{tmp_path}/cache")

    with pytest.raises(ValueError, match="single path segment"):
        list(repo.get_all_resources())

    assert not (tmp_path / "cache" / "ab" / "one").exists()


@pytest.mark.parametrize("mangled_proto_id", [
    "..\n", "a\nb",
], ids=["escape", "alias"])
def test_the_cache_path_check_holds_without_the_id_predicate(
    tmp_path: pathlib.Path, mocker: MockerFixture,
    mangled_proto_id: str,
) -> None:
    """The two guards are independently sensitive -- on purpose.

    ``is_safe_repo_id`` is a blocklist of what today's url parser mangles;
    the next mangling surprise walks around it. So the join site also
    asserts, positively, that the cache directory it is about to use is the
    single directory ``<cache_url>/<proto_id>``. With the predicate stubbed
    out to always say "safe", that assertion alone still refuses both an
    escape and a fold-together -- and refuses them BEFORE anything is
    created, since building a read-write protocol makes its root directory.
    """
    mocker.patch(
        "gain.genomic_resources.cached_repository.is_safe_repo_id",
        return_value=True)
    repo = GenomicResourceCachedRepo(
        GenomicResourceGroupRepo([_proto_repo(
            tmp_path / "remote", mangled_proto_id, {
                "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            })], "top"),
        f"file://{tmp_path}/cache/inner")

    with pytest.raises(ValueError, match="resolves to"):
        list(repo.get_all_resources())

    assert not (tmp_path / "cache").exists()


def test_a_cache_protocol_that_resolved_elsewhere_is_refused(
    tmp_path: pathlib.Path, mocker: MockerFixture,
) -> None:
    """The join site asks the built protocol where it actually landed.

    Re-deriving the cache path from the url is the same arithmetic the
    protocol does, so on its own it could only ever agree with itself. The
    check is repeated against ``root_path`` of the object that was actually
    built, which is what the caching writes go through -- if the two ever
    disagree, the guard refuses rather than trusting its own copy of the
    rule. Simulated here by a builder that hands back a protocol rooted
    somewhere else entirely.
    """
    real_build = build_fsspec_protocol

    def build_elsewhere(
        proto_id: str, _url: str, **kwargs: Any,
    ) -> Any:
        return real_build(proto_id, f"file://{tmp_path}/elsewhere", **kwargs)

    mocker.patch(
        "gain.genomic_resources.cached_repository.build_fsspec_protocol",
        side_effect=build_elsewhere)
    repo = GenomicResourceCachedRepo(
        GenomicResourceGroupRepo([_proto_repo(
            tmp_path / "remote", "remote_repo", {
                "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            })], "top"),
        f"file://{tmp_path}/cache")

    with pytest.raises(ValueError, match="resolves to"):
        list(repo.get_all_resources())


def _a_one_resource_grr(source_dir: pathlib.Path) -> None:
    """Realize a filesystem GRR with a single ``one`` position score."""
    (
        a_grr()
        .with_resource(
            "one",
            a_position_score()
            .with_score("score", "float")
            .with_data("""
                chrom  pos_begin  score
                chr1   10         0.1
            """),
        )
        .build_repo(source_dir)
    )


@pytest.mark.parametrize("unsafe_id_template", [
    "../../escaped",
    "{tmp_path}/escaped",
], ids=["traversal", "absolute"])
def test_an_unsafe_child_id_never_writes_outside_the_cache_dir(
    tmp_path: pathlib.Path,
    unsafe_id_template: str,
) -> None:
    """The two reproductions from #460 must fail loudly, not write.

    A child id is joined onto the cache url to name that child's cache
    directory, so ``../../escaped`` climbed two levels out of the configured
    ``cache_dir`` and an absolute id made the join discard the cache url
    altogether -- the configured ``cache_dir`` was silently ignored and the
    process wrote wherever the definition pointed. Both are refused now, and
    ``<tmp_path>/escaped`` -- where each variant used to land -- stays absent.
    """
    source_dir = tmp_path / "grr_source"
    _a_one_resource_grr(source_dir)
    cache_dir = tmp_path / "cache" / "inner"

    def build_and_cache() -> None:
        repo = build_genomic_resource_repository({
            "type": "group",
            "cache_dir": str(cache_dir),
            "children": [
                {"id": unsafe_id_template.format(tmp_path=tmp_path),
                 "type": "directory", "directory": str(source_dir)},
            ]})
        cache_resources(repo, ["one"], workers=1, progress=False)

    with pytest.raises(ValueError, match="single path segment"):
        build_and_cache()

    assert not (tmp_path / "escaped").exists()
    assert not cache_dir.exists()


def test_a_control_character_child_id_never_writes_above_the_cache_dir(
    tmp_path: pathlib.Path,
) -> None:
    """#460's criterion again, for the id that reads as a safe segment.

    ``"..\\n"`` carries no separator and is not ``..``, so a segment check
    alone passes it -- but the cache directory is parsed out of a url and
    the parser drops the newline, so the cached bytes landed in
    ``<cache_dir>/..``: one level above the configured cache directory,
    which is what a cache_dir is supposed to bound. Refused when the
    definition is loaded, and nothing under ``<tmp_path>/cache`` -- neither
    the escape nor the cache directory itself -- is created.
    """
    source_dir = tmp_path / "grr_source"
    _a_one_resource_grr(source_dir)

    def build_and_cache() -> None:
        repo = build_genomic_resource_repository({
            "type": "group",
            "cache_dir": str(tmp_path / "cache" / "inner"),
            "children": [
                {"id": "..\n",
                 "type": "directory", "directory": str(source_dir)},
            ]})
        cache_resources(repo, ["one"], workers=1, progress=False)

    with pytest.raises(ValueError, match="single path segment"):
        build_and_cache()

    assert not (tmp_path / "cache").exists()


def test_children_that_omit_an_id_cache_under_the_cache_dir(
    tmp_path: pathlib.Path,
) -> None:
    """A synthesised id is a safe segment and must keep working.

    The synthesis path (``_synthesise_repo_id``) is untouched by #460: it
    slugifies the child's identity through a non-alphanumeric class and
    appends a digest, so what it produces is always a single segment. This
    pins that -- the cached bytes land under the configured ``cache_dir``.
    """
    source_dir = tmp_path / "grr_source"
    _a_one_resource_grr(source_dir)
    cache_dir = tmp_path / "cache"

    repo = build_genomic_resource_repository({
        "type": "group",
        "cache_dir": str(cache_dir),
        "children": [
            {"type": "directory", "directory": str(source_dir)},
        ]})
    cache_resources(repo, ["one"], workers=1, progress=False)

    assert [path.name for path in cache_dir.glob("*/one/*.txt")] == ["data.txt"]


def test_find_and_get_resource_agree_on_a_missing_resource(
    cached_group_repository: GenomicResourceCachedRepo,
) -> None:
    """find_resource returns None where get_resource raises -- same input.

    find_resource's contract is to return None, never to raise;
    annotation_factory branches on ``resource is None``.
    """
    assert cached_group_repository.find_resource("no_such_res") is None

    with pytest.raises((ValueError, FileNotFoundError)):
        cached_group_repository.get_resource("no_such_res")


def test_repository_id_reaches_a_repo_nested_in_a_subgroup(
    tmp_path: pathlib.Path,
) -> None:
    """repository_id must descend through intermediate groups.

    Skipping every child whose id does not match means a group never
    descends into a sub-group holding the requested repository.
    """
    leaf = _proto_repo(tmp_path / "leaf", "leaf_repo", {
        "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "leaf"},
    })
    other = _proto_repo(tmp_path / "other", "other_repo", {
        "one(2.0)": {GR_CONF_FILE_NAME: "", "data.txt": "other"},
    })
    outer = GenomicResourceGroupRepo(
        [other, GenomicResourceGroupRepo([leaf], "inner_group")],
        "outer_group")

    found = outer.find_resource("one", repository_id="leaf_repo")
    assert found is not None
    assert found.version == (1, 0)

    assert outer.find_resource(
        "one", repository_id="other_repo") is not None
    assert outer.find_resource("one", repository_id="nope") is None

    # Naming the sub-group itself: the group must NOT re-apply the filter
    # inside a child it has already matched by id, or the child compares
    # "inner_group" against its own children's ids and finds nothing.
    found = outer.find_resource("one", repository_id="inner_group")
    assert found is not None
    assert found.version == (1, 0)


def test_repository_id_names_a_cached_child_of_a_group(
    tmp_path: pathlib.Path,
) -> None:
    """A cached child answers to its own id, filter not re-applied inside.

    Same branch as naming a sub-group: GenomicResourceCachedRepo.repo_id is
    "<child>.caching_repo", and forwarding that id into it would compare it
    against the wrapped child's id and find nothing.
    """
    leaf = _proto_repo(tmp_path / "leaf", "leaf_repo", {
        "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "leaf"},
    })
    other = _proto_repo(tmp_path / "other", "other_repo", {
        "one(2.0)": {GR_CONF_FILE_NAME: "", "data.txt": "other"},
    })
    cached_leaf = GenomicResourceCachedRepo(
        leaf, f"file://{tmp_path}/cache")
    outer = GenomicResourceGroupRepo([other, cached_leaf], "outer_group")

    found = outer.find_resource(
        "one", repository_id="leaf_repo.caching_repo")
    assert found is not None
    assert found.version == (1, 0)
    assert isinstance(found.proto, CachingProtocol)


def test_empty_repository_id_is_not_a_filter(
    tmp_path: pathlib.Path,
) -> None:
    """A falsy repository_id must mean "no filter" at every layer.

    GenomicResourceProtocolRepo ignores a falsy repository_id, so a group
    treating "" as a real id would disagree with its own children -- and ""
    is exactly what repository_factory hands an unnamed child.
    """
    first = _proto_repo(tmp_path / "first", "first_repo", {
        "one(1.0)": {GR_CONF_FILE_NAME: "", "data.txt": "first"},
    })
    unnamed = _proto_repo(tmp_path / "unnamed", "", {
        "one(2.0)": {GR_CONF_FILE_NAME: "", "data.txt": "unnamed"},
    })
    group = GenomicResourceGroupRepo([first, unnamed], "group")

    assert group.find_resource("one", repository_id="") == \
        group.find_resource("one")


# ---------------------------------------------------------------------------
# The lazily populated memo caches must be populated exactly once, even when
# several threads reach them at the same time (#446).
# ---------------------------------------------------------------------------


def _slow_down_cache_proto_build(
    mocker: MockerFixture, delay: float = 0.05,
) -> None:
    """Widen the window of the check-then-populate on ``cache_protos``.

    Building the caching protocol already does filesystem work that releases
    the GIL, so the race reproduces on its own; the delay only makes the
    reproduction deterministic instead of merely very likely.
    """
    real_build = build_fsspec_protocol

    def slow_build(*args: Any, **kwargs: Any) -> Any:
        time.sleep(delay)
        return real_build(*args, **kwargs)

    mocker.patch(
        "gain.genomic_resources.cached_repository.build_fsspec_protocol",
        slow_build)


def test_concurrent_get_resource_shares_one_caching_protocol(
    cache_repository: CacheRepositoryBuilder,
    mocker: MockerFixture,
    run_in_threads: RunInThreads,
) -> None:
    """Racing first-calls must not hand out an orphaned caching protocol.

    A protocol that lost the ``cache_protos`` assignment is unreachable from
    ``invalidate()``, so a caller holding a resource bound to it keeps a
    stale resource dict forever. See #446.
    """
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            }) as cache_repo:
        _slow_down_cache_proto_build(mocker)

        resources, errors = run_in_threads(
            lambda: cache_repo.get_resource("one"), 8)

        assert not errors
        assert len(resources) == 8
        assert len(cache_repo.cache_protos) == 1
        expected_proto = next(iter(cache_repo.cache_protos.values()))
        assert {id(res.proto) for res in resources} == {id(expected_proto)}
        assert len({id(res) for res in resources}) == 1


def test_concurrent_get_all_resources_builds_the_list_once(
    cache_repository: CacheRepositoryBuilder,
    mocker: MockerFixture,
    run_in_threads: RunInThreads,
) -> None:
    """Racing first enumerations must enumerate the child exactly once (#446).

    Two threads building the memo in parallel walk the child twice and hand
    out two different lists for the same repository.
    """
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            "two": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            }) as cache_repo:

        enumerations = []
        real_get_all_resources = cache_repo.child.get_all_resources

        def slow_get_all_resources() -> Any:
            enumerations.append(1)
            time.sleep(0.05)
            return real_get_all_resources()

        mocker.patch.object(
            cache_repo.child, "get_all_resources", slow_get_all_resources)

        listings, errors = run_in_threads(
            lambda: list(cache_repo.get_all_resources()), 8)

        assert not errors
        assert len(enumerations) == 1
        assert all(len(listing) == 2 for listing in listings)
        for index in range(2):
            assert len({id(listing[index]) for listing in listings}) == 1


def test_first_enumeration_of_a_cached_repo_does_not_deadlock(
    cache_repository: CacheRepositoryBuilder,
    run_in_threads: RunInThreads,
) -> None:
    """The memo guard must be reentrant (#446).

    ``get_all_resources`` populates its memo by mapping every child resource
    through ``_to_cache_resource``, which calls
    ``_get_or_create_cache_proto`` -- the same thread re-enters the guarded
    region. A single non-reentrant lock shared by both would deadlock right
    here, so the enumeration runs in a joined-with-timeout thread and fails
    loudly instead of hanging the suite.
    """
    with cache_repository({
            "one": {GR_CONF_FILE_NAME: "", "data.txt": "a"},
            "two": {GR_CONF_FILE_NAME: "", "data.txt": "b"},
            }) as cache_repo:

        listings, errors = run_in_threads(
            lambda: list(cache_repo.get_all_resources()), 1, timeout=10)

        assert not errors
        assert len(listings[0]) == 2


_CSI_VCF_DATA = """
##fileformat=VCFv4.2
##INFO=<ID=value,Number=A,Type=Float,Description="value">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      value=0.1
"""


def _build_cache_repo(
    source_dir: pathlib.Path, cache_dir: pathlib.Path,
) -> GenomicResourceCachedRepo:
    """Wrap an already-realized filesystem GRR in a cache-backed repo."""
    return GenomicResourceCachedRepo(
        build_filesystem_test_repository(source_dir),
        f"file://{cache_dir}")


def test_cache_resources_fetches_a_csi_index_and_the_cached_score_opens(
    tmp_path: pathlib.Path,
) -> None:
    """gain#430: the cache prefetch must fetch the index that exists."""
    source_dir = tmp_path / "grr_source"
    (
        a_grr()
        .with_resource(
            "csi_score",
            a_position_score()
            .with_tabix(csi=True)
            .with_zero_based()
            .with_score("value", "float")
            .with_data("""
                chrom  pos_begin  pos_end  value
                chr1   10         20       0.1
            """),
        )
        .build_repo(source_dir)
    )
    cache_repo = _build_cache_repo(source_dir, tmp_path / "cache")

    cache_resources(cache_repo, ["csi_score"], workers=1, progress=False)

    assert cache_repo.get_resource_cached_files("csi_score") == {
        "data.txt.gz", "data.txt.gz.csi"}
    score = build_score_from_resource(cache_repo.get_resource("csi_score"))
    with score.open():
        assert list(score.fetch_region_values("chr1", 11, 20, ["value"])) == [
            (11, 20, [0.1])]


def test_cache_resources_fetches_a_csi_index_of_a_vcf_score(
    tmp_path: pathlib.Path,
) -> None:
    """gain#430: the same resolution drives the cached VCF open path."""
    source_dir = tmp_path / "grr_source"
    (
        a_grr()
        .with_resource(
            "csi_vcf_score",
            a_vcf_info_score().with_csi_index().with_data(_CSI_VCF_DATA),
        )
        .build_repo(source_dir)
    )
    cache_repo = _build_cache_repo(source_dir, tmp_path / "cache")

    cache_resources(cache_repo, ["csi_vcf_score"], workers=1, progress=False)

    # A VCF score also pulls its header companion files into the cache, so
    # this is a containment check rather than an equality one.
    assert {"data.vcf.gz", "data.vcf.gz.csi"} <= \
        cache_repo.get_resource_cached_files("csi_vcf_score")
    score = build_score_from_resource(
        cache_repo.get_resource("csi_vcf_score"))
    with score.open():
        assert score.get_all_chromosomes() == ["chr1"]
        assert list(score.fetch_region_values("chr1", 10, 10, ["value"])) == [
            (10, 10, [pytest.approx(0.1)])]


def test_cached_open_vcf_file_fetches_the_csi_index_on_a_cold_cache(
    tmp_path: pathlib.Path,
) -> None:
    """gain#430: ``open_vcf_file`` resolves its own index.

    Deliberately opens the VCF directly, with nothing else touched first: a
    preceding tabix open -- what the chromosome-listing path does -- would
    have already pulled the ``.csi`` into the cache and masked a wrong index
    name here.
    """
    source_dir = tmp_path / "grr_source"
    (
        a_grr()
        .with_resource(
            "csi_vcf_score",
            a_vcf_info_score().with_csi_index().with_data(_CSI_VCF_DATA),
        )
        .build_repo(source_dir)
    )
    cache_repo = _build_cache_repo(source_dir, tmp_path / "cache")

    resource = cache_repo.get_resource("csi_vcf_score")
    with resource.open_vcf_file("data.vcf.gz") as vcf:
        assert [record.pos for record in vcf.fetch("chr1", 9, 10)] == [10]

    assert "data.vcf.gz.csi" in \
        cache_repo.get_resource_cached_files("csi_vcf_score")
