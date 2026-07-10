# pylint: disable=W0621,C0114,C0116,W0212,W0613,R0917
"""Golden test locking down ``annotate_tabular`` output byte-for-byte.

The fixture exercises all four genomic position table backends in a
single annotation run -- in-memory, tabix, VCF and bigWig -- so that a
change to the table read path shows up here as a diff, whichever backend
it touches.

Regenerate the expected file with::

    GAIN_UPDATE_GOLDEN=1 pytest tests/small/annotation/\
test_annotate_tabular_golden.py

The test fails when it regenerates, so a regeneration can never be
mistaken for a passing run.  Inspect the diff before committing it.
"""
import os
import pathlib
import textwrap

import pytest
from gain.annotation.annotate_tabular import cli
from gain.genomic_resources.testing import setup_denovo, setup_directories
from gain.genomic_resources.testing.builders import (
    GRRBuilder,
    a_bigwig_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
    an_allele_score,
)

GOLDEN_PATH = (
    pathlib.Path(__file__).parent / "fixtures"
    / "annotate_tabular_golden.tsv"
)
UPDATE_ENV = "GAIN_UPDATE_GOLDEN"

# Input positions are chosen to cover, on every backend: a plain hit, a
# second adjacent hit, a row whose score is NA, a position that misses
# entirely, and a hit on a second contig.
IN_CONTENT = """
    chrom  pos  ref  alt
    chr1   10   A    T
    chr1   11   A    T
    chr1   12   A    T
    chr1   99   A    T
    chr2   20   C    G
    chr2   21   C    G
"""

# mem: header present, scores addressed both by column name and by
# column index.
#
# Note that ``stringify`` renders floats with %.3g, widening to %.6g
# only for 100 <= value < 100_000.  Tabular output is therefore lossy
# below three significant figures, and this golden cannot detect a
# precision drift finer than that -- the histogram/min-max golden is
# what pins full float precision.  s_float still carries a
# more-than-representable value and a scientific-notation value to lock
# the rendering down; s_big straddles both %.3g/%.6g branch boundaries.
MEM_DATA = """
    chrom  pos_begin  s_float                s_int  s_str  s_extra  s_big
    chr1   10         0.1                    1      alpha  100      12345.6789
    chr1   11         0.2                    2      beta   200      99.99999
    chr1   12         NA                     3      gamma  300      100.0
    chr2   20         0.1234567890123456789  4      delta  400      100000.0
    chr2   21         -1.5e-3                5      eps    500      0.0
"""

# tabix: the builder comments the header line and derives the seq/start/
# end columns from it.  Contigs are unprefixed and mapped with
# add_prefix, and an NA sits mid-file.
TABIX_DATA = """
    chrom  pos_begin  pos_end  tbx_float  tbx_extra
    1      10         10       0.75       7
    1      11         11       NA         8
    2      20         20       0.25       9
"""

# tabix allele score: the only resource here that reads reference and
# alternative as TABLE COLUMNS through Line, rather than off VCFLine's
# attributes -- so it is what pins ref_key/alt_key resolution and the
# ref/alt carried on a tabix record.
#
# chr1:10 carries two rows differing only in `alternative`.  The input
# asks for A>T, so a_score must be 0.9 and never 0.8; picking the wrong
# allele is otherwise a silent, plausible-looking value.
ALLELE_DATA = """
    chrom  pos_begin  pos_end  reference  alternative  a_score  a_label
    1      10         10       A          T            0.9      hit
    1      10         10       A          C            0.8      wrong_alt
    1      11         11       A          T            NA       na_row
    2      20         20       C          G            0.7      two
"""

# bigWig: bedGraph intervals are 0-based half-open, so 1-based position
# p falls in the interval containing p - 1.
BIGWIG_DATA = """
    chr1  0   10  0.11
    chr1  10  20  0.22
    chr2  0   30  0.33
"""

# VCF: VCF_AF is Number=A, exercising the allele-indexed INFO path.
#
# VCF_STR is a plain string.  VCF_STR_MULTI is Number=. Type=String,
# which ``VCFLine.get`` alone collapses to a '|'-joined string -- that
# join lives in the VCF backend, not in the shared stringify(), and is
# the sort of backend-local behaviour a record migration can silently
# drop.  Each string field is absent from one record, so the
# missing-value path is covered for both shapes.
VCF_DATA = """
##fileformat=VCFv4.1
##INFO=<ID=VCF_FLOAT,Number=1,Type=Float,Description="a float">
##INFO=<ID=VCF_INT,Number=1,Type=Integer,Description="an int">
##INFO=<ID=VCF_AF,Number=A,Type=Float,Description="per-allele float">
##INFO=<ID=VCF_STR,Number=1,Type=String,Description="a string">
##INFO=<ID=VCF_STR_MULTI,Number=.,Type=String,Description="strings">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T   .    .      VCF_FLOAT=0.5;VCF_INT=11;VCF_AF=0.05;VCF_STR=benign;VCF_STR_MULTI=a,b
chr1   11  .  A   T   .    .      VCF_FLOAT=0.6;VCF_INT=12;VCF_AF=0.06;VCF_STR=likely_pathogenic
chr2   20  .  C   G   .    .      VCF_FLOAT=0.7;VCF_INT=13;VCF_AF=0.07;VCF_STR_MULTI=x,y,z
"""  # noqa: E501

ANNOTATION = """
    - position_score:
        resource_id: mem_score
        attributes:
        - source: mem_float
        - source: mem_int
        - source: mem_str
        - source: mem_by_index
        - source: mem_big
    - position_score:
        resource_id: tabix_score
        attributes:
        - source: tbx_float
        - source: tbx_by_index
    - allele_score:
        resource_id: allele_score
        attributes:
        - source: a_score
        - source: a_label
    - position_score:
        resource_id: bigwig_score
        attributes:
        - source: bw_value
    - allele_score:
        resource_id: vcf_score
        attributes:
        - source: VCF_FLOAT
        - source: VCF_INT
        - source: VCF_AF
        - source: VCF_STR
        - source: VCF_STR_MULTI
"""


def _golden_grr_builder() -> GRRBuilder:
    """Compose the four-backend GRR used by the golden run."""
    return (
        a_grr()
        .with_resource(
            "mem_score",
            a_position_score()
            .with_score("mem_float", "float", column_name="s_float")
            .with_score("mem_int", "int", column_name="s_int")
            .with_score("mem_str", "str", column_name="s_str")
            .with_score("mem_by_index", "int", column_index=5)
            .with_score("mem_big", "float", column_name="s_big")
            .with_data(MEM_DATA),
        )
        .with_resource(
            "tabix_score",
            a_position_score()
            .with_tabix()
            .with_chrom_mapping(add_prefix="chr")
            .with_score("tbx_float", "float", column_name="tbx_float")
            .with_score("tbx_by_index", "int", column_index=4)
            .with_data(TABIX_DATA),
        )
        .with_resource(
            "allele_score",
            an_allele_score()
            .with_tabix()
            .with_chrom_mapping(add_prefix="chr")
            .with_score("a_score", "float", column_name="a_score")
            .with_score("a_label", "str", column_index=6)
            .with_data(ALLELE_DATA),
        )
        .with_resource(
            "bigwig_score",
            a_bigwig_score()
            .with_score("bw_value", "float")
            .with_data(BIGWIG_DATA)
            .with_chrom_lens({"chr1": 1000, "chr2": 2000}),
        )
        .with_resource(
            "vcf_score",
            a_vcf_info_score().with_data(VCF_DATA),
        )
    )


@pytest.fixture(scope="module")
def golden_grr(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build a four-backend GRR and pipeline config on the filesystem."""
    root_path = tmp_path_factory.mktemp("annotate_tabular_golden")
    _golden_grr_builder().build_definition(root_path, grr_id="golden")
    setup_directories(
        root_path, {"annotation.yaml": textwrap.dedent(ANNOTATION)})
    return root_path


def test_annotate_tabular_golden(
    golden_grr: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    setup_denovo(in_file, IN_CONTENT)

    cli([
        str(a) for a in [
            in_file, golden_grr / "annotation.yaml",
            "--grr", golden_grr / "grr.yaml",
            "-o", out_file,
            "-w", tmp_path / "work",
            "-j", 1,
        ]
    ])

    actual = out_file.read_bytes()

    if os.environ.get(UPDATE_ENV):
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_bytes(actual)
        pytest.fail(
            f"golden file regenerated at {GOLDEN_PATH}; "
            f"review the diff, then re-run without {UPDATE_ENV}")

    assert GOLDEN_PATH.exists(), (
        f"missing golden file {GOLDEN_PATH}; "
        f"regenerate it with {UPDATE_ENV}=1")

    expected = GOLDEN_PATH.read_bytes()
    if actual != expected:
        pytest.fail(
            "annotate_tabular output changed.\n"
            f"--- expected ({GOLDEN_PATH})\n{expected.decode()}\n"
            f"--- actual\n{actual.decode()}")
