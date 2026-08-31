"""``genomic_scores`` is a package, and its ``__init__`` is a forever facade.

gain#902 split the 3128-line ``genomic_scores`` module into a package of six
modules.  The split is invisible on purpose: the package's ``__init__``
re-exports every name the pre-split module DEFINED, so each
``from gain.genomic_resources.genomic_scores import <name>`` that named one
of them works after the split, unchanged.

Names the old module merely imported -- ``np``, ``Record``,
``GenomicScoreDef`` and some seventy others -- were reachable through it as
incidental re-exports and deliberately are not carried over; see the
package docstring.  No caller in gain, gpf or ``grr_bench`` used one.

**This is a permanent contract, not a deprecation shim.**  Deep imports from
the submodules (``.base``, ``.records``, ...) are allowed, but nothing is
required to migrate to them and nothing will be.  The names below are
therefore pinned the way the retired-vocabulary test pins its names -- spelled
out rather than derived from ``__all__`` or from ``dir()``.  Deriving them
would make this test agree with the facade by construction: dropping a name
from ``__init__`` would drop it from the expectation too, and the test would
stay green while the export it exists to guard disappeared.

The gain suite alone does not guard this surface.  It exercises the names
*gain itself* imports; ``AlleleRecordArrays``, ``clip_to_region`` and the
four-of-eight builders gain never calls are imported only by gpf, by
``grr_bench`` and by demo repositories out of this tree.  Those callers cannot
fail this repository's build, which is exactly why their imports are written
down here.
"""
import pytest
from gain.genomic_resources import genomic_scores as facade

# The public surface as it stood at the last commit before the split
# (gain#902, verified by an AST scan of every importer in gain and gpf).
# Grouped by the module each name moved INTO, which is documentation, not
# structure -- the contract is that all of them import from the package root.
SCORE_CLASSES = [
    "GenomicScore",     # -> base
    "PositionScore",    # -> position
    "AlleleScore",      # -> allele
    "FragmentScore",    # -> fragment
]

BUILDERS = [
    "build_position_score_from_resource",
    "build_position_score_from_resource_id",
    "build_allele_score_from_resource",
    "build_allele_score_from_resource_id",
    "build_fragment_score_from_resource",
    "build_fragment_score_from_resource_id",
    "build_score_from_resource",
    "build_score_from_resource_id",
]

RECORD_HELPERS = [
    "RecordArrays",
    "AlleleRecordArrays",
    "clip_span",
    "owns_record",
    "owned_records_mask",
    "clip_to_region",
]

# Public by naming and by being a default argument value a caller can read,
# even though no importer in gain or gpf names it today.  Re-exported because
# the contract is the pre-split surface, not the subset this tree happens to
# consume.
CONSTANTS = [
    "DEFAULT_VALUE_ARRAYS_BATCH_SIZE",
]

PUBLIC_SURFACE = SCORE_CLASSES + BUILDERS + RECORD_HELPERS + CONSTANTS


@pytest.mark.parametrize("name", PUBLIC_SURFACE)
def test_every_pre_split_public_name_imports_from_the_package(
    name: str,
) -> None:
    """``from gain.genomic_resources.genomic_scores import <name>`` works."""
    assert hasattr(facade, name), (
        f"{name} was importable from gain.genomic_resources.genomic_scores "
        f"before the gain#902 package split and must stay importable from "
        f"it. Re-export it from the package's __init__."
    )


@pytest.mark.parametrize("name", PUBLIC_SURFACE)
def test_all_covers_every_pre_split_public_name(name: str) -> None:
    """``__all__`` names the whole pre-split surface.

    Separate from the import test above because the two can fail apart: a
    name re-exported but left out of ``__all__`` still imports by attribute
    while vanishing from ``import *`` and from the documented surface.
    """
    assert name in facade.__all__, (
        f"{name} is re-exported but missing from __all__"
    )


def test_all_is_self_consistent() -> None:
    """Every name ``__all__`` promises actually resolves.

    Catches the typo that the parametrized tests above cannot: they only ask
    whether the names *they* know about are present, so a misspelt entry in
    ``__all__`` would sail past them and only break at a caller's
    ``import *``.
    """
    missing = [name for name in facade.__all__ if not hasattr(facade, name)]

    assert missing == [], (
        f"__all__ promises names the package does not define: {missing}"
    )
