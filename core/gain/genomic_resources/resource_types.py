"""Resource ``type:`` values: which spellings are accepted, and which mean
the same thing.

Two different relations live here, and they are not the same.
``fragment_score`` and ``cnv_collection`` are *equivalent* -- either
resolves to the same thing, one is merely deprecated.  ``np_score`` is
*retired*: it is no longer accepted at all, and it was never equivalent to
its replacement, since it carried a different default read mode.  The
first relation is served by :func:`equivalent_resource_types`, the second
by :func:`reject_retired_resource`.

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
from typing import Protocol

# `from gain import logging`, not the stdlib module: the shim bootstraps the
# TRACE / USER_INFO levels and is what every gain module is required to use
# (gain#373, pinned by `test_no_gain_module_uses_stdlib_logging_directly`).
# It is the only gain import here, and it imports nothing from this layer;
# `typing` is stdlib and pulls in nothing at all.
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

#: The resource ``type:`` for an allele score.
PREFERRED_ALLELE_SCORE_TYPE = "allele_score"

#: The retired resource ``type:`` that used to name an allele score.
#:
#: Deprecated since 2024-11 and removed in
#: ``RETIRED_VOCABULARY_REMOVAL_RELEASE`` (gain#920).  Unlike the fragment
#: score's legacy spelling above this one is no longer accepted, so it
#: survives here only to be recognised and refused with a message that
#: names the replacement.
RETIRED_ALLELE_SCORE_TYPE = "np_score"

#: The GAIn release that removed ``np_score`` (gain#781, announced in
#: gain#918).  Named in the refusal so a reader who meets it in an old
#: environment can tell which upgrade changed under them.
RETIRED_VOCABULARY_REMOVAL_RELEASE = "2026.8.5"

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
#:
#: A dict rather than a set because insertion order is what makes the cap
#: below evictable; the values carry nothing.
_ANNOUNCED_DEPRECATIONS: dict[str, None] = {}

#: How many distinct announcements to remember before evicting the oldest.
#:
#: The set is process-wide and never goes out of scope, and what lands in it
#: is caller-supplied: ``found_in`` carries a resource id read verbatim from
#: a repository, or an annotator id derived from a posted pipeline.  A
#: long-lived web worker builds pipelines from request bodies, so an
#: unbounded set would let a caller ratchet the process's memory by naming
#: many distinct offenders once each -- retained forever, because nothing
#: here can know the pipeline was rejected or evicted.
#:
#: Chosen well above any real repository's count of legacy-typed resources,
#: so eviction never costs a duplicate line in the case this exists for.
#: Past the cap the notice is still correct, merely repeatable.
_ANNOUNCEMENT_MEMORY = 4096


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

    What is remembered is capped at ``_ANNOUNCEMENT_MEMORY`` distinct
    messages, oldest evicted first: ``found_in`` is caller-supplied, so an
    uncapped memory would grow with what a long-lived process has been
    asked to parse rather than with the repository it serves.

    Tests reset the set through :func:`reset_deprecation_notices`, so an
    assertion never depends on what ran before it.
    """
    message = deprecated_spelling_message(
        surface, legacy, preferred, found_in=found_in)
    if message in _ANNOUNCED_DEPRECATIONS:
        return
    if len(_ANNOUNCED_DEPRECATIONS) >= _ANNOUNCEMENT_MEMORY:
        # Oldest first: `dict` preserves insertion order, and the entry
        # least recently *announced* is the one whose offender the reader
        # is least likely to still be scrolling past.
        del _ANNOUNCED_DEPRECATIONS[next(iter(_ANNOUNCED_DEPRECATIONS))]
    _ANNOUNCED_DEPRECATIONS[message] = None
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


def retired_resource_type_message(*, found_in: str) -> str:
    """Return the refusal text for one use of the retired ``np_score``.

    ``found_in`` names where the type was written -- a resource id -- for
    the same reason the deprecation messages above carry it: the stack at
    the point of recognition runs through GAIn's own config parsing, not
    through the YAML the reader has to edit.

    The mode sentence is not padding.  ``np_score`` is the one retired
    spelling that was never a pure alias: ``AlleleScore`` used to read the
    default mode off the resource type, so ``np_score`` meant substitutions
    while ``allele_score`` means alleles.  A holder who swaps only the type
    string gets a resource that loads and reads differently, which is a
    silent wrong answer rather than an error -- so the replacement and the
    mode key have to arrive together or the message causes the bug it is
    warning about.
    """
    return (
        f"{found_in} declares resource type "
        f"'{RETIRED_ALLELE_SCORE_TYPE}', which was removed in GAIn "
        f"{RETIRED_VOCABULARY_REMOVAL_RELEASE}; write "
        f"'{PREFERRED_ALLELE_SCORE_TYPE}' instead. This is not a plain "
        f"rename: '{RETIRED_ALLELE_SCORE_TYPE}' read in substitutions mode "
        f"while '{PREFERRED_ALLELE_SCORE_TYPE}' reads in alleles mode by "
        f"default, so add 'allele_score_mode: substitutions' to keep the "
        f"previous behaviour."
    )


class RetirableResource(Protocol):
    """The little of a resource :func:`reject_retired_resource` needs.

    A structural type rather than ``GenomicResource`` itself: this module
    is deliberately dependency-free and low in the import graph (see the
    module docstring), and ``repository`` imports *it*, so naming the
    class here would close a cycle.
    """

    def get_type(self) -> str:
        """Return the resource's declared ``type:``."""
        ...

    def get_full_id(self) -> str:
        """Return the resource's id, with version where it has one."""
        ...


def reject_retired_resource(resource: RetirableResource) -> None:
    """Raise if ``resource`` declares a spelling GAIn has removed.

    Called from each seam that turns a ``type:`` string into something:
    the score factory (``build_score_from_resource``), ``AlleleScore``
    itself, the implementation builder (what ``grr_manage`` sweeps with),
    and the annotation pipeline's resource resolver.  Four call sites
    rather than one because there is no single seam they all pass
    through -- ADR 0011 established the same for the fragment-score
    warning -- and each is reachable without the others: the pipeline's
    own type check would otherwise pre-empt this message with a generic
    one, and the implementation builder never constructs a score at all.

    Not pushed down into ``GenomicResource`` itself, which would be the
    only common ancestor: that is also the enumeration and display path
    (``grr_manage list``, the web API's resource-types endpoint, the
    repository's SQL type predicate), and refusing there would abort a
    whole run over a repository that merely *contains* a retired
    resource.  That is the failure ADR 0011 records as the reason its
    predecessor expired.

    Raising here rather than letting the entry-point lookup fail is the
    whole point.  Deleting the registration already makes an ``np_score``
    resource fail, but it fails as
    ``unsupported resource implementation type <np_score>`` -- which tells
    a holder that GAIn does not know the type, not that GAIn removed it
    and what to write instead.

    Named by full id, matching the fragment-score notice: a repository may
    hold several versions of one resource id, each its own directory with
    its own config to migrate, and the bare id would name none of them
    precisely.  Rendered only on the failure path -- this runs on every
    resource open, including the statistics scan's per-region rebuilds.
    """
    if resource.get_type() != RETIRED_ALLELE_SCORE_TYPE:
        return
    raise ValueError(retired_resource_type_message(
        found_in=f"Resource '{resource.get_full_id()}'"))


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
