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
from gain.genomic_resources.testing import (
    setup_bigwig,
    setup_denovo,
    setup_directories,
    setup_tabix,
    setup_vcf,
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

# tabix: '#'-prefixed header, chrom_mapping add_prefix, an explicit
# pos_end column, and an NA in the middle of the file.
TABIX_DATA = """
    #chrom  pos_begin  pos_end  tbx_float  tbx_extra
    1       10         10       0.75       7
    1       11         11       NA         8
    2       20         20       0.25       9
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

MEM_RESOURCE = """
    type: position_score
    table:
        filename: data.txt
    scores:
    - id: mem_float
      type: float
      name: s_float
    - id: mem_int
      type: int
      name: s_int
    - id: mem_str
      type: str
      name: s_str
    - id: mem_by_index
      type: int
      column_index: 5
    - id: mem_big
      type: float
      name: s_big
"""

TABIX_RESOURCE = """
    type: position_score
    table:
        filename: data.txt.gz
        format: tabix
        chrom_mapping:
            add_prefix: chr
    scores:
    - id: tbx_float
      type: float
      name: tbx_float
    - id: tbx_by_index
      type: int
      column_index: 4
"""

BIGWIG_RESOURCE = """
    type: position_score
    table:
        filename: data.bw
    scores:
    - id: bw_value
      type: float
      index: 3
"""

VCF_RESOURCE = """
    type: allele_score
    table:
        filename: data.vcf.gz
"""


@pytest.fixture(scope="module")
def golden_grr(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Build a four-backend GRR and pipeline config on the filesystem."""
    root_path = tmp_path_factory.mktemp("annotate_tabular_golden")
    setup_directories(
        root_path,
        {
            "annotation.yaml": textwrap.dedent(ANNOTATION),
            "grr.yaml": textwrap.dedent(f"""
                id: golden
                type: dir
                directory: "{root_path}/grr"
            """),
            "grr": {
                "mem_score": {
                    "genomic_resource.yaml": textwrap.dedent(MEM_RESOURCE),
                },
                "tabix_score": {
                    "genomic_resource.yaml": textwrap.dedent(TABIX_RESOURCE),
                },
                "bigwig_score": {
                    "genomic_resource.yaml": textwrap.dedent(BIGWIG_RESOURCE),
                },
                "vcf_score": {
                    "genomic_resource.yaml": textwrap.dedent(VCF_RESOURCE),
                },
            },
        },
    )
    setup_denovo(root_path / "grr" / "mem_score" / "data.txt", MEM_DATA)
    setup_tabix(
        root_path / "grr" / "tabix_score" / "data.txt.gz", TABIX_DATA,
        seq_col=0, start_col=1, end_col=2)
    setup_bigwig(
        root_path / "grr" / "bigwig_score" / "data.bw", BIGWIG_DATA,
        {"chr1": 1000, "chr2": 2000})
    setup_vcf(root_path / "grr" / "vcf_score" / "data.vcf.gz", VCF_DATA)
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
