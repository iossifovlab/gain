# pylint: disable=missing-function-docstring,redefined-outer-name
"""The validation contract of the record-to-annotatable builders.

A record that the builders cannot turn into an annotatable raises
``MalformedRecordError``, whichever converter its keys select, so the one
caller that has to tell a bad record from a bug -- the single-allele view
-- can catch exactly that (iossifovlab/gain#1679).
"""
import pathlib
import subprocess
import sys
import textwrap
from typing import Any

import pytest
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.record_to_annotatable import (
    MalformedRecordError,
    build_annotatable_from_dict,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.testing.builders import a_reference_genome

MALFORMED_RECORDS: list[dict[str, Any]] = [
    # no converter matches the keys
    {},
    # a text field that is not a string, one row per converter
    {"chrom": 3, "pos": "100"},
    {"chrom": None, "pos": "100"},
    {"chrom": 3, "pos_beg": "1", "pos_end": "2"},
    {"chrom": 3, "pos": "1", "ref": "C", "alt": "A"},
    {"chrom": "chr1", "pos": "1", "ref": 3, "alt": "A"},
    {"chrom": "chr1", "pos": "1", "ref": "C", "alt": None},
    {"chrom": "chr1", "pos": "1", "ref": "C", "alt": ["A"]},
    {"vcf_like": 3},
    {"chrom": 3, "pos": "1", "variant": "sub(A->C)"},
    {"chrom": "chr1", "pos": "1", "variant": None},
    {"location": 3, "variant": "sub(A->C)"},
    {"location": "chr1:100", "variant": 3},
    {"chrom": 3, "pos_beg": "1", "pos_end": "2", "cnv_type": "CNV+"},
    {"chrom": "chr1", "pos_beg": "1", "pos_end": "2", "cnv_type": 3},
    # a position that is not an integer, or is not 1-based
    {"chrom": "chr1", "pos": 1.9},
    {"chrom": "chr1", "pos": True},
    {"chrom": "chr1", "pos": None},
    {"chrom": "chr1", "pos": [1]},
    {"chrom": "chr1", "pos": "x"},
    {"chrom": "chr1", "pos": "1.5"},
    {"chrom": "chr1", "pos": 0},
    {"chrom": "chr1", "pos": "0"},
    {"chrom": "chr1", "pos": -5, "ref": "C", "alt": "A"},
    {"chrom": "chr1", "pos_beg": True, "pos_end": "2"},
    {"chrom": "chr1", "pos_beg": "1", "pos_end": 2.0},
    {"chrom": "chr1", "pos_beg": "0", "pos_end": "5", "cnv_type": "CNV+"},
    {"vcf_like": "chr1:0:C:A"},
    {"vcf_like": "chr1:x:C:A"},
    {"chrom": "chr1", "pos": 0, "variant": "sub(A->C)"},
    {"location": "chr1:0", "variant": "sub(A->C)"},
    {"location": "chr1:x", "variant": "sub(A->C)"},
    {"location": "chr1:0-5", "variant": "CNV+"},
    # a span that ends before it begins, the empty span included
    {"chrom": "chr1", "pos_beg": "10", "pos_end": "5"},
    {"chrom": "chr1", "pos_beg": 10, "pos_end": 9},
    {"chrom": "chr1", "pos_beg": "10", "pos_end": "5", "cnv_type": "CNV+"},
    {"location": "chr1:10-5", "variant": "CNV+"},
    # a text field whose format the converter cannot read
    {"vcf_like": "chr1:100:A"},
    {"location": "chr1", "variant": "sub(A->C)"},
    {"location": "chr1:1", "variant": "CNV+"},
    {"chrom": "chr1", "pos_beg": "1", "pos_end": "2", "cnv_type": "banana"},
    {"chrom": "chr1", "pos": "1", "variant": "ins(A)"},
    {"location": "chr1:1", "variant": "del(1)"},
]


@pytest.mark.parametrize("record", MALFORMED_RECORDS)
def test_a_malformed_record_is_refused(record: dict[str, Any]) -> None:
    with pytest.raises(MalformedRecordError):
        build_annotatable_from_dict(record)


@pytest.fixture
def genome(tmp_path: pathlib.Path) -> ReferenceGenome:
    return build_reference_genome_from_resource(
        a_reference_genome()
        .with_chromosome("chr1", "ACGT" * 10)
        .build_resource(tmp_path))


def test_an_unknown_dae_variant_is_refused(genome: ReferenceGenome) -> None:
    with pytest.raises(MalformedRecordError, match="weird variant"):
        build_annotatable_from_dict(
            {"chrom": "chr1", "pos": "5", "variant": "flip(A)"}, genome)


def test_a_position_below_1_is_refused_before_the_genome_is_read(
    genome: ReferenceGenome,
) -> None:
    with pytest.raises(MalformedRecordError, match="pos"):
        build_annotatable_from_dict(
            {"chrom": "chr1", "pos": "0", "variant": "del(1)"}, genome)


def test_a_non_integer_position_names_its_column() -> None:
    with pytest.raises(MalformedRecordError, match="pos_beg"):
        build_annotatable_from_dict(
            {"chrom": "chr1", "pos_beg": "x", "pos_end": "5"})


def test_a_null_alt_is_refused_with_asserts_stripped() -> None:
    script = textwrap.dedent("""
        from gain.annotation.record_to_annotatable import (
            MalformedRecordError, build_annotatable_from_dict,
        )
        try:
            build_annotatable_from_dict(
                {"chrom": "chr1", "pos": "1", "ref": "C", "alt": None})
        except MalformedRecordError:
            print("refused")
    """)

    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        capture_output=True, text=True, check=False)

    assert (result.returncode, result.stdout) == (0, "refused\n"), \
        result.stderr


@pytest.mark.parametrize(("record", "expected"), [
    ({"chrom": "chr1", "pos": 5}, Position("chr1", 5)),
    ({"chrom": "chr1", "pos": "5"}, Position("chr1", 5)),
    ({"chrom": "chr1", "pos_beg": 5, "pos_end": 5}, Region("chr1", 5, 5)),
    ({"chrom": "chr1", "pos": "5", "ref": "C", "alt": "A"},
     VCFAllele("chr1", 5, "C", "A")),
    ({"vcf_like": "chr1:5:C:A"}, VCFAllele("chr1", 5, "C", "A")),
    ({"chrom": "chr1", "pos": 5, "variant": "sub(C->A)"},
     VCFAllele("chr1", 5, "C", "A")),
    ({"location": "chr1:5", "variant": "sub(C->A)"},
     VCFAllele("chr1", 5, "C", "A")),
    ({"chrom": "chr1", "pos_beg": 5, "pos_end": 5, "cnv_type": "CNV+"},
     CNVAllele("chr1", 5, 5, CNVAllele.Type.LARGE_DUPLICATION)),
    ({"location": "chr1:5-5", "variant": "CNV-"},
     CNVAllele("chr1", 5, 5, CNVAllele.Type.LARGE_DELETION)),
])
def test_a_well_formed_record_builds(
    record: dict[str, Any], expected: Annotatable,
) -> None:
    assert build_annotatable_from_dict(record) == expected
