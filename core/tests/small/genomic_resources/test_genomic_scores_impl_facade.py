"""What the ``genomic_scores_impl`` package promises its importers.

The package's own docstring says what the split is for; these pin the
parts of it that other code depends on and that nothing else would catch.

Every name list here is DERIVED from the modules and compared against a
declaration, never transcribed. A hand-written list of the machinery would
only ever drift too small -- a function added to ``scan`` and forgotten
here fails nothing -- which is the opposite of what these tests are for.
"""
import types

import pytest
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.implementations import genomic_scores_impl
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
    impl,
    scan,
)

#: The accessors the info-page templates call. ``genomic_score.jinja``
#: reaches these by name, so they are a published surface too -- and the
#: half of the class gain#1007 deliberately did NOT move (gain#1037).
RENDER_ACCESSORS = (
    "get_info", "get_statistics_info",
    "get_allele_display", "get_coverage_display", "get_fragment_display",
    "get_coverage_statistics", "get_allele_statistics",
)


def _public_names_defined_in(module: types.ModuleType) -> set[str]:
    """The public names ``module`` DEFINES -- not the ones it imports."""
    return {
        name for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    }


def test_the_facade_publishes_exactly_the_three_documented_names() -> None:
    """The acceptance criterion of gain#1007, and not a circular one.

    Compares ``__all__`` against what the package actually re-exports, so
    a fourth name added to one and not the other fails here -- rather than
    comparing the literal to itself.
    """
    assert _public_names_defined_in(impl) == set(genomic_scores_impl.__all__)
    assert sorted(genomic_scores_impl.__all__) == [
        "FragmentScoreImplementation",
        "GenomicScoreImplementation",
        "build_score_implementation_from_resource",
    ]


@pytest.mark.parametrize("name", sorted(genomic_scores_impl.__all__))
def test_each_exported_name_is_the_one_impl_defines(name: str) -> None:
    """The facade re-exports; it does not redefine.

    ``__module__`` is pinned because that is what pickle and the task
    graph write down when they name a class.
    """
    exported = getattr(genomic_scores_impl, name)
    assert exported is getattr(impl, name)
    assert exported.__module__ == impl.__name__


@pytest.mark.parametrize("resource_type", ["position_score", "allele_score"])
def test_the_entry_point_of_each_score_type_still_loads(
    resource_type: str,
) -> None:
    """These two entry points resolve through the facade after the move.

    ``get_resource_implementation_builder`` is what a repository calls the
    first time it meets a resource of the type, and it is the call that
    performs ``EntryPoint.load()`` -- so a module path this rename left
    stale fails there, not at import.

    Only two of the four rows, because the fragment pair is already
    pinned, and pinned harder: ``test_fragment_score_vocabulary`` and
    ``test_fragment_score_config_surface`` assert the same resolution, and
    the latter also parses ``core/pyproject.toml`` to catch a source
    rename that ``importlib.metadata`` reads past until a reinstall.
    """
    assert get_resource_implementation_builder(resource_type) \
        is GenomicScoreImplementation


def test_scan_publishes_the_machinery_it_declares() -> None:
    """``scan.__all__`` is the whole of what ``scan`` defines publicly.

    Derived, so it catches the drift in both directions: a function added
    to the module and left out of ``__all__``, and a name in ``__all__``
    that no longer exists.
    """
    assert _public_names_defined_in(scan) == set(scan.__all__)


@pytest.mark.parametrize("name", sorted(scan.__all__))
def test_the_machinery_is_module_level_and_off_the_class(name: str) -> None:
    """Each published name is a plain function, and the class has none.

    Two halves of one claim. A plain function is what a task body should
    be -- nothing about scanning a region needs an implementation instance
    -- and the class must not carry the machinery back under either
    spelling, which is how a compatibility shim would creep in.
    """
    published = getattr(scan, name)
    assert isinstance(published, (types.FunctionType, type))

    assert not hasattr(GenomicScoreImplementation, name)
    assert not hasattr(GenomicScoreImplementation, f"_{name}")


@pytest.mark.parametrize("name", RENDER_ACCESSORS)
def test_the_render_accessors_stayed_on_the_class(name: str) -> None:
    """The other half of the split did not move (gain#1037).

    ``in vars(...)`` rather than ``getattr``: these names all exist on
    bases too, so an inherited lookup would keep passing after the class
    stopped defining its own.
    """
    assert name in vars(GenomicScoreImplementation)
