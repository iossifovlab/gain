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
from typing import Protocol

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
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_position_score,
    an_allele_score,
)
from gain.templates import get_jinja_env
from jinja2 import nodes

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


class _Builds(Protocol):
    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource: ...


#: Every resource-type spelling ``core/pyproject.toml`` routes to this
#: package, the kind each names, and a builder for a resource of it.  No
#: row names the base.  ``cnv_collection`` is the legacy spelling of a
#: fragment score, kept registered until 2027.1.0 (ADR 0011).
SCORE_TYPES: list[tuple[str, type, Callable[[], _Builds]]] = [
    ("position_score", PositionScoreImplementation, a_position_score),
    ("allele_score", AlleleScoreImplementation, an_allele_score),
    ("fragment_score", FragmentScoreImplementation, a_fragment_score),
    ("cnv_collection", FragmentScoreImplementation,
     lambda: a_fragment_score().with_resource_type("cnv_collection")),
]

#: Each render accessor a kind's template calls, and the ONE class that
#: defines it.  A template reaches these by name, so they are a published
#: surface -- and each belongs to exactly one kind: the section it fills
#: exists on that kind's page and no other (gain#1210).
ACCESSOR_OWNERS: dict[str, type] = {
    "get_coverage_statistics": PositionScoreImplementation,
    "get_coverage_display": PositionScoreImplementation,
    "get_coverage_segment_lengths_image_filename": PositionScoreImplementation,
    "_render_genome": PositionScoreImplementation,
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

#: The sections of a genomic-score page: the blocks of the shared
#: template that are slots, each filled by exactly one kind.
SECTIONS = frozenset({"coverage", "alleles", "fragments"})


def _public_names_defined_in(module: types.ModuleType) -> set[str]:
    """The public names ``module`` DEFINES -- not the ones it imports."""
    return {
        name for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    }


def _module_stem(module: types.ModuleType) -> str:
    return module.__name__.rsplit(".", maxsplit=1)[1]


def _template_ast(template_name: str) -> nodes.Template:
    """The template, found and parsed by the environment that renders it.

    Through the environment's loader rather than a path of this test's
    own, so a template served the way a provider's would be is found
    the same way the page is.
    """
    env = get_jinja_env()
    assert env.loader is not None
    source, _, _ = env.loader.get_source(env, template_name)
    return env.parse(source, template_name)


def _extended(ast: nodes.Template) -> set[str]:
    return {
        node.template.value
        for node in ast.find_all(nodes.Extends)
        if isinstance(node.template, nodes.Const)
    }


def _blocks(ast: nodes.Template) -> dict[str, bool]:
    """Each block the template defines, and whether it has a body."""
    return {block.name: bool(block.body) for block in ast.find_all(nodes.Block)}


def _accessor_calls(ast: nodes.Template) -> set[str]:
    """The ``impl.<name>(...)`` calls: the accessors a template reads.

    A read through ``impl.score`` is the score's own surface and is not
    one of these.
    """
    return {
        call.node.attr
        for call in ast.find_all(nodes.Call)
        if isinstance(call.node, nodes.Getattr)
        and isinstance(call.node.node, nodes.Name)
        and call.node.node.name == "impl"
    }


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
    assert isinstance(resolved, type)
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
    build: Callable[[], _Builds],
    tmp_path: pathlib.Path,
) -> None:
    """A caller holding a resource gets the kind its type name gets.

    Asserted as the exact class, which is stronger than the strict
    subclass the entry point row settles for: a kind is a subclass of
    the base, and a wrong kind would pass ``isinstance``.
    """
    resource = build().build_resource(tmp_path)
    assert resource.get_type() == resource_type

    implementation = build_score_implementation_from_resource(resource)

    # The exact class, on purpose: isinstance would accept the base.
    # pylint: disable=unidiomatic-typecheck
    assert type(implementation) is expected


def test_the_factory_refuses_a_type_that_is_no_kind(
    tmp_path: pathlib.Path,
) -> None:
    """A spelling that names no kind is refused, not defaulted.

    The ladder ends in a refusal, as ``build_score_from_resource``'s
    does, so a new kind must be added to the factory rather than falling
    through to whichever branch is last.
    """
    # Off a bare directory: every score builder validates its spelling.
    setup_directories(
        tmp_path, {"res": {"genomic_resource.yaml": "type: not_a_score\n"}})
    resource = build_filesystem_test_repository(tmp_path).get_resource("res")

    with pytest.raises(ValueError, match="not_a_score"):
        build_score_implementation_from_resource(resource)


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


@pytest.mark.parametrize("kind", KIND_CLASSES, ids=lambda kind: kind.__name__)
def test_each_kind_template_fills_one_section_with_its_own_accessors(
    kind: type[GenomicScoreImplementation],
) -> None:
    """A kind's template is its section, and calls its class for it.

    Three claims off the parsed template.  It extends the shared page.
    It defines exactly one block, and that block has a body -- an empty
    override is how a kind used to BLANK a section the shared template
    rendered for everyone (gain#1118, gain#1127), and with the shared
    template rendering none there is nothing left to blank.  And every
    ``impl.<name>()`` it calls is defined on the kind itself: the
    section a template fills exists on this kind's page and no other,
    so its accessors have no reason to be anywhere else (gain#1210).
    """
    ast = _template_ast(kind.template_name)

    assert _extended(ast) == {"genomic_score.jinja"}
    blocks = _blocks(ast)
    assert len(blocks) == 1, blocks
    assert all(blocks.values()), blocks

    called = _accessor_calls(ast)
    assert called, "a section that reads nothing off its class"
    assert called <= set(vars(kind)), called - set(vars(kind))


def test_the_shared_template_renders_no_section_body_itself() -> None:
    """Every section is a slot, filled by exactly one kind's template.

    The shared page used to fill Coverage by default and leave the
    other two empty, so a kind added later inherited a Coverage section
    whether or not it was coverage-scanned, and the only symptom was a
    page reading "not computed" forever (gain#1116).  With every section
    empty here, the kind -> section relation is which template a class
    names -- and no accessor the shared page calls is one the base
    class lacks.
    """
    blocks = _blocks(_template_ast("genomic_score.jinja"))
    assert set(blocks) >= SECTIONS
    assert {name for name in SECTIONS if blocks[name]} == set()

    called = _accessor_calls(_template_ast("genomic_score.jinja"))
    assert called <= set(vars(GenomicScoreImplementation)), called

    filled = {
        name
        for kind in KIND_CLASSES
        for name in _blocks(_template_ast(kind.template_name))
    }
    assert filled == SECTIONS


@pytest.mark.parametrize("name", SHARED_PROTOCOL)
def test_the_shared_protocol_is_defined_once_on_the_base(name: str) -> None:
    """What every kind answers alike is written once.

    The kinds used to repeat five of these as ``super()`` delegations,
    which said nothing and hid, under a pylint disable, the question of
    whether a kind overrides anything at all.  It does not.
    """
    assert name in vars(GenomicScoreImplementation)
    assert [cls for cls in KIND_CLASSES if name in vars(cls)] == []
