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
# `from gain import logging`, not the stdlib module: the shim bootstraps the
# TRACE / USER_INFO levels and is what every gain module is required to use
# (gain#373, pinned by `test_no_gain_module_uses_stdlib_logging_directly`).
# It is the only import here, and it imports nothing from this layer.
from gain import logging

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


#: Every deprecation message already announced by this process.
#:
#: Keyed by the message itself, so two announcements collapse exactly when
#: they would have printed the same line -- a different resource id or a
#: different surface is a different offender and is still announced.
_ANNOUNCED_DEPRECATIONS: set[str] = set()


def warn_deprecated_spelling(
    logger: logging.Logger,
    surface: str, legacy: str, preferred: str, *, found_in: str,
) -> None:
    """Announce one legacy spelling once per offender, per process.

    The seams that recognise a legacy spelling are not once-per-offender on
    their own.  ``FragmentScore.__init__`` looked like it was -- until the
    statistics scan, which rebuilds the score inside every min/max and
    histogram task: ``grr_manage repo-repair`` over an hg38-scale resource
    re-opens it once per region, so an unguarded warning there prints
    thousands of identical lines for a single offender.  That is the noise
    the deprecation was supposed to avoid, and it hides the other offenders
    behind it.

    Deduplicating on the rendered message keeps the property that matters
    -- every distinct offender is named -- without asking each call site to
    know how often it runs.  The scope is the process: a multiprocess task
    run announces once per worker, which is bounded by the worker count
    rather than by the task count.

    Tests reset the set through :func:`reset_deprecation_notices`, so an
    assertion never depends on what ran before it.
    """
    message = deprecated_spelling_message(
        surface, legacy, preferred, found_in=found_in)
    if message in _ANNOUNCED_DEPRECATIONS:
        return
    _ANNOUNCED_DEPRECATIONS.add(message)
    logger.warning("%s", message)


def reset_deprecation_notices() -> None:
    """Forget what this process has already announced.

    Exists for tests: the announced-set is process-wide, and a test that
    asserts a warning fired must not depend on whether an earlier test in
    the same worker already consumed it.  ``core/tests/conftest.py`` calls
    this before every test.
    """
    _ANNOUNCED_DEPRECATIONS.clear()


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
