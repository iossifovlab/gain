# pylint: disable=C0114,C0116,W0212,W0621
from gain.genomic_resources.statistics.chromosome_order import (
    natural_chromosome_key,
)


def test_a_shorter_number_sorts_before_a_longer_one() -> None:
    assert natural_chromosome_key("chr2") < natural_chromosome_key("chr10")


def test_a_numbered_contig_sorts_before_a_lettered_one() -> None:
    numbered = natural_chromosome_key("chr22")

    assert numbered < natural_chromosome_key("chrM")
    assert numbered < natural_chromosome_key("chrX")
    assert numbered < natural_chromosome_key("chrY")


def test_alt_random_and_unplaced_contigs_order_by_their_numbers() -> None:
    shuffled = [
        "chr10_GL383545v1_alt", "chr2", "chrUn_GL000195v1", "chr11",
        "chr10", "chr11_KI270721v1_random", "chr1", "chrX",
    ]

    assert sorted(shuffled, key=natural_chromosome_key) == [
        "chr1",
        "chr2",
        "chr10",
        "chr10_GL383545v1_alt",
        "chr11",
        "chr11_KI270721v1_random",
        "chrUn_GL000195v1",
        "chrX",
    ]


def test_no_chr_prefix_is_assumed() -> None:
    shuffled = ["10", "MT", "2", "1", "X"]

    assert sorted(shuffled, key=natural_chromosome_key) == [
        "1", "2", "10", "MT", "X",
    ]


def test_a_non_human_assembly_orders_by_its_scaffold_numbers() -> None:
    shuffled = ["scaffold_100", "scaffold_2", "scaffold_21", "scaffold_3"]

    assert sorted(shuffled, key=natural_chromosome_key) == [
        "scaffold_2", "scaffold_3", "scaffold_21", "scaffold_100",
    ]


def test_case_never_decides_an_order() -> None:
    assert natural_chromosome_key("CHR1") == natural_chromosome_key("chr1")
    assert natural_chromosome_key("chr2") < natural_chromosome_key("CHR10")


def test_leading_zeros_do_not_change_a_contigs_number() -> None:
    assert natural_chromosome_key("chr01") == natural_chromosome_key("chr1")
    assert natural_chromosome_key("chr007") < natural_chromosome_key("chr10")


def test_a_digit_run_wider_than_any_fixed_pad_still_orders() -> None:
    # The zero-padding the issue sketched mis-orders these as soon as a
    # run outgrows the chosen width; the digit-count prefix does not.
    shorter = "ctg" + "9" * 30
    longer = "ctg" + "1" * 31

    assert natural_chromosome_key(shorter) < natural_chromosome_key(longer)
