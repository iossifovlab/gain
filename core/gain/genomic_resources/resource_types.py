"""Resource ``type:`` values, and the spellings that mean the same thing.

Deliberately dependency-free and low in the import graph.  The equivalence
below is needed by ``repository`` (which applies the type predicate in SQL),
by ``genomic_scores`` and its implementations, by ``annotation_config`` and
by the web API -- and ``genomic_scores`` imports ``repository``, so a home
in the score layer could not be reached from the layer that needs it most.
That is not a detail: the review of gain#471 found that the SQL-side
predicate had been missed precisely because the helper was out of reach.

See ``docs/adr/0003-fragment-score-vocabulary.md``, superseded by
``docs/adr/0011-deprecate-cnv-collection-vocabulary.md``.
"""

#: The preferred resource ``type:`` for a fragment score.
PREFERRED_FRAGMENT_SCORE_TYPE = "fragment_score"

#: The deprecated resource ``type:`` for a fragment score.  Still accepted,
#: and still declared by repositories that have not migrated.
LEGACY_FRAGMENT_SCORE_TYPE = "cnv_collection"

#: The GAIn release in which every legacy fragment-score spelling stops
#: being accepted (gain#539).  Named in every deprecation warning: a notice
#: that does not say when it bites cannot be scheduled against.
LEGACY_VOCABULARY_REMOVAL_RELEASE = "2027.1.0"

#: The resource ``type:`` values that name a fragment score.
#:
#: Two spellings.  ``fragment_score`` is what a resource should declare, and
#: what the public GRR declares since the migration; ``cnv_collection`` is
#: what a repository that has not migrated declares.  It is deprecated and
#: stops being accepted in ``LEGACY_VOCABULARY_REMOVAL_RELEASE``; consuming
#: it warns, at the places that open a resource rather than here.
#:
#: A tuple rather than a set: it is used for membership, but also rendered
#: into user-facing messages and into SQL placeholders, and a set would
#: order them arbitrarily.  Preferred spelling first, so a message reads as
#: a recommendation.
FRAGMENT_SCORE_TYPES = (
    PREFERRED_FRAGMENT_SCORE_TYPE, LEGACY_FRAGMENT_SCORE_TYPE)


def deprecated_spelling_message(
    surface: str, legacy: str, preferred: str, *, found_in: str,
) -> str:
    """Return the warning text for one use of one legacy spelling.

    ``surface`` names the kind of configuration the spelling was written as
    (``"resource type"``, ``"annotator name"``, ``"parameter"``),
    ``found_in`` names where it was written -- a resource id, or an
    annotator within a pipeline.  Both are required because the stack at the
    point of the warning points into GAIn's own config parsing rather than
    at the YAML the reader has to edit, so the message must carry the
    location itself.

    A plain string rather than a logging call: the module that recognised
    the spelling logs it, so the record carries that module's logger name.
    """
    return (
        f"{found_in} uses deprecated {surface} '{legacy}'; "
        f"write '{preferred}' instead -- '{legacy}' stops being accepted "
        f"in GAIn {LEGACY_VOCABULARY_REMOVAL_RELEASE}"
    )


def equivalent_resource_types(resource_type: str) -> tuple[str, ...]:
    """Return every ``type:`` value denoting the same kind of resource.

    Only a fragment score has more than one spelling; every other type maps
    to itself, so a caller can filter by the result unconditionally without
    special-casing.

    Exists because filtering resources by an exact type string went wrong
    the moment a second spelling appeared: asking for ``fragment_score``
    matched nothing at all in a repository whose resources declare
    ``cnv_collection``.  An empty result is indistinguishable from "this
    repository has none of those", so the failure is silent -- a wrong
    answer rather than an error.
    """
    if resource_type in FRAGMENT_SCORE_TYPES:
        return FRAGMENT_SCORE_TYPES
    return (resource_type,)


def require_fragment_score_type(resource_type: str) -> str:
    """Return ``resource_type``, or raise if it names no fragment score.

    Lives here rather than at its one call site (the test-data builder's
    ``with_resource_type``) because ``builders.py`` sits four lines under
    pylint's 1500-line module ceiling, and because the rule it enforces is
    this module's to state.
    """
    if resource_type not in FRAGMENT_SCORE_TYPES:
        raise ValueError(
            f"{resource_type!r} does not name a fragment score; "
            f"expected one of {list(FRAGMENT_SCORE_TYPES)}")
    return resource_type
