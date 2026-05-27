# pylint: disable=W0621,C0114,C0116,W0212,W0613
import gzip
import logging
import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import VCFAllele
from gain.annotation.prepare_tabular import (
    _build_argument_parser,
    _build_direct_sort_plan,
    _build_indirect_sort_plan,
    _default_output_path,
    _detect_input_separator,
    _read_header,
    cli,
)
from gain.annotation.record_to_annotatable import (
    RecordToCNVAllele,
    RecordToPosition,
    RecordToRegion,
    RecordToVcfAllele,
    VcfLikeRecordToVcfAllele,
)
from gain.genomic_resources.testing import (
    setup_denovo,
    setup_genome,
)
from pysam import TabixFile

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


# --- unit tests for helpers ---------------------------------------------


@pytest.mark.parametrize(
    "input_path,expected", [
        ("a/foo.tsv", "a/foo.sorted.tsv.bgz"),
        ("a/foo.tsv.gz", "a/foo.sorted.tsv.bgz"),
        ("a/foo.tsv.bgz", "a/foo.sorted.tsv.bgz"),
        ("a/foo.csv", "a/foo.sorted.tsv.bgz"),
        ("a/foo.csv.gz", "a/foo.sorted.tsv.bgz"),
        ("a/foo.CSV", "a/foo.sorted.tsv.bgz"),
        ("a/foo.txt", "a/foo.sorted.tsv.bgz"),
        ("a/foo.txt.gz", "a/foo.sorted.tsv.bgz"),
        ("a/data", "a/data.sorted.tsv.bgz"),
    ],
)
def test_default_output_path(input_path: str, expected: str) -> None:
    assert _default_output_path(input_path) == expected


def test_read_header_plain(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "x.tsv"
    p.write_text("chrom\tpos\tref\talt\n1\t1\tA\tT\n")
    assert _read_header(str(p), "\t") == ["chrom", "pos", "ref", "alt"]


def test_read_header_gzipped(tmp_path: pathlib.Path) -> None:
    p = tmp_path / "x.tsv.gz"
    with gzip.open(p, "wt") as f:
        f.write("#chrom\tpos\n1\t1\n")
    assert _read_header(str(p), "\t") == ["chrom", "pos"]


def test_direct_sort_plan_position() -> None:
    header = ["chrom", "pos", "score"]
    r2a = RecordToPosition(("chrom", "pos"), None)
    plan = _build_direct_sort_plan(r2a, header)
    assert plan.output_header == header
    assert plan.chrom_col_idx == 0
    assert plan.sort_keys == [(2, "n")]
    assert plan.tabix_seq_col == 0
    assert plan.tabix_start_col == 1
    assert plan.tabix_end_col == 1


def test_direct_sort_plan_region() -> None:
    header = ["chrom", "pos_beg", "pos_end", "score"]
    r2a = RecordToRegion(("chrom", "pos_beg", "pos_end"), None)
    plan = _build_direct_sort_plan(r2a, header)
    assert plan.sort_keys == [(2, "n"), (3, "n")]
    assert (plan.tabix_seq_col, plan.tabix_start_col, plan.tabix_end_col) \
        == (0, 1, 2)


def test_direct_sort_plan_vcf_allele() -> None:
    header = ["chrom", "pos", "ref", "alt", "score"]
    r2a = RecordToVcfAllele(("chrom", "pos", "ref", "alt"), None)
    plan = _build_direct_sort_plan(r2a, header)
    assert plan.sort_keys == [(2, "n"), (3, ""), (4, "")]


def test_direct_sort_plan_cnv_allele() -> None:
    header = ["chrom", "pos_beg", "pos_end", "cnv_type", "score"]
    r2a = RecordToCNVAllele(
        ("chrom", "pos_beg", "pos_end", "cnv_type"), None)
    plan = _build_direct_sort_plan(r2a, header)
    assert plan.sort_keys == [(2, "n"), (3, "n")]
    assert (plan.tabix_seq_col, plan.tabix_start_col, plan.tabix_end_col) \
        == (0, 1, 2)


def test_indirect_sort_plan_vcf_like_injects_columns() -> None:
    header = ["vcf_like", "score"]
    r2a = VcfLikeRecordToVcfAllele(("vcf_like",), None)
    plan = _build_indirect_sort_plan(
        r2a, header, {"vcf_like": "chr1:4:C:T"})
    assert plan.output_header == \
        ["vcf_like", "score", "chrom", "pos", "ref", "alt"]
    # injected at indexes 2..5; chrom is at 2
    assert plan.chrom_col_idx == 2
    assert plan.sort_keys == [(4, "n"), (5, ""), (6, "")]
    assert (plan.tabix_seq_col, plan.tabix_start_col, plan.tabix_end_col) \
        == (2, 3, 3)
    assert plan.injected_count == 4
    assert plan.expected_annotatable_type is VCFAllele
    # inject helper returns the right values for a sample row
    assert plan.inject({"vcf_like": "chr1:4:C:T", "score": "0.1"}) \
        == ["chr1", "4", "C", "T"]


def test_indirect_sort_plan_collision_errors() -> None:
    header = ["vcf_like", "chrom"]  # "chrom" collides with injection
    r2a = VcfLikeRecordToVcfAllele(("vcf_like",), None)
    with pytest.raises(ValueError, match="cannot inject sort columns"):
        _build_indirect_sort_plan(
            r2a, header, {"vcf_like": "chr1:4:C:T", "chrom": "x"})


# --- CLI integration tests -----------------------------------------------


def _read_gz_text(path: pathlib.Path) -> str:
    with gzip.open(path, "rt") as f:
        return f.read()


def test_cli_vcf_allele_lex_sort(tmp_path: pathlib.Path) -> None:
    """No reference genome → lexicographic chromosome sort."""
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos ref alt score
        2     200 C   T   0.9
        1     300 A   G   0.5
        1     100 G   C   0.7
        1     100 G   A   0.3
        2     100 A   T   0.8
    """)
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file)])

    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos\tref\talt\tscore
        1\t100\tG\tA\t0.3
        1\t100\tG\tC\t0.7
        1\t300\tA\tG\t0.5
        2\t100\tA\tT\t0.8
        2\t200\tC\tT\t0.9
        """)
    assert (tmp_path / "out.tsv.gz.tbi").exists()


def test_cli_tabix_index_is_queryable(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos ref alt score
        2     200 C   T   0.9
        1     100 G   C   0.7
        2     100 A   T   0.8
    """)
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file)])

    with TabixFile(str(out_file)) as tf:
        assert set(tf.contigs) == {"1", "2"}
        rows_chr2 = list(tf.fetch("2", 0, 1000))
        assert rows_chr2 == ["2\t100\tA\tT\t0.8", "2\t200\tC\tT\t0.9"]


def _setup_foobar_grr(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a GRR with a 'foobar_genome' resource (foo before bar)."""
    grr_root = tmp_path / "grr_root"
    setup_genome(
        grr_root / "foobar_genome" / "chrAll.fa",
        """
            >foo
            NNACCCAAAC
            GGGCCTTCCN
            NNNA
            >bar
            NNGGGCCTTC
            CACGACCCAA
            NN
        """,
    )
    return grr_root


def test_cli_position_rank_prefix_sort_uses_ref_genome_order(
    tmp_path: pathlib.Path,
) -> None:
    """With a reference genome, chromosomes follow ref-genome order
    (foo before bar — opposite of lexicographic)."""
    grr_root = _setup_foobar_grr(tmp_path)
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos score
        bar   5   0.1
        foo   3   0.2
        bar   20  0.3
        foo   17  0.4
    """)
    out_file = tmp_path / "out.tsv.gz"
    cli([
        str(in_file), "-o", str(out_file),
        "--grr-directory", str(grr_root),
        "-R", "foobar_genome",
    ])

    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos\tscore
        foo\t3\t0.2
        foo\t17\t0.4
        bar\t5\t0.1
        bar\t20\t0.3
        """)


def test_cli_unknown_chromosome_sorts_to_end_with_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    grr_root = _setup_foobar_grr(tmp_path)
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos score
        foo   3   0.2
        zzz   5   0.1
        bar   20  0.3
    """)
    out_file = tmp_path / "out.tsv.gz"
    with caplog.at_level(logging.WARNING, logger="prepare_tabular"):
        cli([
            str(in_file), "-o", str(out_file),
            "--grr-directory", str(grr_root),
            "-R", "foobar_genome",
        ])

    # Unknown chromosomes sort to the end of the file
    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos\tscore
        foo\t3\t0.2
        bar\t20\t0.3
        zzz\t5\t0.1
        """)
    assert any("zzz" in r.message and "not found" in r.message
               for r in caplog.records)


def test_cli_region_sort(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos_beg pos_end score
        1     100     200     0.1
        1     50      120     0.2
        1     50      80      0.3
    """)
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file)])

    # Same chrom (col is lex-sorted), then pos_beg ascending, then pos_end
    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos_beg\tpos_end\tscore
        1\t50\t80\t0.3
        1\t50\t120\t0.2
        1\t100\t200\t0.1
        """)
    # Tabix indexed on (chrom, pos_beg, pos_end)
    with TabixFile(str(out_file)) as tf:
        assert list(tf.fetch("1", 0, 60)) == [
            "1\t50\t80\t0.3", "1\t50\t120\t0.2",
        ]


def test_cli_skip_sort_on_gzipped_input(tmp_path: pathlib.Path) -> None:
    """--skip-sort still bgzips and tabix-indexes; preserves row order."""
    grr_root = _setup_foobar_grr(tmp_path)
    plain = tmp_path / "in.tsv"
    setup_denovo(plain, """
        chrom pos score
        foo   3   0.2
        foo   17  0.4
        bar   5   0.1
        bar   20  0.3
    """)
    gz_in = tmp_path / "in.tsv.gz"
    with gzip.open(gz_in, "wb") as dst:
        dst.write(plain.read_bytes())

    out_file = tmp_path / "out.tsv.gz"
    cli([
        str(gz_in), "-o", str(out_file), "--skip-sort",
        "--grr-directory", str(grr_root), "-R", "foobar_genome",
    ])

    # Order preserved exactly (no sort performed)
    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos\tscore
        foo\t3\t0.2
        foo\t17\t0.4
        bar\t5\t0.1
        bar\t20\t0.3
        """)
    with TabixFile(str(out_file)) as tf:
        assert list(tf.fetch("foo", 0, 20)) == [
            "foo\t3\t0.2", "foo\t17\t0.4",
        ]


def test_cli_vcf_like_injection(tmp_path: pathlib.Path) -> None:
    """Indirect R2A (vcf_like) sorts by parsed annotatable and injects
    standard chrom/pos/ref/alt columns into the output."""
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        vcf_like        score
        2:200:C:T       0.9
        1:300:A:G       0.5
        1:100:G:C       0.7
        1:100:G:A       0.3
    """)
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file)])

    assert _read_gz_text(out_file) == textwrap.dedent("""\
        vcf_like\tscore\tchrom\tpos\tref\talt
        1:100:G:A\t0.3\t1\t100\tG\tA
        1:100:G:C\t0.7\t1\t100\tG\tC
        1:300:A:G\t0.5\t1\t300\tA\tG
        2:200:C:T\t0.9\t2\t200\tC\tT
        """)
    # Tabix points at the injected chrom (col 2) and pos (col 3)
    with TabixFile(str(out_file)) as tf:
        assert set(tf.contigs) == {"1", "2"}
        assert len(list(tf.fetch("1", 0, 1000))) == 3


def test_cli_default_output_path(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.tsv"
    setup_denovo(in_file, """
        chrom pos
        1     10
    """)
    cli([str(in_file)])
    assert (tmp_path / "in.sorted.tsv.bgz").exists()
    assert (tmp_path / "in.sorted.tsv.bgz.tbi").exists()


# --- input separator handling --------------------------------------------


@pytest.mark.parametrize(
    "input_path,expected", [
        ("data.csv", ","),
        ("data.CSV", ","),
        ("data.csv.gz", ","),
        ("data.tsv", "\t"),
        ("data.tsv.gz", "\t"),
        ("data.txt", "\t"),
        ("data", "\t"),
    ],
)
def test_detect_input_separator(input_path: str, expected: str) -> None:
    assert _detect_input_separator(input_path) == expected


def test_help_excludes_inapplicable_options() -> None:
    """Pipeline / gene-models / repeated-attributes flags shouldn't be
    surfaced — they don't apply to a sort+index tool."""
    parser = _build_argument_parser()
    help_text = parser.format_help()
    # No annotation-pipeline positional, no gene-models, no -ar flag.
    assert "pipeline" not in help_text.lower().split("positional arguments:")[1]
    assert "--gene-models-resource-id" not in help_text
    assert "-G " not in help_text
    assert "--allow-repeated-attributes" not in help_text
    assert " -ar" not in help_text


def test_cli_csv_input_produces_tsv_output(tmp_path: pathlib.Path) -> None:
    """A .csv input is parsed with ',' but the output is always TSV
    (tabix requires tabs)."""
    in_file = tmp_path / "in.csv"
    in_file.write_text(
        "chrom,pos,ref,alt,score\n"
        "2,200,C,T,0.9\n"
        "1,300,A,G,0.5\n"
        "1,100,G,A,0.3\n",
    )
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file)])

    assert _read_gz_text(out_file) == textwrap.dedent("""\
        chrom\tpos\tref\talt\tscore
        1\t100\tG\tA\t0.3
        1\t300\tA\tG\t0.5
        2\t200\tC\tT\t0.9
        """)
    # Tabix index is queryable on the tab-separated output.
    with TabixFile(str(out_file)) as tf:
        assert list(tf.fetch("1", 0, 1000)) == [
            "1\t100\tG\tA\t0.3", "1\t300\tA\tG\t0.5",
        ]


def test_cli_explicit_input_separator_overrides_detection(
    tmp_path: pathlib.Path,
) -> None:
    """A .csv input with --input-separator '\\t' is parsed as TSV."""
    in_file = tmp_path / "in.csv"  # extension is misleading on purpose
    in_file.write_text("chrom\tpos\n1\t10\n1\t5\n")
    out_file = tmp_path / "out.tsv.gz"
    cli([str(in_file), "-o", str(out_file), "--input-separator", "\t"])

    assert _read_gz_text(out_file) == "chrom\tpos\n1\t5\n1\t10\n"


def test_cli_rejects_tab_in_csv_cell(tmp_path: pathlib.Path) -> None:
    """A tab inside a CSV cell would break the tab-separated output."""
    in_file = tmp_path / "in.csv"
    in_file.write_text("chrom,pos\n1,10\n1,2\t3\n")
    out_file = tmp_path / "out.tsv.gz"
    with pytest.raises(ValueError, match="tab character"):
        cli([str(in_file), "-o", str(out_file)])
