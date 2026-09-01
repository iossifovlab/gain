# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Golden test locking down the resource-statistics build byte-for-byte.

A single GRR exercises all four genomic position table backends -- in-memory,
tabix, VCF and bigWig -- and the histogram/min-max statistics are then built by
``grr_manage repo-stats``.  The serialized histograms are asserted byte-for-byte
against a checked-in expected file, and the min/max statistics carried inside
those histograms are asserted against a second checked-in expected file.  This
is the safety net for an upcoming refactor of the genomic position table read
path: a change that alters what any backend yields shows up here as a diff.

The fixtures deliberately cover the corners that a record-migration can silently
break:

* the in-memory table is authored with **0-based** coordinates, so the
  begin/end shift is exercised (and reflected in the per-record histogram
  weights);
* the tabix table maps unprefixed contigs with a ``chrom_mapping``
  **add_prefix** rule;
* the VCF has a **multi-allelic** record and INFO fields of Number=A and
  Number=R, so the allele-indexed and ref-offset INFO paths are both hit;
* the allele score puts **several records at one position** -- one per ref/alt
  pair -- and each weighs 1 however wide its ``pos_end`` reaches, so both
  halves of what an allele score means by a region are pinned in a bar;
* the fragment score is **tabix-backed**, so it is the one resource here that
  reaches the vectorized (column-array) scan as a non-position kind -- its
  fragments overlap and two share a start, and each counts 1 however wide it
  is, so a weight or an ordering rule that treated a fragment like a position
  score would move a bar;
* the in-memory float and both VCF float scores are configured **without** an
  explicit view range, forcing the two-pass min/max-then-histogram scan
  (auto-ranging); the tabix and bigWig scores pin an explicit range, so they
  take the single-pass path.

Regenerate the expected files with::

    GAIN_UPDATE_GOLDEN=1 pytest tests/small/genomic_resources/\
test_statistics_golden.py

The test fails when it regenerates, so a regeneration can never be mistaken for
a passing run.  Inspect the diff before committing it.
"""
import json
import os
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.implementations.genomic_scores_impl import (
    build_score_implementation_from_resource,
)
from gain.genomic_resources.testing.builders import (
    GRRBuilder,
    a_bigwig_score,
    a_fragment_score,
    a_grr,
    a_position_score,
    a_vcf_info_score,
    an_allele_score,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
HISTOGRAMS_GOLDEN = FIXTURES / "statistics_histograms_golden.txt"
MIN_MAX_GOLDEN = FIXTURES / "statistics_min_max_golden.json"
UPDATE_ENV = "GAIN_UPDATE_GOLDEN"

# In-memory, 0-based.  Rows are [begin, end) half-open, so a record's base
# span -- the weight each value carries into the histogram -- is end - begin
# after the +1 begin shift.
#   mem_auto is auto-ranged (no view range) -> two-pass scan.
#   mem_label is a str score -> categorical histogram.
#
# The score spans two contigs, and the two-pass auto-range builds a per-contig
# min/max statistic that is then *merged* into the global view range.  The
# global minimum (0.0) lives on contig 2 and the global maximum (8.0) on
# contig 1 -- deliberately on different contigs -- so the merge is
# load-bearing: a broken merge that kept only one region's extrema (e.g. the
# last-processed contig's) would drop either the 0.0 or the 8.0, shrink the
# view range, and shift every mem_auto bar.  Spans are 1/3 (contig 1) and 2/4
# (contig 2) bases, so the histogram weights are span-weighted, not row-counts.
MEM_DATA = """
    chrom  pos_begin  pos_end  mem_auto  mem_label
    1      10         11       8.0       alpha
    1      20         23       6.0       beta
    2      10         12       0.0       alpha
    2      20         24       2.0       beta
"""

# Tabix, contigs unprefixed and mapped with add_prefix=chr.  Single-base rows
# (weight 1).  tbx_val pins an explicit 0..1 range -> single-pass.
TABIX_DATA = """
    chrom  pos_begin  pos_end  tbx_val
    1      10         10       0.1
    1      20         20       0.6
    2      30         30       0.9
"""

# bigWig, authored as bedGraph (0-based half-open).  bw_val pins an explicit
# 0..4 range -> single-pass.
BIGWIG_DATA = """
    chr1  0  2  0.0
    chr1  2  4  2.0
    chr1  4  6  4.0
"""

# In-memory allele score, 1-based.  An allele score's records legitimately
# share a position -- one per ref/alt pair -- and each weighs 1 however wide an
# optional ``pos_end`` column reaches, so the three rows at chr1:10 contribute
# three counts and the row spanning 20..29 contributes one.  Both facts are
# visible in the histogram: a weight rule that span-weighted an allele record
# would move the 0.25 bar by nine.  An explicit view range keeps the bins exact.
ALLELE_DATA = """
    chrom  pos_begin  pos_end  reference  alternative  all_val
    chr1   10         10       A          G            0.25
    chr1   10         10       A          C            0.75
    chr1   10         10       A          T            0.25
    chr1   20         29       C          T            0.25
"""

# Tabix fragment score.  Fragments overlap freely and several may share a
# start -- both ordinary data for this kind -- and each weighs 1 however wide
# it is, so the four rows contribute four counts and not 91+11+181+6 of them.
# Tabix-backed deliberately: an in-memory table serves no column arrays, so a
# fragment score authored that way would never reach the VECTORIZED scan, and
# the statistics this file pins would say nothing about the path the fragment
# kind's weights and record rule are actually measured on.
FRAGMENT_DATA = """
    chrom  pos_begin  pos_end  frg_val
    chr1   10         100      0.1
    chr1   20         30       0.9
    chr1   20         200      0.5
    chr1   40         45       0.25
"""

# VCF: vcf_af is Number=A (one value per ALT allele); vcf_ar is Number=R (a ref
# value followed by one per ALT -- the read path drops the ref and keeps the
# per-ALT values).  The ref values (5.0) differ from every ALT value so a
# ref/alt off-by-one in the read path would move a histogram bar.  vcf_type is
# a plain Number=1 string -> categorical.  chr1:10 is multi-allelic (T,C).
# Both float scores auto-range; the extremes 0.0 and 6.25 give a 100-bin
# linspace that is exactly representable, so the serialized bins are stable.
VCF_DATA = """
##fileformat=VCFv4.1
##INFO=<ID=vcf_af,Number=A,Type=Float,Description="per-alt frequency">
##INFO=<ID=vcf_ar,Number=R,Type=Float,Description="ref and per-alt values">
##INFO=<ID=vcf_type,Number=1,Type=String,Description="variant type">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  A   T,C .    .      vcf_af=0.0,6.25;vcf_ar=5.0,0.0,6.25;vcf_type=snv
chr1   20  .  G   A   .    .      vcf_af=3.125;vcf_ar=5.0,3.125;vcf_type=indel
"""  # ruff: ignore[line-too-long]


def _golden_grr_builder() -> GRRBuilder:
    """Compose the four-backend GRR used by the golden run."""
    return (
        a_grr()
        .with_resource(
            "mem_score",
            a_position_score()
            .with_zero_based()
            .with_score("mem_auto", "float", column_name="mem_auto")
            .with_histogram({"type": "number", "number_of_bins": 4})
            .with_score("mem_label", "str", column_name="mem_label")
            .with_data(MEM_DATA),
        )
        .with_resource(
            "tabix_score",
            a_position_score()
            .with_tabix()
            .with_chrom_mapping(add_prefix="chr")
            .with_score("tbx_val", "float", column_name="tbx_val")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data(TABIX_DATA),
        )
        .with_resource(
            "bigwig_score",
            a_bigwig_score()
            .with_score("bw_val", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 4.0}})
            .with_data(BIGWIG_DATA)
            .with_chrom_lens({"chr1": 100}),
        )
        .with_resource(
            "allele_score",
            an_allele_score()
            .with_score("all_val", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data(ALLELE_DATA),
        )
        .with_resource(
            "fragment_score",
            a_fragment_score()
            .with_tabix()
            .with_score("frg_val", "float")
            .with_histogram({
                "type": "number", "number_of_bins": 4,
                "view_range": {"min": 0.0, "max": 1.0}})
            .with_data(FRAGMENT_DATA),
        )
        .with_resource(
            "vcf_score",
            a_vcf_info_score().with_data(VCF_DATA),
        )
    )


@pytest.fixture
def built_statistics(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the GRR and run the statistics build; return the repo root."""
    _golden_grr_builder().build_repo(tmp_path)
    cli_manage(["repo-stats", "-R", str(tmp_path), "-j", "1"])
    return tmp_path


def _collect_histograms(repo_root: pathlib.Path) -> str:
    """Concatenate every serialized histogram, keyed by repo-relative path.

    Deterministic order (sorted relative path) so the blob is stable across
    runs; each histogram is preceded by a ``# <path>`` header line.
    """
    parts: list[str] = []
    hist_files = sorted(
        repo_root.glob("*/statistics/histogram_*.json"),
        key=lambda p: p.relative_to(repo_root).as_posix())
    # Not a bare ``assert`` -- that is stripped under ``python -O``, which would
    # let a statistics build that emitted no histograms pass this test
    # vacuously.  Fail unconditionally instead.
    if not hist_files:
        pytest.fail("statistics build produced no histogram files")
    for path in hist_files:
        rel = path.relative_to(repo_root).as_posix()
        parts.append(f"# {rel}\n{path.read_text()}")
    return "\n\n".join(parts) + "\n"


def _collect_min_max(repo_root: pathlib.Path) -> str:
    """Extract the min/max statistics carried inside the number histograms.

    The score-statistics build does not emit standalone ``min_max_*.yaml``
    files; the observed data extrema (``min_value``/``max_value``) and the
    resulting view range live inside each number histogram.  Pull them out
    into an explicit, human-readable expectation keyed by repo-relative path.
    """
    result: dict[str, dict[str, object]] = {}
    for path in sorted(
            repo_root.glob("*/statistics/histogram_*.json"),
            key=lambda p: p.relative_to(repo_root).as_posix()):
        data = json.loads(path.read_text())
        if data.get("config", {}).get("type") != "number":
            continue
        rel = path.relative_to(repo_root).as_posix()
        result[rel] = {
            "min_value": data["min_value"],
            "max_value": data["max_value"],
            "view_range": data["config"]["view_range"],
        }
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def _assert_golden(golden_path: pathlib.Path, actual: str) -> None:
    if os.environ.get(UPDATE_ENV):
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual)
        pytest.fail(
            f"golden file regenerated at {golden_path}; review the diff, "
            f"then re-run without {UPDATE_ENV}")

    assert golden_path.exists(), (
        f"missing golden file {golden_path}; regenerate it with "
        f"{UPDATE_ENV}=1")
    expected = golden_path.read_text()
    if actual != expected:
        pytest.fail(
            f"statistics output changed.\n"
            f"--- expected ({golden_path})\n{expected}\n"
            f"--- actual\n{actual}")


def test_statistics_histograms_golden(built_statistics: pathlib.Path) -> None:
    _assert_golden(HISTOGRAMS_GOLDEN, _collect_histograms(built_statistics))


def test_statistics_min_max_golden(built_statistics: pathlib.Path) -> None:
    # Issue #232 explicitly requires the min/max statistics to be asserted
    # against checked-in expected values, so this golden exists as a
    # human-readable restatement of them.  It carries no independent detection
    # power, though: min_value, max_value and view_range for every number
    # histogram already appear verbatim inside the histogram JSON that
    # test_statistics_histograms_golden compares byte-for-byte, so this test
    # cannot fail unless that one fails first.  Treat it as documentation, not
    # as a second, independent check.
    _assert_golden(MIN_MAX_GOLDEN, _collect_min_max(built_statistics))


# --- gain#430: the .tbi statistics hash must not drift -------------------
#
# Roughly 16k production GRR resources are .tbi-indexed tabix tables, and a
# resource is restatisticised the moment its statistics hash changes.  Making
# the index name manifest-resolved rather than assumed must therefore leave
# the hash of a .tbi resource byte-identical.  The two md5s inside it are the
# local htslib's bgzip/tabix output and so are environment-dependent; they are
# substituted by placeholders here and everything else is pinned verbatim.
HASH_GOLDEN = FIXTURES / "statistics_hash_tabix_golden.json"
_DATA_MD5 = "<md5 of data.txt.gz>"
_INDEX_MD5 = "<md5 of data.txt.gz.tbi>"


def test_tabix_score_statistics_hash_golden(tmp_path: pathlib.Path) -> None:
    repo = _golden_grr_builder().build_repo(tmp_path)
    resource = repo.get_resource("tabix_score")
    manifest = resource.get_manifest()
    data_md5 = manifest["data.txt.gz"].md5
    index_md5 = manifest["data.txt.gz.tbi"].md5
    assert data_md5 is not None
    assert index_md5 is not None

    statistics_hash = build_score_implementation_from_resource(
        resource).calc_statistics_hash().decode()

    _assert_golden(
        HASH_GOLDEN,
        statistics_hash
        .replace(data_md5, _DATA_MD5)
        .replace(index_md5, _INDEX_MD5),
    )
