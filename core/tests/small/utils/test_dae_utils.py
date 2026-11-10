# pylint: disable=W0621,C0114,C0116
import pathlib

import pytest
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.testing import setup_genome
from gain.utils.dae_utils import dae2vcf_variant, join_line


@pytest.fixture
def genome(tmp_path: pathlib.Path) -> ReferenceGenome:
    # chr1 positions 1..12: A C G T A C G T A C G T
    return setup_genome(tmp_path / "genome.fa", """
        >chr1
        ACGTACGTACGT
    """)


def test_join_line() -> None:
    assert join_line(
        ["1", "a", "3.14", ["0", "1.23", "b"], ["single"]],
    ) == "1\ta\t3.14\t0; 1.23; b\tsingle\n"


def test_join_line_writes_none_and_the_none_string_as_empty() -> None:
    assert join_line(["a", None, "None", 5], sep=",") == "a,,,5\n"


@pytest.mark.parametrize("variant, ref, alt", [
    ("sub(A->G)", "A", "G"),
    ("complex(AC->T)", "AC", "T"),
    ("comp(T->GGA)", "T", "GGA"),
])
def test_dae2vcf_variant_substitution_keeps_the_position(
    variant: str, ref: str, alt: str,
) -> None:
    assert dae2vcf_variant("chr1", 5, variant, None) == (5, ref, alt)


@pytest.mark.parametrize("variant", ["ins(AC)", "del(2)"])
def test_dae2vcf_variant_refuses_an_indel_without_a_genome(
    variant: str,
) -> None:
    with pytest.raises(ValueError, match="genome is required"):
        dae2vcf_variant("chr1", 5, variant, None)


def test_dae2vcf_variant_insertion_anchors_on_the_previous_base(
    genome: ReferenceGenome,
) -> None:
    assert dae2vcf_variant("chr1", 5, "ins(GG)", genome) == (4, "T", "TGG")


def test_dae2vcf_variant_deletion_anchors_on_the_previous_base(
    genome: ReferenceGenome,
) -> None:
    assert dae2vcf_variant("chr1", 5, "del(3)", genome) == (4, "TACG", "T")


@pytest.mark.parametrize("variant", [
    "sub(A-G)", "ins()", "del(x)", "xsub(A->G)", "sub(A->G)x",
])
def test_dae2vcf_variant_refuses_an_unknown_variant(
    genome: ReferenceGenome, variant: str,
) -> None:
    with pytest.raises(ValueError, match=r"weird variant: "):
        dae2vcf_variant("chr1", 5, variant, genome)
