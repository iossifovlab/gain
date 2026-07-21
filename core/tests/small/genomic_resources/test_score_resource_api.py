# pylint: disable=protected-access
"""Pin the public API surface of the shared ``ScoreResource`` base.

``ScoreResource`` is the *catalogue plane* shared by gene scores and genomic
scores: score definitions, ``get_all_scores``/``get_score_definition`` and the
histogram accessors -- and nothing else.  The whole point of the abstraction
(see ``docs/2026-07-14-gain-score-abstraction.html``) is that the two families
are *not* an ``is-a``: gene scores are keyed by gene symbol, have no open/close
lifecycle, no table, no aggregators and nothing to fetch over a region.  A base
method that assumes any of those re-creates the false ``is-a`` this whole epic
exists to avoid.

So the base's public method set is pinned here against an explicit allowlist.
If someone lifts ``open``/``close``/``fetch_scores``/``get_all_chromosomes`` (or
any other lifecycle, table or aggregator method) into the base "because both
subclasses happen to have one", this test fails and names the intruder.

This is deliberately NOT a ``pytestarch`` rule: pytestarch reasons about module
*imports* and cannot express "this class must not grow this method".
"""
from gain.gene_scores.gene_scores import GeneScore
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.score_resource import ScoreResource

# The complete, intended public surface of ScoreResource.  Grow this ONLY when
# the new method is genuinely a catalogue-plane concern shared by BOTH families
# -- never to accommodate a lifecycle/table/fetch/aggregator method that only
# genomic scores have.
ALLOWED_PUBLIC_METHODS = {
    "get_all_scores",
    "get_score_definition",
    "get_histogram_filename",
    "get_score_histogram",
    "get_score_range",
    "get_histogram_image_filename",
    "get_histogram_image_url",
    "get_histogram_image_public_url",
    # The shared histogram config-schema fragment, contributed into both
    # families' ``get_schema()`` -- a catalogue-plane concern, not lifecycle.
    "histogram_schema",
}

# Names that must NEVER appear on the base -- they encode the false is-a.
# Listed explicitly so a regression reads as intent, not a set-difference.
FORBIDDEN_ON_BASE = {
    "open", "close", "is_open", "__enter__", "__exit__",
    "fetch_lines", "fetch_scores", "fetch_region", "fetch_region_values",
    "fetch_scores_agg", "get_all_chromosomes",
}


def _public_methods_defined_on(cls: type) -> set[str]:
    """Public callables defined directly on ``cls`` (not inherited)."""
    return {
        name
        for name in vars(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


def test_both_families_extend_the_shared_base() -> None:
    assert issubclass(GenomicScore, ScoreResource)
    assert issubclass(GeneScore, ScoreResource)


def test_score_resource_public_surface_is_exactly_the_allowlist() -> None:
    assert _public_methods_defined_on(ScoreResource) == ALLOWED_PUBLIC_METHODS


def test_score_resource_has_no_lifecycle_table_or_fetch_methods() -> None:
    # Guards against the intruder being added AND spuriously allow-listed at
    # the same time: a forbidden name may never appear, allowlist or not.
    assert not (FORBIDDEN_ON_BASE & set(vars(ScoreResource)))
    assert not (FORBIDDEN_ON_BASE & ALLOWED_PUBLIC_METHODS)
