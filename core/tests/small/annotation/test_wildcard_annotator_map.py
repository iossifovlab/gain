"""The wildcard map agrees with what the annotators declare.

``AnnotationConfigParser`` decides which annotator names accept a wildcard
``resource_id``, and which resource type each such wildcard selects, from a
list it owns.  The annotators themselves declare the resource types they
accept, on ``ACCEPTED_RESOURCE_TYPES``.  Those are two statements of
overlapping facts, and until now nothing checked that they agree.

The list stays a list -- deriving it from the annotators was considered and
rejected (``docs/adr/0028-wildcard-expandability-is-parser-policy.md``).
What these tests add is the guard that makes keeping it tolerable: the
agreement is pinned here rather than left to review.

Scoped to annotators registered from the ``gain.`` package on purpose.  A
third-party plugin registers through the same entry-point group and may
well declare accepted resource types; whether its name expands a wildcard
is GAIn's policy to state, not the plugin's, so an installed plugin must
not fail GAIn's own pin.
"""
# pylint: disable=W0621,C0116
import importlib
from collections.abc import Iterable
from importlib.metadata import entry_points

import pytest
from gain.annotation.annotation_config import AnnotationConfigParser
from gain.annotation.annotator_base import AnnotatorBase
from gain.genomic_resources.resource_types import (
    LEGACY_FRAGMENT_SCORE_TYPE,
    PREFERRED_ALLELE_SCORE_TYPE,
    PREFERRED_FRAGMENT_SCORE_TYPE,
    PREFERRED_POSITION_SCORE_TYPE,
)

#: The entry-point group every annotator registers under.
ENTRY_POINT_GROUP = "gain.annotation.annotators"


def _declared_resource_types() -> dict[str, tuple[str, ...]]:
    """Return each GAIn annotator name with the types its class declares.

    Keyed by the name a pipeline writes, not by class: several names share
    one class (``allele_score`` and ``allele_score_annotator``; the legacy
    fragment-score pair), and the map under test is keyed the same way.

    The declaration is read off the classes *defined in* the entry point's
    own module rather than off the factory's return type, which would mean
    building an annotator -- a resource, a repository and a pipeline -- to
    read a class attribute.  An annotator that declares nothing is kept,
    with an empty tuple, because the pin has something to say about it: a
    map entry naming it would be a wildcard for an annotator with no
    resource-type concept.
    """
    declarations: dict[str, tuple[str, ...]] = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if not entry.module.startswith("gain."):
            continue
        # Resolving the factory is what imports the module the classes are
        # then read from -- and a broken entry point should fail here,
        # loudly, rather than look like an annotator that declares nothing.
        entry.load()
        module = importlib.import_module(entry.module)
        declared = {
            obj.ACCEPTED_RESOURCE_TYPES
            for obj in vars(module).values()
            if isinstance(obj, type)
            and issubclass(obj, AnnotatorBase)
            and obj.__module__ == entry.module
            and obj.ACCEPTED_RESOURCE_TYPES
        }
        assert len(declared) <= 1, (
            f"'{entry.name}' resolves to {entry.module}, which defines more "
            f"than one annotator declaring accepted resource types "
            f"({sorted(declared)}); this pin cannot tell which of them the "
            f"name refers to."
        )
        declarations[entry.name] = declared.pop() if declared else ()
    return declarations


@pytest.fixture(scope="module")
def declarations() -> dict[str, tuple[str, ...]]:
    return _declared_resource_types()


def _pin_violations(
    declarations: dict[str, tuple[str, ...]],
    wildcard_types: dict[str, str],
    exempt: frozenset[str],
) -> list[str]:
    """Return one sentence per disagreement, or an empty list.

    Takes the two constants as arguments rather than reading them off the
    parser, so the tests below can feed it a deliberately broken pair and
    show that the pin bites.
    """
    violations = []
    for name, accepted in sorted(declarations.items()):
        if not accepted:
            continue
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
        # accepted ones: the preferred spelling leads the tuple by
        # contract, and membership would pass a map pointing at the
        # deprecated one -- a wildcard that answers only the resources
        # that have not migrated (gain#1266).
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
    names: Iterable[str], role: str,
    declarations: dict[str, tuple[str, ...]],
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
        elif not declarations[name]:
            violations.append(
                f"'{name}' is {role}, but its annotator declares no "
                f"ACCEPTED_RESOURCE_TYPES; it consumes no one typed "
                f"resource, so there is no type for a wildcard to select.")
    return violations


def test_every_declaring_annotator_is_mapped_or_exempt(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    violations = _pin_violations(
        declarations,
        AnnotationConfigParser.WILDCARD_RESOURCE_TYPES,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS)

    assert not violations, violations


def test_the_gene_set_annotator_is_the_only_exemption() -> None:
    # Stated as content, not just as "the pin passes": an exemption is a
    # decision that an annotator takes no wildcard, and the pin above
    # accepts any such decision. Adding a second one should have to come
    # through here, and say why beside the name (gain#1365 is the open
    # question about this one).
    exemptions = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS

    assert exemptions == {"gene_set_annotator"}


def test_a_map_value_naming_a_legacy_spelling_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # The silent miss gain#1266 closed, reintroduced: a wildcard keyed on
    # the deprecated spelling answers only the resources that still
    # declare it, in a repository that has migrated to the preferred one.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["fragment_score_annotator"] = LEGACY_FRAGMENT_SCORE_TYPE

    violations = _pin_violations(
        declarations, broken,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS)

    assert len(violations) == 1, violations
    assert "fragment_score_annotator" in violations[0]
    assert LEGACY_FRAGMENT_SCORE_TYPE in violations[0]
    assert PREFERRED_FRAGMENT_SCORE_TYPE in violations[0]


def test_an_annotator_that_is_both_mapped_and_exempt_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # The two constants answer one question between them, so an annotator
    # in both leaves the answer to whichever is consulted first -- and the
    # exemption reads as a decision that was made when it was not.
    broken = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "gene_score_annotator"}

    violations = _pin_violations(
        declarations, AnnotationConfigParser.WILDCARD_RESOURCE_TYPES, broken)

    assert len(violations) == 1, violations
    assert "gene_score_annotator" in violations[0]


def test_a_declaring_annotator_missing_from_the_map_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # The case a new score annotator lands in: since gain#1266 its
    # wildcard is refused outright rather than expanding to nothing, so
    # the fault is loud -- but only for whoever writes that pipeline
    # first, which is the reader least placed to fix it.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    del broken["gene_score_annotator"]

    violations = _pin_violations(
        declarations, broken,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS)

    assert len(violations) == 1, violations
    assert "gene_score_annotator" in violations[0]


def test_an_annotator_moved_from_the_map_to_the_exemptions_is_accepted(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # Deciding an annotator takes no wildcard is a decision the pin
    # accepts, not one it argues with; what it insists on is that the
    # decision be written down somewhere.
    without = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    del without["gene_score_annotator"]
    exempt = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "gene_score_annotator"}

    violations = _pin_violations(declarations, without, exempt)

    assert not violations, violations


def test_a_map_key_that_names_no_registered_annotator_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # What an entry left behind by a retirement looks like: the annotator
    # is gone, the wildcard policy for its name is not, and the name is
    # advertised in the refusal that lists who accepts a wildcard.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["np_score_annotator"] = PREFERRED_ALLELE_SCORE_TYPE

    violations = _pin_violations(
        declarations, broken,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS)

    assert len(violations) == 1, violations
    assert "np_score_annotator" in violations[0]


def test_a_map_key_whose_annotator_takes_no_typed_resource_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # A registered name, so the staleness check above passes it -- but
    # ``effect_annotator`` consumes gene models and a reference genome,
    # not one typed resource, so there is nothing for a wildcard to
    # select and the entry would advertise one that cannot work.
    broken = dict(AnnotationConfigParser.WILDCARD_RESOURCE_TYPES)
    broken["effect_annotator"] = PREFERRED_POSITION_SCORE_TYPE

    violations = _pin_violations(
        declarations, broken,
        AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS)

    assert len(violations) == 1, violations
    assert "effect_annotator" in violations[0]
    assert "ACCEPTED_RESOURCE_TYPES" in violations[0]


def test_an_exemption_that_names_no_registered_annotator_is_refused(
    declarations: dict[str, tuple[str, ...]],
) -> None:
    # An exemption outliving its annotator is quieter than a stale map
    # entry -- nothing reads it at runtime at all -- so it would sit there
    # claiming a decision about an annotator that no longer exists.
    broken = AnnotationConfigParser.WILDCARD_EXEMPT_ANNOTATORS | {
        "np_score_annotator"}

    violations = _pin_violations(
        declarations, AnnotationConfigParser.WILDCARD_RESOURCE_TYPES, broken)

    assert len(violations) == 1, violations
    assert "np_score_annotator" in violations[0]
