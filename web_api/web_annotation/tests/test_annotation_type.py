"""Tests for the single definition of a job's annotation type (gain#159)."""
import pathlib

from web_annotation.annotation_base_view import count_input_variants
from web_annotation.models import BaseJob


def test_annotation_type_has_exactly_vcf_and_tabular() -> None:
    """The enum carries exactly the two stored annotation-type values."""
    assert {
        member.name: member.value for member in BaseJob.AnnotationType
    } == {"VCF": "vcf", "TABULAR": "tabular"}


def test_count_input_variants_skips_the_tabular_header_line(
    tmp_path: pathlib.Path,
) -> None:
    """Tabular input has one header line not prefixed with '#'."""
    input_path = tmp_path / "input.tsv"
    input_path.write_text("chrom\tpos\tref\talt\n1\t10\tA\tC\n1\t20\tG\tT\n")

    count = count_input_variants(
        str(input_path), BaseJob.AnnotationType.TABULAR)

    assert count == 2


def test_count_input_variants_counts_every_vcf_record(
    tmp_path: pathlib.Path,
) -> None:
    """VCF headers are all '#'-prefixed, so every other line counts."""
    input_path = tmp_path / "input.vcf"
    input_path.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n"
        "1\t10\t.\tA\tC\n1\t20\t.\tG\tT\n",
    )

    count = count_input_variants(str(input_path), BaseJob.AnnotationType.VCF)

    assert count == 2
