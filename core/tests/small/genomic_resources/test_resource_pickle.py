# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import pickle  # ruff: ignore[suspicious-pickle-import]
import textwrap

import pytest
from gain.genomic_resources.cached_repository import (
    CachingProtocol,
    GenomicResourceCachedRepo,
)
from gain.genomic_resources.fsspec_protocol import FsspecReadOnlyProtocol
from gain.genomic_resources.repository import (
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)


def _setup_scores(root_path: pathlib.Path) -> None:
    setup_directories(
        root_path,
        {
            "one": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_one
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          1.01
                    chr1   54         1.02
                """),
            },
            "two": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_two
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          2.11
                    chr1   54         2.12
                """),
            },
            "three": {
                "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score_three
                          type: float
                          name: score
                """),
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  score
                    chr1   4          3.11
                    chr1   54         3.12
                """),
            },
        },
    )


@pytest.fixture
def grr_fixture(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path / "test_local_grr"
    _setup_scores(root_path)
    return build_genomic_resource_repository({
        "id": "test_local",
        "type": "directory",
        "directory": str(root_path),
    })


@pytest.fixture
def cached_grr_fixture(
    tmp_path: pathlib.Path,
) -> GenomicResourceCachedRepo:
    root_path = tmp_path / "test_local_grr"
    _setup_scores(root_path)
    repo = build_genomic_resource_repository({
        "id": "test_local",
        "type": "directory",
        "directory": str(root_path),
        "cache_dir": str(tmp_path / "cache"),
    })
    assert isinstance(repo, GenomicResourceCachedRepo)
    return repo


def test_pickle_genomic_score(grr_fixture: GenomicResourceRepo) -> None:

    res_one = grr_fixture.get_resource("one")
    res_one_u = pickle.loads(pickle.dumps(res_one))
    assert res_one_u.get_type() == "position_score"
    assert res_one_u.resource_id == "one"

    assert isinstance(res_one.proto, FsspecReadOnlyProtocol)
    assert isinstance(res_one_u.proto, FsspecReadOnlyProtocol)

    assert id(res_one.proto) == id(res_one_u.proto)


def test_pickle_cache_backed_resource(
    cached_grr_fixture: GenomicResourceCachedRepo,
) -> None:
    """A cache-backed resource carries its caching protocol through pickle.

    The protocol memoizes its resource dict behind a lock, and a lock is
    unpicklable -- so the guard added in #446 has to be dropped and rebuilt
    around the round-trip, or every dask worker read of a cached GRR breaks.
    """
    res_one = cached_grr_fixture.get_resource("one")
    assert isinstance(res_one.proto, CachingProtocol)

    res_one_u = pickle.loads(pickle.dumps(res_one))

    assert isinstance(res_one_u.proto, CachingProtocol)
    assert res_one_u.resource_id == "one"
    assert res_one_u.get_type() == "position_score"
    with res_one_u.open_raw_file("data.txt") as infile:
        assert "1.01" in infile.read()

    # The lock-guarded memo is the point of the round-trip: a worker that
    # unpickles a cache-backed resource and enumerates through its protocol
    # is the realistic path, and it needs the rebuilt lock.
    assert "one" in res_one_u.proto.get_all_resources_dict()


def test_pickle_cache_backed_resource_can_be_invalidated(
    cached_grr_fixture: GenomicResourceCachedRepo,
) -> None:
    """An unpickled caching protocol can still be invalidated.

    ``invalidate`` is the second entry point behind the memo guard (see
    #446); a worker that drops its cached view of an unpickled resource
    reaches it without ever enumerating.
    """
    res_one = cached_grr_fixture.get_resource("one")
    res_one_u = pickle.loads(pickle.dumps(res_one))

    res_one_u.proto.invalidate()

    assert "one" in res_one_u.proto.get_all_resources_dict()


def test_pickle_cached_repository(
    cached_grr_fixture: GenomicResourceCachedRepo,
) -> None:
    """The cached repository itself survives a round-trip and still resolves.

    Its memo guard is a reentrant lock (see #446); like the protocol's, it
    must not reach the pickle. The repository is primed before the
    round-trip so that ``cache_protos`` travels populated -- a cold
    repository builds a brand new caching protocol on the other side and
    never exercises the unpickled one.
    """
    cached_grr_fixture.get_resource("one")
    assert cached_grr_fixture.cache_protos

    repo_u = pickle.loads(pickle.dumps(cached_grr_fixture))

    assert isinstance(repo_u, GenomicResourceCachedRepo)
    assert repo_u.cache_protos

    res_two = repo_u.get_resource("two")
    assert res_two.resource_id == "two"
    assert isinstance(res_two.proto, CachingProtocol)
    with res_two.open_raw_file("data.txt") as infile:
        assert "2.11" in infile.read()
