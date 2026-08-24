"""What an allele-score row's ref/alt pair is, as one of five classes.

The classes and the rule that assigns them are vocabulary, defined once in
``CONTEXT.md`` and decided in ADR 0020; :func:`classify_allele` states the
rule and nothing else here restates it.  The classification is what the
allele-score statistics count, and it is deliberately independent of them:
no accumulator, no storage, no scan -- just the rule.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class AlleleClass(enum.Enum):
    """The class of an allele-score row's ref/alt pair (ADR 0020)."""

    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    DELETION = "deletion"
    COMPLEX = "complex"
    OTHER = "other"


@dataclass(frozen=True)
class AlleleClassification:
    """A classified ref/alt pair."""

    allele_class: AlleleClass
    #: Lengths of the two alleles, absent for :attr:`AlleleClass.OTHER` --
    #: those strings are not alleles, so they have no allele length.
    ref_length: int | None
    alt_length: int | None

    @property
    def length_change(self) -> int | None:
        """Bases the alternative adds over the reference."""
        if self.ref_length is None or self.alt_length is None:
            return None
        return self.alt_length - self.ref_length


#: The bases an allele may be written with.  Anything else -- ``N``, a
#: symbolic allele such as ``<DEL>``, the missing-allele ``*`` -- means the
#: pair does not parse as an allele and is counted as ``other``.
ALLELE_BASES = "ACGT"

#: Every substitution classifies to the same value: the branch returning one
#: has already established that both lengths are 1.  So does every ``other``.
#: Shared constants rather than a cache -- both sets are closed, so nothing
#: grows with the rows scanned, and the class is frozen and compared by value,
#: which makes the shared identity invisible to callers.
_SUBSTITUTION = AlleleClassification(AlleleClass.SUBSTITUTION, 1, 1)
_OTHER = AlleleClassification(AlleleClass.OTHER, None, None)


def _is_allele(allele: str) -> bool:
    # ``strip`` removes only leading and trailing characters, so an empty
    # result proves every character was a base -- the same predicate as a
    # subset test, without building a set per allele per row.
    return bool(allele) and not allele.strip(ALLELE_BASES)


def classify_allele(
    ref: str | None, alt: str | None,
) -> AlleleClassification:
    """Classify a ref/alt pair as written, VCF-anchored (ADR 0020).

    Both alleles are upper-cased first, so a soft-masked lowercase base
    classifies as the base it masks rather than missing the anchor and
    inflating ``complex``.  What is still not an allele afterwards -- ``N``,
    a symbolic allele, an empty string -- is ``other``, and the remaining
    rules apply in the order the ADR states them:

    * **substitution** -- strictly one base to one base, the identity pair
      included;
    * **insertion** -- anchored: a single reference base that the
      alternative starts with, adding ``length_change`` bases;
    * **deletion** -- the mirror image, removing ``-length_change`` bases;
    * **complex** -- everything else, MNVs and unanchored indels alike,
      carrying both lengths.

    Total over *rows*, not merely over strings: a table need not configure
    a ref or an alt column, and a VCF ALT of ``.`` yields a record with no
    alternative at all, so either allele may arrive as ``None``.  A row
    missing an allele is ``other`` -- it is still a row, and the class
    counts of a resource's rows always sum to its row count.  Never raises.
    """
    if ref is None or alt is None:
        return _OTHER
    ref, alt = ref.upper(), alt.upper()
    if not _is_allele(ref) or not _is_allele(alt):
        return _OTHER
    if len(ref) == 1:
        if len(alt) == 1:
            return _SUBSTITUTION
        if alt.startswith(ref):
            return AlleleClassification(
                AlleleClass.INSERTION, 1, len(alt))
    if len(alt) == 1 and ref.startswith(alt):
        return AlleleClassification(
            AlleleClass.DELETION, len(ref), 1)
    return AlleleClassification(AlleleClass.COMPLEX, len(ref), len(alt))
