"""Resource ``type:`` values, and the spellings that mean the same thing.

Deliberately dependency-free and low in the import graph.  The equivalence
below is needed by ``repository`` (which applies the type predicate in SQL),
by ``genomic_scores`` and its implementations, by ``annotation_config`` and
by the web API -- and ``genomic_scores`` imports ``repository``, so a home
in the score layer could not be reached from the layer that needs it most.
That is not a detail: the review of gain#471 found that the SQL-side
predicate had been missed precisely because the helper was out of reach.

See ``docs/adr/0003-fragment-score-vocabulary.md``.
"""

#: The resource ``type:`` values that name a fragment score.
#:
#: Two spellings, both permanent.  ``fragment_score`` is what a resource
#: should declare, and what the public GRR declares since the migration;
#: ``cnv_collection`` is what a repository that has not migrated declares
#: and is therefore NOT deprecated.  Any first-party resources still on it
#: are tracked in ``iossifovlab/grr``#19, and third-party repositories
#: answer to no migration at all, so the legacy spelling outlives this
#: module's memory of why.
#:
#: A tuple rather than a set: it is used for membership, but also rendered
#: into user-facing messages and into SQL placeholders, and a set would
#: order them arbitrarily.  Preferred spelling first, so a message reads as
#: a recommendation.
FRAGMENT_SCORE_TYPES = ("fragment_score", "cnv_collection")


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
