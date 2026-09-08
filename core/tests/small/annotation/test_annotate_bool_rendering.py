"""A ``bool`` score in annotation output, through both CLI sinks (gain#1222).

``stringify`` used to render ``False`` as the sink's missing-value marker,
which is what ``None`` renders, so a reader could not tell a false flag
from an absent one.  The tests pin WHOLE ROWS so the three values -- true,
false, no record -- are seen distinct in the bytes a user reads.  Why the
two sinks spell a bool the same way: ADR 0026.
"""
# pylint: disable=C0116,W0621
import pathlib
import textwrap

import pytest
from gain.annotation import annotate_tabular, annotate_vcf
from gain.genomic_resources.testing import (
    setup_denovo,
    setup_directories,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import a_grr, a_position_score

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


@pytest.fixture(scope="module")
def bool_score_grr(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """One ``bool`` position score: true at 10-19, false at 20-29, none above.

    The attribute names an aggregator because a ``bool`` score has no
    default (ADR 0024).
    """
    root = tmp_path_factory.mktemp("bool_rendering")
    (
        a_grr()
        .with_resource("flags", a_position_score()
            .with_score("flag", "bool")
            .with_data("""
                chrom  pos_begin  pos_end  flag
                chr1   10         19       True
                chr1   20         29       False
            """))
        .build_definition(root, grr_id="bool_rendering")
    )
    setup_directories(root, {
        "annotation.yaml": textwrap.dedent("""
            - position_score:
                resource_id: flags
                attributes:
                - source: flag
                  aggregator: mode
        """),
    })
    return root


def _argv(
    grr: pathlib.Path, in_file: pathlib.Path, out_file: pathlib.Path,
    tmp_path: pathlib.Path,
) -> list[str]:
    return [str(a) for a in [
        in_file, grr / "annotation.yaml", "--grr", grr / "grr.yaml",
        "-o", out_file, "-w", tmp_path / "work", "-j", 1,
    ]]


def test_a_false_flag_renders_no_in_an_annotated_table(
    bool_score_grr: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """The defect rendered the false row as ``"chr1\\t22\\t"``, byte-identical
    to the unrecorded row below it."""
    in_file = setup_denovo(tmp_path / "in.txt", textwrap.dedent("""
        chrom   pos
        chr1    12
        chr1    22
        chr1    32
    """))
    out_file = tmp_path / "out.txt"

    annotate_tabular.cli(_argv(bool_score_grr, in_file, out_file, tmp_path))

    assert out_file.read_text() == (
        "chrom\tpos\tflag\n"
        "chr1\t12\tyes\n"
        "chr1\t22\tno\n"
        "chr1\t32\t\n"
    )


def test_a_false_flag_renders_no_in_an_annotated_vcf(
    bool_score_grr: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """The defect rendered the false allele as ``.``, so the writer dropped
    its key exactly as it drops the unrecorded allele's: an INFO column of
    ``.`` with no ``flag`` key."""
    in_file = tmp_path / "in.vcf"
    setup_vcf(in_file, textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT s1
        chr1   12  .  C   T   .    .      .    GT     0/1
        chr1   22  .  C   T   .    .      .    GT     0/1
        chr1   32  .  C   T   .    .      .    GT     0/1
    """))
    out_file = tmp_path / "out.vcf"

    annotate_vcf.cli(_argv(bool_score_grr, in_file, out_file, tmp_path))

    records = [
        line for line in out_file.read_text().splitlines()
        if not line.startswith("#")
    ]
    assert records == [
        "chr1\t12\t.\tC\tT\t.\t.\tflag=yes\tGT\t0/1",
        "chr1\t22\t.\tC\tT\t.\t.\tflag=no\tGT\t0/1",
        "chr1\t32\t.\tC\tT\t.\t.\t.\tGT\t0/1",
    ]
