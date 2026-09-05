"""What the ``genomic_scores_impl`` package promises its importers.

The package's own docstring says what the layout is for; these pin the
parts of it that other code depends on and that nothing else would catch.

Every name list here is DERIVED from the modules and compared against a
declaration, never transcribed. A hand-written list of the machinery would
only ever drift too small -- a function added to ``scan`` and forgotten
here fails nothing -- which is the opposite of what these tests are for.
"""
import pathlib
import types
from collections.abc import Callable

import pytest
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.implementations import genomic_scores_impl
from gain.genomic_resources.implementations.genomic_scores_impl import (
    AlleleScoreImplementation,
    FragmentScoreImplementation,
    GenomicScoreImplementation,
    PositionScoreImplementation,
    allele,
    base,
    build_score_implementation_from_resource,
    builders,
    fragment,
    position,
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)

#: Each published name and the module that defines it: one module per
#: kind, the kind-neutral base, and the factory that picks a kind.  The
#: layout mirrors ``genomic_resources/genomic_scores/`` (gain#1210).
DEFINING_MODULES: dict[str, types.ModuleType] = {
    "GenomicScoreImplementation": base,
    "PositionScoreImplementation": position,
    "AlleleScoreImplementation": allele,
    "FragmentScoreImplementation": fragment,
    "build_score_implementation_from_resource": builders,
}

#: The concrete classes, one per kind.
KIND_CLASSES = (
    PositionScoreImplementation,
    AlleleScoreImplementation,
    FragmentScoreImplementation,
)

#: Every resource-type spelling ``core/pyproject.toml`` routes to this
#: package, the kind each names, and a builder for a resource of it.
#: ``cnv_collection`` is the legacy spelling of a fragment score, kept
#: registered until 2027.1.0 (ADR 0011).
SCORE_TYPES: list[tuple[str, type, Callable[
    [pathlib.Path], GenomicResource]]] = [
    ("position_score", PositionScoreImplementation, lambda tmp_path: (
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  pos_end  score
            chr1   10         20       0.1
        """)
        .build_resource(tmp_path))),
    ("allele_score", AlleleScoreImplementation, lambda tmp_path: (
        an_allele_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  score
            chr1   10         A          G            0.1
        """)
        .build_resource(tmp_path))),
    ("fragment_score", FragmentScoreImplementation, lambda tmp_path: (
        a_fragment_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  pos_end  score
            chr1   10         20       0.1
        """)
        .build_resource(tmp_path))),
    ("cnv_collection", FragmentScoreImplementation, lambda tmp_path: (
        a_fragment_score()
        .with_resource_type("cnv_collection")
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  pos_end  score
            chr1   10         20       0.1
        """)
        .build_resource(tmp_path))),
]

#: Each render accessor a kind's template calls, and the ONE class that
#: defines it.  A template reaches these by name, so they are a published
#: surface -- and each belongs to exactly one kind: the section it fills
#: exists on that kind's page and no other (gain#1210).
ACCESSOR_OWNERS: dict[str, type] = {
    "get_allele_statistics": AlleleScoreImplementation,
    "get_allele_display": AlleleScoreImplementation,
    "get_allele_insertion_lengths_image_filename": AlleleScoreImplementation,
    "get_allele_deletion_lengths_image_filename": AlleleScoreImplementation,
    "get_allele_complex_grid_image_filename": AlleleScoreImplementation,
    "get_fragment_statistics": FragmentScoreImplementation,
    "get_fragment_display": FragmentScoreImplementation,
    "get_fragment_lengths_image_filename": FragmentScoreImplementation,
}

#: What every kind answers identically, so it is defined once, on the
#: base, and no kind repeats it -- not even as a delegation to ``super``.
SHARED_PROTOCOL = (
    "get_info", "get_statistics_info",
    "create_statistics_build_tasks",
    "calc_info_hash", "calc_statistics_hash",
    "files",
)


def _public_names_defined_in(module: types.ModuleType) -> set[str]:
    """The public names ``module`` DEFINES -- not the ones it imports."""
    return {
        name for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    }


def _module_stem(module: types.ModuleType) -> str:
    return module.__name__.rsplit(".", maxsplit=1)[1]


def test_the_package_is_laid_out_one_module_per_kind() -> None:
    """The file layout is the defining modules, the scan, and the facade.

    Derived from ``DEFINING_MODULES`` so that a module added to the
    package without a published name -- or ``impl.py`` coming back --
    fails here.
    """
    package_dir = pathlib.Path(genomic_scores_impl.__file__).parent
    assert {path.stem for path in package_dir.glob("*.py")} == {
        *(_module_stem(module) for module in DEFINING_MODULES.values()),
        "scan",
        "__init__",
    }


def test_the_facade_publishes_exactly_the_documented_names() -> None:
    """The acceptance criterion of gain#1007, and not a circular one.

    Compares ``__all__`` against what the modules actually define, so a
    name added to one and not the other fails here -- rather than
    comparing the literal to itself.
    """
    defined = set().union(*(
        _public_names_defined_in(module)
        for module in DEFINING_MODULES.values()
    ))
    assert defined == set(genomic_scores_impl.__all__)
    assert set(genomic_scores_impl.__all__) == set(DEFINING_MODULES)


@pytest.mark.parametrize("name", sorted(DEFINING_MODULES))
def test_each_exported_name_is_the_one_its_module_defines(name: str) -> None:
    """The facade re-exports; it does not redefine.

    ``__module__`` is pinned because that is what pickle and the task
    graph write down when they name a class.
    """
    exported = getattr(genomic_scores_impl, name)
    module = DEFINING_MODULES[name]
    assert exported is getattr(module, name)
    assert exported.__module__ == module.__name__


@pytest.mark.parametrize(
    ("resource_type", "expected"),
    [(resource_type, expected) for resource_type, expected, _ in SCORE_TYPES],
)
def test_the_entry_point_of_each_score_type_resolves_to_its_kind(
    resource_type: str, expected: type,
) -> None:
    """Every score entry point resolves, through the facade, to a kind.

    ``get_resource_implementation_builder`` is what a repository calls the
    first time it meets a resource of the type, and it is the call that
    performs ``EntryPoint.load()`` -- so a module path a rename left
    stale fails there, not at import.

    The class each resolves to is asserted, not merely that all resolve:
    the kinds render different pages, and the page a kind gets is decided
    here (gain#1105).  And it is a STRICT subclass of the base: nothing
    instantiates the base for a real resource, since it names no
    kind's template (gain#1210).

    The fragment pair is also pinned by ``test_fragment_score_vocabulary``
    and ``test_fragment_score_config_surface``; the latter parses
    ``core/pyproject.toml`` too, to catch a source rename that
    ``importlib.metadata`` reads past until a reinstall.
    """
    resolved = get_resource_implementation_builder(resource_type)
    assert resolved is expected
    assert issubclass(resolved, GenomicScoreImplementation)
    assert resolved is not GenomicScoreImplementation


@pytest.mark.parametrize(
    ("resource_type", "expected", "build"),
    [
        pytest.param(
            *row, id=row[0],
            # Opening a legacy-spelled resource warns, and this row is
            # here to exercise that spelling.
            marks=([pytest.mark.legacy_vocabulary]
                   if row[0] == "cnv_collection" else []),
        )
        for row in SCORE_TYPES
    ],
)
def test_the_factory_picks_the_same_kind_the_entry_point_does(
    resource_type: str, expected: type,
    build: Callable[[pathlib.Path], GenomicResource],
    tmp_path: pathlib.Path,
) -> None:
    """A caller holding a resource gets the kind its type name gets.

    Asserted as the exact class, which is stronger than the strict
    subclass the entry point row settles for: the factory has a default
    branch, and a spelling that falls through it would still be SOME
    kind -- just not the one whose page the resource should render.
    """
    resource = build(tmp_path)
    assert resource.get_type() == resource_type

    implementation = build_score_implementation_from_resource(resource)

    assert type(implementation) is expected


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


@pytest.mark.parametrize("name", sorted(ACCESSOR_OWNERS))
def test_each_render_accessor_is_defined_on_exactly_its_kind(
    name: str,
) -> None:
    """A kind's section accessors live on that kind, and nowhere else.

    ``in vars(...)`` rather than ``getattr``: an inherited lookup would
    keep passing after an accessor drifted back onto the base, which is
    the one place it must not be -- a base that answers for one kind's
    section hands every kind that section (gain#1210).
    """
    owners = [
        cls for cls in (GenomicScoreImplementation, *KIND_CLASSES)
        if name in vars(cls)
    ]
    assert owners == [ACCESSOR_OWNERS[name]]


@pytest.mark.parametrize("name", SHARED_PROTOCOL)
def test_the_shared_protocol_is_defined_once_on_the_base(name: str) -> None:
    """What every kind answers alike is written once.

    The kinds used to repeat five of these as ``super()`` delegations,
    which said nothing and hid, under a pylint disable, the question of
    whether a kind overrides anything at all.  It does not.
    """
    assert name in vars(GenomicScoreImplementation)
    assert [cls for cls in KIND_CLASSES if name in vars(cls)] == []
