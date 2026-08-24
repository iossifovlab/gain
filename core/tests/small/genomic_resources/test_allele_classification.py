# pylint: disable=W0621,C0114,C0116,W0212,W0613
import itertools

import pytest
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.genomic_resources.allele_classification import (
    AlleleClass,
    classify_allele,
)


def test_a_one_to_one_pair_is_a_substitution() -> None:
    classification = classify_allele("A", "G")

    assert classification.allele_class == AlleleClass.SUBSTITUTION


def test_an_anchored_insertion_carries_the_length_it_adds() -> None:
    classification = classify_allele("A", "ATT")

    assert classification.allele_class == AlleleClass.INSERTION
    assert classification.length_change == 2


def test_an_anchored_deletion_carries_the_length_it_removes() -> None:
    classification = classify_allele("ATT", "A")

    assert classification.allele_class == AlleleClass.DELETION
    assert classification.length_change == -2


def test_an_mnv_is_complex_not_a_multi_base_substitution() -> None:
    classification = classify_allele("AA", "GG")

    assert classification.allele_class == AlleleClass.COMPLEX
    assert (classification.ref_length, classification.alt_length) == (2, 2)


@pytest.mark.parametrize("ref,alt", [
    # Both alleles multi-base, and unanchored either way.
    ("AT", "GCCT"),
    # Lengthens, but the alternative does not start with the reference.
    ("A", "GT"),
    # Shortens, but the reference does not start with the alternative.
    ("AT", "G"),
    # Starts with the reference, but the reference is not a single base:
    # anchoring is strict, so this is not an insertion.
    ("AT", "ATG"),
    # The mirror image: alt is not a single base.
    ("ATG", "AT"),
])
def test_only_a_strictly_anchored_indel_escapes_complex(
    ref: str, alt: str,
) -> None:
    classification = classify_allele(ref, alt)

    assert classification.allele_class == AlleleClass.COMPLEX
    assert (classification.ref_length, classification.alt_length) \
        == (len(ref), len(alt))


def test_the_identity_pair_stays_a_substitution() -> None:
    # ADR 0020 reads "strictly 1->1" literally: an allele that changes
    # nothing is still a substitution, and the 4x4 matrix keeps its
    # diagonal.  Rendering that diagonal is the matrix's decision.
    classification = classify_allele("A", "A")

    assert classification.allele_class == AlleleClass.SUBSTITUTION
    assert classification.length_change == 0


@pytest.mark.parametrize("ref,alt", [
    ("N", "A"),
    ("<DEL>", "A"),
    ("", ""),
    ("A", "N"),
    ("A", "ATN"),
    ("A", "<DEL>"),
    ("A", "*"),
    ("", "A"),
    ("A", ""),
])
def test_an_allele_that_does_not_parse_is_counted_as_other(
    ref: str, alt: str,
) -> None:
    classification = classify_allele(ref, alt)

    assert classification.allele_class == AlleleClass.OTHER
    assert classification.ref_length is None
    assert classification.alt_length is None


@pytest.mark.parametrize("ref,alt", [
    ("A", None),
    (None, "A"),
    (None, None),
])
def test_a_row_without_an_allele_is_counted_as_other(
    ref: str | None, alt: str | None,
) -> None:
    # A table need not configure a ref or an alt column, and a VCF ALT of
    # "." yields a record with no alternative at all -- both reach here as
    # None.  Such a row is still a row: it has to classify, or the class
    # counts stop summing to the row count (ADR 0020).
    classification = classify_allele(ref, alt)

    assert classification.allele_class == AlleleClass.OTHER
    assert classification.ref_length is None
    assert classification.alt_length is None


def test_other_has_no_length_change() -> None:
    assert classify_allele("N", "A").length_change is None


@pytest.mark.parametrize("ref,alt,expected", [
    ("a", "ag", AlleleClass.INSERTION),
    ("A", "ag", AlleleClass.INSERTION),
    ("a", "AG", AlleleClass.INSERTION),
    ("at", "a", AlleleClass.DELETION),
    ("a", "g", AlleleClass.SUBSTITUTION),
    ("aa", "gg", AlleleClass.COMPLEX),
])
def test_case_does_not_change_an_alleles_class(
    ref: str, alt: str, expected: AlleleClass,
) -> None:
    # A soft-masked lowercase base would otherwise miss the anchor and
    # inflate `complex` with no signal, and a mixed-case pair would
    # classify differently from the same pair written in one case.
    classification = classify_allele(ref, alt)

    assert classification.allele_class == expected
    assert classification == classify_allele(ref.upper(), alt.upper())


#: The annotation layer's `VCFAllele` derives the same four classes in its
#: constructor.  The GRR may not import it -- `genomic_resources` sits below
#: `annotation` -- so the rules are stated twice on purpose; this pin is what
#: makes a future divergence in either loud instead of silent.  It holds only
#: where both alleles parse: `VCFAllele` has no `other`.
CLASS_OF_ANNOTATABLE_TYPE = {
    Annotatable.Type.SUBSTITUTION: AlleleClass.SUBSTITUTION,
    Annotatable.Type.SMALL_INSERTION: AlleleClass.INSERTION,
    Annotatable.Type.SMALL_DELETION: AlleleClass.DELETION,
    Annotatable.Type.COMPLEX: AlleleClass.COMPLEX,
}


def test_classification_agrees_with_the_annotation_layer() -> None:
    alleles = [
        "".join(bases)
        for length in (1, 2, 3)
        for bases in itertools.product("ACGT", repeat=length)
    ]

    disagreements = [
        (ref, alt)
        for ref in alleles
        for alt in alleles
        if classify_allele(ref, alt).allele_class
        != CLASS_OF_ANNOTATABLE_TYPE[VCFAllele("1", 1, ref, alt).type]
    ]

    assert disagreements == []


def test_the_class_values_are_stable_wire_names() -> None:
    # These strings are the natural keys for #777's statistics file, so
    # they are wire names: renaming one is a format change, not a
    # refactor.  What the file actually stores is #777's to pin.
    assert {allele_class.value for allele_class in AlleleClass} == {
        "substitution", "insertion", "deletion", "complex", "other",
    }
