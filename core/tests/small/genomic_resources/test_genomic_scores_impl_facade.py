"""What the ``genomic_scores_impl`` package promises its importers.

The module was split into a package (gain#1007): ``scan`` holds the
statistics machinery as module-level functions, ``impl`` holds the two
implementation classes, and the package ``__init__`` is a facade that
re-exports what anything outside imports.

The facade is not decoration.  Four ``core/pyproject.toml`` entry points
name ``…implementations.genomic_scores_impl:{GenomicScoreImplementation,
FragmentScoreImplementation}`` by module path, and a stale entry point
fails when the plugin is LOADED -- when a repository of that type is first
opened -- not when anything is imported.  So the failure it would produce
is a resource type that has no implementation at runtime, far from the
rename that caused it.  These tests fail at the rename instead.
"""
import inspect
import types

import pytest
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.implementations import genomic_scores_impl
from gain.genomic_resources.implementations.genomic_scores_impl import (
    FragmentScoreImplementation,
    GenomicScoreImplementation,
    impl,
    scan,
)


def test_the_facade_exports_exactly_three_names() -> None:
    """``__all__`` is the whole promise -- not a sample of it."""
    assert sorted(genomic_scores_impl.__all__) == [
        "FragmentScoreImplementation",
        "GenomicScoreImplementation",
        "build_score_implementation_from_resource",
    ]


@pytest.mark.parametrize("name", [
    "GenomicScoreImplementation",
    "FragmentScoreImplementation",
    "build_score_implementation_from_resource",
])
def test_each_exported_name_is_the_one_impl_defines(name: str) -> None:
    """The facade re-exports; it does not redefine."""
    assert getattr(genomic_scores_impl, name) is getattr(impl, name)


@pytest.mark.parametrize(("resource_type", "expected"), [
    ("position_score", GenomicScoreImplementation),
    ("allele_score", GenomicScoreImplementation),
    ("fragment_score", FragmentScoreImplementation),
    # The legacy spelling, registered until 2027.1.0 (ADR 0011).
    ("cnv_collection", FragmentScoreImplementation),
])
def test_the_entry_point_of_each_score_type_still_loads(
    resource_type: str, expected: type,
) -> None:
    """Every entry point naming this module resolves through the facade.

    ``get_resource_implementation_builder`` is what a repository calls the
    first time it meets a resource of the type, and it is the call that
    performs ``EntryPoint.load()``.
    """
    assert get_resource_implementation_builder(resource_type) is expected


def test_the_two_classes_live_in_the_impl_submodule() -> None:
    """The split is real: the classes are defined in ``impl``, not re-created.

    Pinned because ``__module__`` is what pickle and the task graph write
    down when they name a class.
    """
    assert GenomicScoreImplementation.__module__ == impl.__name__
    assert FragmentScoreImplementation.__module__ == impl.__name__


@pytest.mark.parametrize("name", [
    # The five the task wiring in ``impl`` schedules...
    "do_noregion_histograms",
    "do_min_max_task",
    "merge_min_max",
    "do_histogram_task",
    "merge_and_save_histograms",
    # ...and the rest of the machinery the tests drive directly.
    "unpack_score_defs",
    "scan_region",
    "do_min_max",
    "do_min_max_bulk",
    "do_histogram",
    "do_histogram_bulk",
    "bulk_region_scan",
    "bulk_scan_eligible",
    "can_bulk_histogram",
    "can_bulk_min_max",
    "update_hist_confs",
    "merge_histograms",
])
def test_the_machinery_is_a_module_level_function_of_scan(name: str) -> None:
    """No longer a staticmethod hanging off the implementation class.

    A plain function is what a task body should be: the task graph pickles
    it by module path, and nothing about scanning a region needs an
    implementation instance to exist.
    """
    assert isinstance(getattr(scan, name), types.FunctionType)


@pytest.mark.parametrize("name", [
    "do_histogram",
    "do_histogram_task",
    "do_min_max",
    "merge_and_save_histograms",
])
def test_the_machinery_left_the_implementation_class(name: str) -> None:
    """The point of the split -- the class does not carry the scan back.

    A sample rather than the full list: what this refuses is a
    compatibility shim quietly re-attaching the machinery to the class it
    was moved out of.
    """
    assert not hasattr(GenomicScoreImplementation, name)
    assert not hasattr(GenomicScoreImplementation, f"_{name}")


def test_scan_does_not_import_the_implementation_classes() -> None:
    """``scan`` sits BELOW ``impl``; the dependency runs one way only.

    ``impl`` imports ``scan`` to schedule its tasks.  If ``scan`` reached
    back for ``build_score_implementation_from_resource`` the package would
    have an import cycle, and the only reason it does not need to is that
    every use was for the ``.score`` a resource builds directly.
    """
    assert not hasattr(scan, "build_score_implementation_from_resource")
    assert not hasattr(scan, "GenomicScoreImplementation")


def test_the_render_accessors_stayed_on_the_class() -> None:
    """The other half of the split: the info page's accessors did not move.

    Out of scope for gain#1007 by name -- they are instance methods reading
    the resource, and the class keeps them.
    """
    for name in (
        "get_info", "get_statistics_info", "get_allele_display",
        "get_coverage_display", "get_fragment_display",
        "get_coverage_statistics", "get_allele_statistics",
    ):
        assert inspect.isfunction(getattr(GenomicScoreImplementation, name))
