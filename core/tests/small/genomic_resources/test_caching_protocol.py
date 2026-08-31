# pylint: disable=W0621,C0114,C0116,W0212,W0613
import time
from typing import Any

import pytest
import pytest_mock
from gain.genomic_resources.cached_repository import CachingProtocol
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.testing import (
    FsspecReadWriteProtocol,
    build_filesystem_test_protocol,
    build_inmemory_test_protocol,
    setup_directories,
    setup_genome_bgz,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import a_reference_genome

from .conftest import RunInThreads


def test_caching_repo_simple(
        content_fixture: dict[str, Any],
        tmp_path_factory: pytest.TempPathFactory) -> None:

    local_proto = build_filesystem_test_protocol(
        tmp_path_factory.mktemp("cache_proto_test"))

    assert local_proto is not None
    assert len(list(local_proto.get_all_resources())) == 0

    remote_proto = build_inmemory_test_protocol(content_fixture)
    assert len(list(remote_proto.get_all_resources())) == 5

    caching_proto = CachingProtocol(remote_proto, local_proto)
    assert caching_proto is not None

    assert len(list(caching_proto.get_all_resources())) == 5


def test_caching_protocol_rejects_non_local_cache(
        content_fixture: dict[str, Any]) -> None:
    """A cache on anything but the local filesystem is unsupported.

    The per-file lock the caching protocol relies on is a lockfile, and a
    lockfile only means anything on a local filesystem; a remote cache used
    to be handed a silent no-op lock instead. Reject the configuration when
    the protocol is built, before any caching work begins. See #473.
    """
    remote_proto = build_inmemory_test_protocol(content_fixture)
    cache_proto = build_inmemory_test_protocol({})

    with pytest.raises(ValueError) as excinfo:
        CachingProtocol(remote_proto, cache_proto)

    message = str(excinfo.value)
    assert "memory" in message
    assert cache_proto.get_url() in message
    assert "local filesystem" in message


@pytest.fixture
def remote_proto_fixture(
    content_fixture: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
) -> FsspecReadWriteProtocol:
    root_path = tmp_path_factory.mktemp("source_proto_fixture")
    setup_directories(root_path, content_fixture)
    setup_tabix(
        root_path / "one" / "test.txt.gz",
        """
            #chrom  pos_begin  pos_end    c1
            1      1          10         1.0
            2      1          10         2.0
            2      11         20         2.5
            3      1          10         3.0
            3      11         20         3.5
        """,
        seq_col=0, start_col=1, end_col=2)
    return build_filesystem_test_protocol(root_path)


@pytest.fixture
def bgz_genome_caching_proto(
    tmp_path_factory: pytest.TempPathFactory,
) -> CachingProtocol:
    # Not parametrized over the GRR schemes: the cache side of a caching
    # protocol must be on the local filesystem (#473). The remote side here
    # is a filesystem protocol, so no remote-scheme coverage is lost.
    remote_root = tmp_path_factory.mktemp("bgz_genome_remote")
    setup_genome_bgz(
        remote_root / "bgz_genome" / "chr.fa.gz",
        """
            >pesho
            NNACCCAAAC
            GGGCCTTCCN
            NNNA
            >gosho
            NNAACCGGTT
            TTGGCCAANN
        """)
    remote_proto = build_filesystem_test_protocol(remote_root)

    cache_root = tmp_path_factory.mktemp("bgz_genome_file_cache")
    return CachingProtocol(
        remote_proto, build_filesystem_test_protocol(cache_root))


@pytest.fixture
def caching_proto(
    tmp_path_factory: pytest.TempPathFactory,
    remote_proto_fixture: FsspecReadWriteProtocol,
) -> CachingProtocol:
    # Local cache only -- see ``bgz_genome_caching_proto`` and #473.
    root_path = tmp_path_factory.mktemp("file_caching_proto_path")
    return CachingProtocol(
        remote_proto_fixture, build_filesystem_test_protocol(root_path))


def test_get_resource_three(
        caching_proto: CachingProtocol) -> None:
    proto = caching_proto
    res = proto.get_resource("three")

    assert res.resource_id == "three"
    assert res.version == (2, 0)


def test_get_resource_two(
        caching_proto: CachingProtocol) -> None:
    res = caching_proto.get_resource("sub/two")

    assert res.resource_id == "sub/two"
    assert res.version == (1, 0)


def test_get_resource_copies_nothing_three(
        caching_proto: CachingProtocol) -> None:
    res = caching_proto.get_resource("three")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "genomic_resource.yaml")
    assert not local_proto.file_exists(res, "sub1/a.txt")
    assert not local_proto.file_exists(res, "sub2/b.txt")


def test_get_resource_copies_nothing_two(
        caching_proto: CachingProtocol) -> None:
    res = caching_proto.get_resource("sub/two")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "genomic_resource.yaml")
    assert not local_proto.file_exists(res, "genes.gtf")


def test_open_raw_file_copies_the_file_three_a(
        caching_proto: CachingProtocol) -> None:

    res = caching_proto.get_resource("three")
    with caching_proto.open_raw_file(res, "sub1/a.txt") as infile:
        content = infile.read()
    assert content == "a"

    local_proto = caching_proto.local_protocol
    assert local_proto.file_exists(res, "sub1/a.txt")
    assert not local_proto.file_exists(res, "genomic_resource.yaml")
    assert not local_proto.file_exists(res, "sub2/b.txt")


def test_open_raw_file_copies_the_file_three_b(
        caching_proto: CachingProtocol) -> None:
    res = caching_proto.get_resource("three")
    with caching_proto.open_raw_file(res, "sub2/b.txt") as infile:
        content = infile.read()
    assert content == "b"

    local_proto = caching_proto.local_protocol
    assert local_proto.file_exists(res, "sub2/b.txt")
    assert not local_proto.file_exists(res, "genomic_resource.yaml")
    assert not local_proto.file_exists(res, "sub1/a.txt")


def test_open_tabix_file_simple(
        caching_proto: CachingProtocol) -> None:
    res = caching_proto.get_resource("one")
    with caching_proto.open_tabix_file(res, "test.txt.gz") as tabix:
        assert tabix.contigs == ["1", "2", "3"]


def test_open_tabix_file_caches_both_files(
        caching_proto: CachingProtocol) -> None:
    """Test that opening tabix file caches both data and index."""
    res = caching_proto.get_resource("one")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "test.txt.gz")
    assert not local_proto.file_exists(res, "test.txt.gz.tbi")

    with caching_proto.open_tabix_file(res, "test.txt.gz") as tabix:
        assert tabix.contigs == ["1", "2", "3"]

    # Both files should be cached
    assert local_proto.file_exists(res, "test.txt.gz")
    assert local_proto.file_exists(res, "test.txt.gz.tbi")


def test_open_fasta_file_caches_all_three(
        bgz_genome_caching_proto: CachingProtocol) -> None:
    """Opening a bgzipped genome caches the data, .fai and .gzi files."""
    caching_proto = bgz_genome_caching_proto
    res = caching_proto.get_resource("bgz_genome")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "chr.fa.gz")
    assert not local_proto.file_exists(res, "chr.fa.gz.fai")
    assert not local_proto.file_exists(res, "chr.fa.gz.gzi")

    with caching_proto.open_fasta_file(res, "chr.fa.gz") as fasta:
        assert fasta.fetch("pesho", 0, 12) == "NNACCCAAACGG"

    # all three files (data + both indexes) must be cached locally
    assert local_proto.file_exists(res, "chr.fa.gz")
    assert local_proto.file_exists(res, "chr.fa.gz.fai")
    assert local_proto.file_exists(res, "chr.fa.gz.gzi")


def test_reference_genome_over_cached_protocol(
        bgz_genome_caching_proto: CachingProtocol) -> None:
    """A bgzipped genome reads correctly through the caching protocol."""
    res = bgz_genome_caching_proto.get_resource("bgz_genome")
    with build_reference_genome_from_resource(res).open() as genome:
        assert genome.get_chrom_length("pesho") == 24
        assert genome.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"
        assert genome.get_sequence("gosho", 11, 20) == "TTGGCCAANN"


@pytest.fixture
def custom_index_genome_caching_proto(
    tmp_path_factory: pytest.TempPathFactory,
) -> CachingProtocol:
    # Local cache side only, as for bgz_genome_caching_proto (#473).
    remote_root = tmp_path_factory.mktemp("custom_index_genome_remote")
    (
        a_reference_genome()
        .with_chromosome("pesho", "NNACCCAAACGGGCCTTCCNNNNA")
        .with_index_file("custom.fai")
        .realize_into(remote_root / "custom_index_genome")
    )
    remote_proto = build_filesystem_test_protocol(remote_root)

    cache_root = tmp_path_factory.mktemp("custom_index_genome_file_cache")
    return CachingProtocol(
        remote_proto, build_filesystem_test_protocol(cache_root))


def test_custom_index_genome_caches_the_configured_index(
        custom_index_genome_caching_proto: CachingProtocol) -> None:
    """The caching protocol refreshes the configured index, not a derived."""
    proto = custom_index_genome_caching_proto
    res = proto.get_resource("custom_index_genome")

    with build_reference_genome_from_resource(res).open() as genome:
        assert genome.get_sequence("pesho", 1, 12) == "NNACCCAAACGG"

    local_proto = proto.local_protocol
    assert local_proto.file_exists(res, "custom.fai")
    assert not local_proto.file_exists(res, "chr.fa.gz.fai")


def test_load_manifest(
    caching_proto: CachingProtocol,
) -> None:
    """Test loading manifest through caching protocol."""
    res = caching_proto.get_resource("three")

    manifest = caching_proto.load_manifest(res)
    assert manifest is not None

    # Config file should be cached after loading manifest
    local_proto = caching_proto.local_protocol
    assert local_proto.file_exists(res, "genomic_resource.yaml")


def test_get_all_resources_caches_list(
        caching_proto: CachingProtocol) -> None:
    """Test that get_all_resources caches the resource list."""
    assert caching_proto._all_resources is None

    # First call populates cache
    resources = list(caching_proto.get_all_resources())
    assert len(resources) == 5
    assert caching_proto._all_resources is not None

    # Second call uses cached list
    resources2 = list(caching_proto.get_all_resources())
    assert resources2 == list(caching_proto._all_resources.values())


def test_refresh_cached_resource(
        caching_proto: CachingProtocol) -> None:
    """Test refreshing all files in a resource."""
    res = caching_proto.get_resource("three")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "sub1/a.txt")
    assert not local_proto.file_exists(res, "sub2/b.txt")

    # Refresh all files
    caching_proto.refresh_cached_resource(res)

    # All files should be cached (except lockfiles)
    assert local_proto.file_exists(res, "genomic_resource.yaml")
    assert local_proto.file_exists(res, "sub1/a.txt")
    assert local_proto.file_exists(res, "sub2/b.txt")


def test_refresh_cached_resource_file_returns_tuple(
        caching_proto: CachingProtocol) -> None:
    """Test that refresh_cached_resource_file returns resource_id, filename."""
    res = caching_proto.get_resource("three")

    result = caching_proto.refresh_cached_resource_file(res, "sub1/a.txt")
    assert isinstance(result, tuple)
    assert result == (res.resource_id, "sub1/a.txt")


def test_lockfiles_are_ignored(
        caching_proto: CachingProtocol) -> None:
    """Test that .lockfile files are ignored during refresh."""
    res = caching_proto.get_resource("one")

    # Attempting to refresh a lockfile should return immediately
    result = caching_proto.refresh_cached_resource_file(
        res, "test.txt.gz.lockfile")
    assert result == (res.resource_id, "test.txt.gz.lockfile")

    # Lockfile should not be cached
    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "test.txt.gz.lockfile")


def test_classify_cached_resource_file_lockfile_ignored(
        caching_proto: CachingProtocol) -> None:
    """.lockfile classifies as no-download without touching the remote."""
    res = caching_proto.get_resource("one")

    verdict = caching_proto.classify_cached_resource_file(
        res, "test.txt.gz.lockfile")
    assert verdict.needs_download is False
    assert verdict.size == 0


def test_classify_cached_resource_file_uncached(
        caching_proto: CachingProtocol) -> None:
    """An uncached file classifies as needing download, no lock taken."""
    res = caching_proto.get_resource("three")
    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "sub1/a.txt")

    verdict = caching_proto.classify_cached_resource_file(res, "sub1/a.txt")
    assert verdict.needs_download is True
    assert verdict.size > 0
    # classify must not download
    assert not local_proto.file_exists(res, "sub1/a.txt")


def test_download_cached_resource_file_copies_and_returns_tuple(
        caching_proto: CachingProtocol) -> None:
    """download_cached_resource_file unconditionally caches the file."""
    res = caching_proto.get_resource("three")
    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "sub1/a.txt")

    result = caching_proto.download_cached_resource_file(res, "sub1/a.txt")

    assert result == (res.resource_id, "sub1/a.txt")
    assert local_proto.file_exists(res, "sub1/a.txt")


def test_classify_then_download_after_caching_is_fresh(
        caching_proto: CachingProtocol) -> None:
    """After a download, the same file classifies as fresh (no re-download)."""
    res = caching_proto.get_resource("three")

    caching_proto.download_cached_resource_file(res, "sub1/a.txt")

    verdict = caching_proto.classify_cached_resource_file(res, "sub1/a.txt")
    assert verdict.needs_download is False


def test_public_url_override(
        content_fixture: dict[str, Any],
        tmp_path_factory: pytest.TempPathFactory) -> None:
    """Test CachingProtocol with custom public URL."""
    remote_proto = build_inmemory_test_protocol(content_fixture)
    local_proto = build_filesystem_test_protocol(
        tmp_path_factory.mktemp("cache_test"))

    custom_public_url = "https://custom.example.com/resources"
    caching_proto = CachingProtocol(
        remote_proto, local_proto, public_url=custom_public_url)

    assert caching_proto.get_public_url() == custom_public_url
    assert caching_proto.get_url() == remote_proto.get_url()


def test_get_resource_url_after_caching(
        caching_proto: CachingProtocol) -> None:
    """Test getting resource URL points to cached location."""
    res = caching_proto.get_resource("one")

    resource_url = caching_proto.get_resource_url(res)
    assert resource_url is not None


def test_get_resource_file_url_triggers_caching(
        caching_proto: CachingProtocol) -> None:
    """Test getting file URL triggers caching."""
    res = caching_proto.get_resource("three")

    local_proto = caching_proto.local_protocol
    assert not local_proto.file_exists(res, "sub1/a.txt")

    # Getting file URL should trigger caching
    file_url = caching_proto.get_resource_file_url(res, "sub1/a.txt")
    assert file_url is not None
    assert "sub1/a.txt" in file_url

    # File should now be cached
    assert local_proto.file_exists(res, "sub1/a.txt")


def test_protocol_invalidate_clears_cache(
        caching_proto: CachingProtocol) -> None:
    """Test invalidate() clears cached resources list."""
    # Populate cache
    list(caching_proto.get_all_resources())
    assert caching_proto._all_resources is not None

    # Invalidate
    caching_proto.invalidate()
    assert caching_proto._all_resources is None


def test_protocol_get_id(
        caching_proto: CachingProtocol) -> None:
    """Test protocol ID is set correctly."""
    proto_id = caching_proto.get_id()
    assert proto_id is not None
    assert proto_id == caching_proto.local_protocol.proto_id


def test_concurrent_get_all_resources_dict_builds_the_dict_once(
    content_fixture: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
    mocker: pytest_mock.MockerFixture,
    run_in_threads: RunInThreads,
) -> None:
    """Racing first-calls must enumerate the remote once and agree (#446).

    Two threads building the memo in parallel produce two sets of
    ``CacheResource`` objects for the same resources; whichever set loses the
    assignment is handed to its caller anyway, breaking the identity promise
    that a returned resource is the one the memo holds.
    """
    local_proto = build_filesystem_test_protocol(
        tmp_path_factory.mktemp("concurrent_cache_proto"))
    remote_proto = build_inmemory_test_protocol(content_fixture)
    caching_proto = CachingProtocol(remote_proto, local_proto)

    enumerations = []
    real_get_all_resources = remote_proto.get_all_resources

    def slow_get_all_resources() -> Any:
        enumerations.append(1)
        # Widen the check-then-populate window so the race is deterministic
        # rather than merely likely.
        time.sleep(0.05)
        return real_get_all_resources()

    mocker.patch.object(
        remote_proto, "get_all_resources", slow_get_all_resources)

    dicts, errors = run_in_threads(caching_proto.get_all_resources_dict, 8)

    assert not errors
    assert len(enumerations) == 1
    assert len({id(resources) for resources in dicts}) == 1
    for resource_id in dicts[0]:
        assert len({id(resources[resource_id]) for resources in dicts}) == 1
