"""Tests for the catch-all ``basic`` resource implementation (gain)."""
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.implementations.basic_resource_impl import (
    BasicResourceImplementation,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


def test_untyped_resource_resolves_to_basic_type() -> None:
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": "",
        "data.txt": "a",
    })
    assert res.get_type() == "basic"
    assert get_resource_implementation_builder("basic") is not None


def test_basic_implementation_files_cover_all_resource_data() -> None:
    # The basic (catch-all) implementation must enumerate every data file so
    # caching still mirrors the old untyped behaviour (gain#78). Otherwise
    # _enumerate_resource_files returns only ["genomic_resource.yaml"] and the
    # resource's data files are silently dropped from the cache.
    res = build_inmemory_test_resource({
        "genomic_resource.yaml": "",
        "data1.txt": "a",
        "data2.txt": "b",
    })
    impl = BasicResourceImplementation(res)
    assert impl.files == {"data1.txt", "data2.txt"}
