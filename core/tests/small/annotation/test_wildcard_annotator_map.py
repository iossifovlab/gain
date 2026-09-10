"""The wildcard map agrees with what the annotators declare.

``AnnotationConfigParser`` decides which annotator names accept a wildcard
``resource_id``, and which resource type each such wildcard selects, from a
list it owns.  The annotators themselves declare the resource types they
accept, on ``ACCEPTED_RESOURCE_TYPES``.  Those are two statements of
overlapping facts, and until now nothing checked that they agree.

The list stays a list -- deriving it from the annotators was considered and
rejected (``docs/adr/0029-wildcard-expandability-is-parser-policy.md``).
What these tests add is the guard that makes keeping it tolerable: the
agreement is pinned here rather than left to review.

Scoped to annotators registered from the ``gain.`` package on purpose.  A
third-party plugin registers through the same entry-point group and may
well declare accepted resource types; whether its name expands a wildcard
is GAIn's policy to state, not the plugin's, so an installed plugin must
not fail GAIn's own pin.
"""
# pylint: disable=W0621,C0116
import sys
from collections.abc import Iterable, Mapping
from importlib.metadata import entry_points

import pytest
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.resource_types import (
    GENE_SCORE_TYPE,
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_ALLELE_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
    PREFERRED_POSITION_SCORE_TYPE,
)

#: The entry-point group every annotator registers under.
ENTRY_POINT_GROUP = "gain.annotation.annotators"

#: An annotator name with the class its wildcard policy is judged against,
#: or ``None`` for a registered name whose annotator declares no accepted
#: resource types.
Declarations = dict[str, type[AnnotatorBase] | None]


def _declaring_classes_in(module_name: str) -> set[type[AnnotatorBase]]:
    """Return the annotator classes ``module_name`` DEFINES that declare."""
    module = sys.modules[module_name]
    return {
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, AnnotatorBase)
        # Defined here, not imported here: every score annotator module
        # imports its base, and two import each other's constants.
        and obj.__module__ == module_name
        and obj.ACCEPTED_RESOURCE_TYPES
    }


def _declared_by_entry_point() -> Declarations:
    """Return each GAIn annotator name with the class that declares for it.

    Keyed by the name a pipeline writes, not by class: several names share
    one class (``allele_score`` and ``allele_score_annotator``; the legacy
    fragment-score pair), and the map under test is keyed the same way.

    The class is found in the entry point's own module rather than by
    calling the factory, which would mean building an annotator -- a
    resource, a repository and a pipeline -- to read a class attribute.
    A registered name whose annotator declares nothing is kept, as
    ``None``, because the pin has something to say about it: a map entry
    naming it would promise a wildcard for an annotator with no
    resource-type concept.

    Read from the entry points rather than through
    ``get_available_annotator_types``, which answers the neighbouring
    question of what this PROCESS can build: its registry is a mutable
    process-global that ``register_annotator_factory`` adds to, and what
    the map is policy about is what GAIn DECLARES.
    """
    declarations: Declarations = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if not entry.module.startswith("gain."):
            continue
        # Resolving the factory is what imports the module the classes are
        # then read from -- and a broken entry point should fail here,
        # loudly, rather than look like an annotator that declares nothing.
        entry.load()
        declared = _declaring_classes_in(entry.module)
        assert len(declared) <= 1, (
            f"'{entry.name}' resolves to {entry.module}, which defines more "
            f"than one annotator declaring accepted resource types "
            f"({sorted(cls.__name__ for cls in declared)}); this pin cannot "
            f"tell which of them the name refers to."
        )
        declarations[entry.name] = declared.pop() if declared else None
    return declarations


@pytest.fixture(scope="module")
def declarations() -> Declarations:
    return _declared_by_entry_point()


def _pin_violations(
    declarations: Declarations,
    wildcard_types: Mapping[str, str],
    exempt: frozenset[str],
) -> list[str]:
    """Return one sentence per disagreement, or an empty list.

    Takes the two constants as arguments rather than reading them off the
    parser, so the tests below can feed it a deliberately broken pair and
    show that the pin bites.
    """
    violations = []
    for name, annotator in sorted(declarations.items()):
        if annotator is None:
            continue
        accepted = annotator.ACCEPTED_RESOURCE_TYPES
        mapped = name in wildcard_types
        exempted = name in exempt
        if mapped and exempted:
            violations.append(
                f"annotator '{name}' is in WILDCARD_RESOURCE_TYPES and in "
                f"WILDCARD_EXEMPT_ANNOTATORS; the two answer one question "
                f"between them, so it belongs to exactly one.")
        elif not mapped and not exempted:
            violations.append(
                f"annotator '{name}' declares accepted resource types "
                f"{list(accepted)} but is neither in WILDCARD_RESOURCE_TYPES "
                f"nor in WILDCARD_EXEMPT_ANNOTATORS; a wildcard "
                f"'resource_id' written for it would be refused.")
        # Equality against the FIRST accepted type, not membership in the
        # accepted ones. For the fragment score's pair the two would agree
        # today, because `search_resources` expands whichever spelling it
        # is given through `equivalent_resource_types`. They do not agree
        # in general: search honours a narrower set of equivalences than
        # the annotators accept -- `GENE_SET_TYPES` is a pair search does
        # NOT relate -- so for such a pair, membership would pass a map
        # entry naming the non-preferred spelling and the wildcard would
        # answer only the resources declaring it.
        elif mapped and wildcard_types[name] != accepted[0]:
            violations.append(
                f"annotator '{name}' is mapped to resource type "
                f"'{wildcard_types[name]}' in WILDCARD_RESOURCE_TYPES, but "
                f"its preferred accepted type is '{accepted[0]}'.")
    violations.extend(
        _stale_entries(
            wildcard_types, "mapped in WILDCARD_RESOURCE_TYPES",
            declarations))
    violations.extend(
        _stale_entries(
            exempt, "listed in WILDCARD_EXEMPT_ANNOTATORS", declarations))
    return violations


def _stale_entries(
    names: Iterable[str], role: str, declarations: Declarations,
) -> list[str]:
    """Return one sentence per name no GAIn annotator declaration backs.

    Two ways for an entry to be unbacked, and they read differently to
    whoever meets the failure: the annotator is not registered at all, or
    it is registered and consumes no typed resource -- an entry that
    promises a wildcard nothing can answer.
    """
    violations = []
    for name in sorted(names):
        if name not in declarations:
            violations.append(
                f"'{name}' is {role}, but no annotator of that name is "
                f"registered under {ENTRY_POINT_GROUP}.")
        elif declarations[name] is None:
            violations.append(
                f"'{name}' is {role}, but its annotator declares no "
                f"ACCEPTED_RESOURCE_TYPES; it consumes no one typed "
                f"resource, so there is no type for a wildcard to select.")
    return violations


def _violations_with(
    declarations: Declarations, *,
    types: Mapping[str, str] | None = None,
    exempt: frozenset[str] | None = None,
) -> list[str]:
    """Run the pin with one constant replaced and the other left real.

    So that each test below shows the mutation it is about and nothing
    else -- naming the constant a test does NOT touch, in full, is what
    made the seven of them hard to tell apart.
    """
    return _pin_violations(
        declarations,
        AnnotationConfigParser.WILDCARD_RESOURCE_TYPES
        if types is None else types,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS
        if exempt is None else exempt)


def test_every_declaring_annotator_is_mapped_or_exempt(
    declarations: Declarations,
) -> None:
    violations = _violations_with(declarations)

    assert not violations, violations


def test_the_walk_sees_every_declaring_annotator_class_gain_has_imported(
    declarations: Declarations,
) -> None:
    """The pin above is only as wide as the walk that feeds it.

    A class is found in the module its entry point names, so an annotator
    whose class lives in a shared base module and is merely imported by
    its factory's module would record nothing and slip past the
    mapped-or-exempt requirement silently -- the one fault the pin exists
    to catch.  This is the floor: every declaring annotator class GAIn has
    loaded must be one the walk attributed to some registered name.

    Judged against what is in ``sys.modules``, so it grows with whatever
    else the session imported.  That can only widen the check, and a
    declaring class no registered name resolves to is worth failing on
    however it got loaded.
    """
    seen = {cls for cls in declarations.values() if cls is not None}
    loaded = {
        cls
        for module_name in list(sys.modules)
        if module_name.startswith("gain.") and sys.modules[module_name]
        for cls in _declaring_classes_in(module_name)
    }

    # A subset assertion passes on an empty left side, and this one is
    # the guard against a walk that finds nothing -- so it has to say
    # that it found something first.
    assert loaded, "the sweep found no declaring annotator class at all"
    assert loaded <= seen, sorted(
        cls.__name__ for cls in loaded - seen)


def test_the_gene_set_annotator_is_the_only_exemption() -> None:
    # Stated as content, not just as "the pin passes": an exemption is a
    # decision that an annotator takes no wildcard, and the pin above
    # accepts any such decision. Adding a second one should have to come
    # through here, and say why beside the name (gain#1365 is the open
    # question about this one).
    exemptions = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS

    assert exemptions == {"gene_set_annotator"}


def test_the_wildcard_map_cannot_be_edited_in_place() -> None:
    # Pins the CHOICE of `MappingProxyType`, which a revert to a plain
    # dict would undo silently: a test reaching for `monkeypatch.setitem`
    # would then leave the parser corrupted for every test after it.
    with pytest.raises(TypeError):
        AnnotationConfigParser.WILDCARD_RESOURCE_TYPES[  # type: ignore[index]
            "position_score"] = GENE_SCORE_TYPE


def test_a_map_value_naming_a_legacy_spelling_is_refused(
    declarations: Declarations,
) -> None:
    # Worth a test even though it is behaviour-neutral for THIS pair as
    # it stands -- search expands either fragment-score spelling into
    # both. Refused anyway, for the reason `_pin_violations` gives above.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["fragment_score_annotator"] = LEGACY_FRAGMENT_SCORE_TYPE

    violations = _violations_with(declarations, types=broken)

    assert len(violations) == 1, violations
    assert "fragment_score_annotator" in violations[0]
    assert LEGACY_FRAGMENT_SCORE_TYPE in violations[0]
    assert PREFERRED_FRAGMENT_SCORE_TYPE in violations[0]


def test_an_annotator_that_is_both_mapped_and_exempt_is_refused(
    declarations: Declarations,
) -> None:
    # The two constants answer one question between them, so an annotator
    # in both leaves the answer to whichever is consulted first -- and the
    # exemption reads as a decision that was made when it was not.
    broken = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "gene_score_annotator"}

    violations = _violations_with(declarations, exempt=broken)

    assert len(violations) == 1, violations
    assert "gene_score_annotator" in violations[0]


def test_a_declaring_annotator_missing_from_the_map_is_refused(
    declarations: Declarations,
) -> None:
    # The case a new score annotator lands in: since gain#1266 its
    # wildcard is refused outright rather than expanding to nothing, so
    # the fault is loud -- but only for whoever writes that pipeline
    # first, which is the reader least placed to fix it.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    del broken["gene_score_annotator"]

    violations = _violations_with(declarations, types=broken)

    assert len(violations) == 1, violations
    assert "gene_score_annotator" in violations[0]


def test_an_annotator_moved_from_the_map_to_the_exemptions_is_accepted(
    declarations: Declarations,
) -> None:
    # Deciding an annotator takes no wildcard is a decision the pin
    # accepts, not one it argues with; what it insists on is that the
    # decision be written down somewhere.
    without = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    del without["gene_score_annotator"]
    exempt = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "gene_score_annotator"}

    violations = _violations_with(declarations, types=without, exempt=exempt)

    assert not violations, violations


def test_a_map_key_that_names_no_registered_annotator_is_refused(
    declarations: Declarations,
) -> None:
    # What an entry left behind by a retirement looks like: the annotator
    # is gone, the wildcard policy for its name is not, and the name is
    # advertised in the refusal that lists who accepts a wildcard.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["np_score_annotator"] = PREFERRED_ALLELE_SCORE_TYPE

    violations = _violations_with(declarations, types=broken)

    assert len(violations) == 1, violations
    assert "np_score_annotator" in violations[0]


def test_a_map_key_whose_annotator_takes_no_typed_resource_is_refused(
    declarations: Declarations,
) -> None:
    # A registered name, so the staleness check above passes it -- but
    # ``effect_annotator`` consumes gene models and a reference genome,
    # not one typed resource, so there is nothing for a wildcard to
    # select and the entry would advertise one that cannot work.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["effect_annotator"] = PREFERRED_POSITION_SCORE_TYPE

    violations = _violations_with(declarations, types=broken)

    assert len(violations) == 1, violations
    assert "effect_annotator" in violations[0]
    assert "ACCEPTED_RESOURCE_TYPES" in violations[0]


def test_an_exemption_that_names_no_registered_annotator_is_refused(
    declarations: Declarations,
) -> None:
    # An exemption outliving its annotator is quieter than a stale map
    # entry -- nothing reads it at runtime at all -- so it would sit there
    # claiming a decision about an annotator that no longer exists.
    broken = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "np_score_annotator"}

    violations = _violations_with(declarations, exempt=broken)

    assert len(violations) == 1, violations
    assert "np_score_annotator" in violations[0]
