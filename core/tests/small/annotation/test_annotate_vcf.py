# pylint: disable=W0621,C0114,C0116,W0212,W0613
import os
import pathlib
import textwrap
from typing import Any

import gain.annotation.annotate_vcf
import pysam
import pytest
import pytest_mock
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotate_utils import (
    produce_partfile_paths,
)
from gain.annotation.annotate_vcf import (
    _add_tasks_tabixed,
    _annotate_vcf,
    _count_vcf_records,
    _VCFBatchSource,
    _VCFSource,
    _VCFWriter,
    annotate_vcf,
    cli,
)
from gain.annotation.annotation_config import Attribute
from gain.annotation.annotation_factory import (
    build_annotation_pipeline,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_http_test_protocol,
    setup_denovo,
    setup_directories,
    setup_tabix,
    setup_vcf,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.testing.acgt_import import acgt_grr
from gain.utils.regions import Region

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


@pytest.fixture
def acgt_annotate_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    score_dir = tmp_path / "acgt_gpf" / "sample_score"
    setup_denovo(
        score_dir / "data.txt",
        textwrap.dedent("""
            chrom  pos_begin  score
            chr1   10         0.1
            chr2   20         0.2
            chr3   30         0.3
        """))
    (score_dir / "genomic_resource.yaml").write_text(textwrap.dedent(
        """
        type: position_score
        table:
            filename: data.txt
        scores:
            - id: score
              type: float
              name: score
        """))
    return acgt_grr(tmp_path)


def test_count_vcf_records_counts_records(sample_vcf: pathlib.Path) -> None:
    assert _count_vcf_records(str(sample_vcf), 100) == 3


def test_count_vcf_records_caps_at_limit(sample_vcf: pathlib.Path) -> None:
    assert _count_vcf_records(str(sample_vcf), 2) == 2


def _write_vcf(path: pathlib.Path, n_records: int) -> None:
    header = textwrap.dedent("""\
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT s1
        """)
    rows = "".join(f"chr1 {10 + i} . C T . . . GT 0/1\n" for i in range(
        n_records))
    setup_vcf(path, header + rows)


@pytest.mark.grr_http
def test_annotate_vcf_aborts_on_large_input_over_http_grr(
    tmp_path: pathlib.Path,
    request: pytest.FixtureRequest,
) -> None:
    if not request.config.getoption("enable_http"):
        pytest.skip("HTTP testing not enabled (use --enable-http-testing)")

    repo_path = tmp_path / "grr_repo"
    setup_directories(repo_path, {
        "score_one": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: none
                  chrom:
                    index: 0
                  pos_begin:
                    index: 1
                  pos_end:
                    index: 2
                scores:
                - id: score
                  index: 3
                  type: float
            """),
        },
    })
    setup_tabix(
        repo_path / "score_one" / "data.txt.gz",
        textwrap.dedent("""
        chr1   10   10   0.1
        chr1   11   11   0.2
        """).strip(),
        seq_col=0, start_col=1, end_col=2)

    annotation_file = tmp_path / "annotation.yaml"
    annotation_file.write_text("- position_score: score_one\n")

    big_input = tmp_path / "big.vcf"
    _write_vcf(big_input, 5001)  # trips the hard limit
    small_input = tmp_path / "small.vcf"
    _write_vcf(small_input, 2)

    with build_http_test_protocol(repo_path) as http_proto:
        grr_file = tmp_path / "grr.yaml"
        grr_file.write_text(f"type: http\nurl: {http_proto.url}\n")

        # 5001 records trip the hard limit; the guard must fire before any
        # annotation work against the remote resource.
        with pytest.raises(ValueError, match=r"score_one \(http\)"):
            cli([
                str(big_input), str(annotation_file), "--grr", str(grr_file),
                "-o", str(tmp_path / "out.vcf"), "-w", str(tmp_path / "work"),
                "-j", "1",
            ])

        # --allow-remote-resources skips the guard; a small input keeps the
        # override leg from issuing thousands of network lookups.
        cli([
            str(small_input), str(annotation_file), "--grr", str(grr_file),
            "-o", str(tmp_path / "out2.vcf"), "-w", str(tmp_path / "work2"),
            "-j", "1", "--allow-remote-resources",
        ])
        assert (tmp_path / "out2.vcf").exists()


@pytest.fixture
def sample_vcf(tmp_path: pathlib.Path) -> pathlib.Path:
    filepath = tmp_path / "sample.vcf"
    setup_vcf(filepath, textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        ##contig=<ID=chr2>
        ##contig=<ID=chr3>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT mom dad prb
        chr1   10  .  C   T   .    .      .    GT     0/0 0/0 0/1
        chr2   20  .  C   T   .    .      .    GT     0/1 0/0 0/1
        chr3   30  .  C   T   .    .      .    GT     0/1 0/1 0/1
    """))
    return filepath


def test_annotate_vcf_simple(
    tmp_path: pathlib.Path,
    acgt_annotate_grr: GenomicResourceRepo,
    sample_vcf: pathlib.Path,
) -> None:
    out_path = tmp_path / "out.vcf"
    work_dir = tmp_path / "work_dir"
    pipeline_config = [
        {"position_score": "sample_score"},
    ]

    _annotate_vcf(
        str(out_path),
        pipeline_config,
        acgt_annotate_grr.definition,
        None,
        {
            "input": str(sample_vcf),
            "reannotate": "",
            "work_dir": str(work_dir),
            "batch_size": 0,
            "region_size": 1,
            "allow_repeated_attributes": False,
            "full_reannotation": False,
            "keep_parts": False,
        },
    )

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_path)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.1", "0.2", "0.3"]


def test_annotate_vcf_simple_batch(
    tmp_path: pathlib.Path,
    acgt_annotate_grr: GenomicResourceRepo,
    sample_vcf: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    out_path = tmp_path / "out.vcf"
    work_dir = tmp_path / "work_dir"
    pipeline_config = [
        {"position_score": "sample_score"},
    ]

    spy = mocker.spy(_VCFBatchSource, "__init__")

    _annotate_vcf(
        str(out_path),
        pipeline_config,
        acgt_annotate_grr.definition,
        None,
        {
            "input": str(sample_vcf),
            "reannotate": "",
            "work_dir": str(work_dir),
            "batch_size": 1,
            "region_size": 3_000_000,
            "allow_repeated_attributes": False,
            "full_reannotation": False,
            "keep_parts": False,
        },
    )

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_path)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.1", "0.2", "0.3"]

    # assert correct batch size was actually passed to the reader
    assert len(spy.call_args.args) == 2
    assert spy.call_args.kwargs["batch_size"] == 1


def test_basic_vcf(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.1", "0.2"]


def test_annotate_vcf_non_splittable_forces_sequential(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"  # plaintext, no tabix index -> no split
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    process_graph = mocker.patch(
        "gain.task_graph.cli_tools.TaskGraphCli.process_graph")

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 5,  # must be overridden to 1 because input can't be split
        ]
    ])

    process_graph.assert_called_once()
    assert process_graph.call_args.kwargs["jobs"] == 1


def test_annotate_vcf_cli_runs_in_work_dir_and_restores_cwd(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "work"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    real_process_graph = TaskGraphCli.process_graph
    captured_cwd: dict[str, str] = {}

    def _spy(task_graph: Any, **kwargs: Any) -> bool:
        captured_cwd["value"] = os.getcwd()
        return real_process_graph(task_graph, **kwargs)

    mocker.patch.object(TaskGraphCli, "process_graph", side_effect=_spy)

    cwd_before = os.getcwd()
    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir, "-j", 1,
        ]
    ])

    assert os.path.realpath(captured_cwd["value"]) == \
        os.path.realpath(work_dir)
    assert os.getcwd() == cwd_before


def test_annotate_vcf_splittable_keeps_jobs(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"  # tabixed -> splittable
    out_file = tmp_path / "out.vcf.gz"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    process_graph = mocker.patch(
        "gain.task_graph.cli_tools.TaskGraphCli.process_graph")

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 5,  # splittable input must keep the user's -j untouched
        ]
    ])

    process_graph.assert_called_once()
    assert process_graph.call_args.kwargs["jobs"] == 5


def test_annotate_vcf_cli_preserves_bgz_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """`-o out.bgz` produces a .bgz VCF output, not .gz (regression for #54)."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.bgz"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"
    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file,
            "-o", out_file, "-w", tmp_path / "work", "-j", 1,
        ]
    ])

    assert out_file.exists()
    assert (tmp_path / "out.vcf.bgz.tbi").exists()
    assert not (tmp_path / "out.vcf.gz").exists()
    with pysam.VariantFile(str(out_file)) as vcf:
        assert len(list(vcf.fetch())) == 2


def test_annotate_vcf_cli_removes_uncompressed_working_file(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A compressed output leaves no uncompressed working file (#61).

    The task-graph framework does not auto-delete intermediate files, so
    _tabix_compress must remove the uncompressed working file (out.vcf)
    after producing out.vcf.bgz, mirroring annotate_tabular.
    """
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.bgz"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"
    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file,
            "-o", out_file, "-w", tmp_path / "work", "-j", 1,
        ]
    ])

    assert out_file.exists()
    assert not (tmp_path / "out.vcf").exists()


def test_batch(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        ##contig=<ID=chr2>
        ##contig=<ID=chr3>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr2   33  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr2   34  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr3   43  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr3   44  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--batch-size", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6"]


def test_multiallelic_vcf(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T,A   .    .      .
        chr1   24  .  C   A,G   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_multiallelic.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    result = []
    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"] for vcf in vcf_file.fetch()]
    assert result == [("0.1", "0.2"), ("0.3", "0.4")]


def test_vcf_multiple_chroms(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        ##contig=<ID=chr2>
        ##contig=<ID=chr3>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr2   33  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr2   34  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr3   43  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr3   44  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    out_file_tbi = tmp_path / "out.vcf.gz.tbi"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            # keep the work dir so the leftover-parts assertion can inspect it
            "--keep-work-dir",
        ]
    ])

    result = []
    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.1", "0.2",
                      "0.3", "0.4",
                      "0.5", "0.6"]
    assert os.path.exists(out_file_tbi)
    leftover = set(os.listdir(work_dir)) - {".task-log", ".task-status"}
    assert all(
        os.path.isdir(os.path.join(work_dir, d))
        and not os.listdir(os.path.join(work_dir, d))
        for d in leftover
    ), f"Unexpected non-empty entries in work_dir: {leftover}"


def test_annotate_vcf_float_precision(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr4>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr4   53  .  C   T   .    .      .    GT     0/1 0/0 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["0.123"]


def test_annotate_vcf_internal_attributes(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_internal_attributes.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        assert "score_1" in vcf_file.header.info
        assert "score_4" not in vcf_file.header.info
        for rec in vcf_file.fetch():
            assert "score_1" in rec.info
            assert "score_4" not in rec.info


def test_annotate_vcf_forbidden_symbol_replacement(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   A   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   25  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_forbidden_symbols.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [vcf.info["score"][0] for vcf in vcf_file.fetch()]
    assert result == ["a|b", "c|d", "e_f"]


def test_annotate_vcf_none_values(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A,G,T   .    .      .
        chr1   25  .  C   C,T   .    .      .
        chr1   26  .  C   G   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_multiallelic.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        variants = [*vcf_file.fetch()]
    assert variants[0].info["score"] == ("0.1",)
    assert variants[1].info["score"] == ("0.3", "0.4", ".")
    assert "score" not in variants[2].info
    assert "score" not in variants[3].info


def test_vcf_description_with_quotes(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   A   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   25  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_quotes_in_description.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        info = vcf_file.header.info
    assert info["score"].description == \
        'The \\"phastCons\\" computed over the tree of 100 verterbrate species'


def test_annotate_vcf_repeated_attributes(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_repeated_attributes.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--allow-repeated-attributes",
        ]
    ])

    result = []
    # pylint: disable=no-member
    with pysam.VariantFile(str(out_file)) as vcf_file:
        for vcf in vcf_file.fetch():
            result.extend([
                vcf.info["score_A0"][0],
                vcf.info["score_A1"][0],
            ])
    assert result == ["0.1", "0.101", "0.2", "0.201"]


def test_produce_partfile_paths() -> None:
    regions = [Region("chr1", 0, 1000),
               Region("chr1", 1000, 2000),
               Region("chr1", 2000, 3000)]
    expected_output = [
        "work_dir/output/input.vcf_annotation_chr1_0_1000",
        "work_dir/output/input.vcf_annotation_chr1_1000_2000",
        "work_dir/output/input.vcf_annotation_chr1_2000_3000",
    ]
    # relative input file path
    assert produce_partfile_paths(
        "src/input.vcf", regions, "work_dir/output",
    ) == expected_output
    # absolute input file path
    assert produce_partfile_paths(
        "/home/user/src/input.vcf", regions, "work_dir/output",
    ) == expected_output


def test_add_tasks_tabixed_rejects_uncompressed_output_path() -> None:
    """``_add_tasks_tabixed`` must reject an ``output_path`` with no
    compression suffix.

    Regression for iossifovlab/gain#62: without the suffix the derived
    ``working_path`` equals ``output_path``, so the compress task would
    ``tabix_compress(out, out, force=True)`` and truncate the file in place.
    Make the precondition explicit so a future caller can't silently
    trigger data loss.
    """
    with pytest.raises(AssertionError, match="compression suffix"):
        _add_tasks_tabixed(
            args={},
            task_graph=TaskGraph(),
            output_path="out.vcf",
            pipeline_config=[],
            grr_definition={},
        )


def test_vcf_source_missing_alts(
    tmp_path: pathlib.Path,
) -> None:
    vcf_path = tmp_path / "data.vcf"
    setup_vcf(vcf_path, """
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   .   .    .      .    GT     0/0 0/1 0/0
        chr1   25  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    with _VCFSource(str(vcf_path)) as source:
        result = list(source.fetch())
        assert len(result) == 2
        assert result[0].annotations[0].annotatable == \
            VCFAllele("chr1", 23, "C", "T")
        assert result[1].annotations[0].annotatable == \
            VCFAllele("chr1", 25, "C", "A")


def test_cli_nonexistent_input_file(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    root_path = annotate_directory_fixture
    in_file = root_path / "blabla_does_not_exist_input.vcf"
    out_file = tmp_path / "out.vcf"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    with pytest.raises(
        ValueError,
        match=r"blabla_does_not_exist_input.vcf does not exist!",
    ):
        cli([
            str(a) for a in [
                in_file,
                annotation_file,
                "--grr", grr_file,
                "-o", out_file,
                "-w", work_dir,
                "-j", 1,
            ]
        ])


def test_writer_does_not_omit_literal_zeros_from_info(
    sample_vcf: pathlib.Path,
) -> None:
    attributes = [
        Attribute("score_1", "source_number",
                      internal=False, parameters={}),
    ]

    with pysam.VariantFile(str(sample_vcf)) as vcf:
        variant = next(vcf.fetch())

    variant.header.info.add("score_1", "A", "String", "blabla")

    _VCFWriter._update_variant(
        variant,
        [{"score_1": 0}],
        attributes,
        [],
    )

    assert variant.info["score_1"] == ("0",)


def test_writer_does_not_write_empty_values_into_info(
    sample_vcf: pathlib.Path,
) -> None:
    attributes = [
        Attribute("score_1", "source_string",
                      internal=False, parameters={}),
        Attribute("score_2", "source_bool",
                      internal=False, parameters={}),
    ]

    with pysam.VariantFile(str(sample_vcf)) as vcf:
        variant = next(vcf.fetch())

    variant.header.info.add("score_1", "A", "String", "blabla")
    variant.header.info.add("score_2", "A", "String", "blabla")

    _VCFWriter._update_variant(
        variant,
        [{"score_1": "", "score_2": False}],
        attributes,
        [],
    )

    assert "score_1" not in variant.info
    assert "score_2" not in variant.info


def test_vcf_region_boundary(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   1   .  C   A   .    .      .    GT     0/1 0/0 0/0
        chr1   2   .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   3   .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   4   .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   5   .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   6   .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   7   .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_quotes_in_description.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--region-size", "2",
        ]
    ])

    variants = 0
    with pysam.VariantFile(str(out_file)) as vcf_file:
        for _ in vcf_file.fetch():
            variants += 1
    assert variants == 7


def test_vcf_keep_parts(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch(
        "gain.annotation.annotate_utils."
        "get_chromosome_length_tabix",
        return_value=47,
    )

    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        ##contig=<ID=chr2>
        ##contig=<ID=chr3>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr2   33  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr2   34  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr3   43  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr3   44  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    out_file_tbi = tmp_path / "out.vcf.gz.tbi"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--keep-parts",
        ]
    ])

    assert os.path.exists(out_file)
    assert os.path.exists(out_file_tbi)
    expected = {
        ".task-log",
        ".task-status",
        "in.vcf.gz_annotation_chr1_1_47",
        "in.vcf.gz_annotation_chr2_1_47",
        "in.vcf.gz_annotation_chr3_1_47",
    }
    actual = set(os.listdir(work_dir))
    annotator_dirs = actual - expected
    assert all(
        os.path.isdir(os.path.join(work_dir, d))
        and not os.listdir(os.path.join(work_dir, d))
        for d in annotator_dirs
    ), f"Unexpected non-empty entries in work_dir: {annotator_dirs}"
    assert expected.issubset(actual)


def test_vcf_cross_region_boundary(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF    ALT  QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   1   .  CAAAA  C    .    .      .    GT     0/1 0/0 0/0
        chr1   2   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   3   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   4   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   5   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   6   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   7   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_quotes_in_description.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--region-size", "2",
        ]
    ])

    variants = 0
    with pysam.VariantFile(str(out_file)) as vcf_file:
        for _ in vcf_file.fetch():
            variants += 1
    assert variants == 7


def test_annotate_vcf_no_regions(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF    ALT  QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   1   .  CAAAA  C    .    .      .    GT     0/1 0/0 0/0
        chr1   2   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   3   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   4   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   5   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   6   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
        chr1   7   .  CAAAA  C    .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_quotes_in_description.yaml"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_vcf, "_annotate_vcf")
    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--region-size", "0",
        ]
    ])

    assert spy.call_count == 1


def test_annotate_vcf_version_report(
    capsys: pytest.CaptureFixture,
) -> None:
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli(["--version"])

    out, _err = capsys.readouterr()
    assert out.startswith("GAIn version: ")


def _build_annotate_vcf_args(**overrides: Any) -> dict[str, Any]:
    """Build args dict for annotate_vcf function with sensible defaults."""
    defaults = {
        "batch_size": 0,
    }
    defaults.update(overrides)
    return defaults


def test_annotate_vcf_function_basic(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with basic VCF data."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [v.info["score"][0] for v in vcf_file.fetch()]
    assert result == ["0.1", "0.2"]


def test_annotate_vcf_function_preserves_bgz_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """The library annotate_vcf() honors an explicit .bgz output."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   23  .  C   T   .    .      .
        chr1   24  .  C   A   .    .      .
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf.bgz"
    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline = build_annotation_pipeline([{"position_score": "one"}], grr)
    args = _build_annotate_vcf_args()

    annotate_vcf(str(in_file), pipeline, str(out_file), args)

    assert out_file.exists()
    assert not (tmp_path / "out.vcf.gz").exists()
    with pysam.VariantFile(str(out_file)) as vcf_file:
        assert len(list(vcf_file.fetch())) == 2


def test_annotate_vcf_function_with_batch_mode(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Test annotate_vcf function with batch processing."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    spy = mocker.spy(_VCFBatchSource, "__init__")

    # Test annotate_vcf function with batch mode
    args = _build_annotate_vcf_args(batch_size=10)

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output
    with pysam.VariantFile(str(out_file)) as vcf_file:
        result = [v.info["score"][0] for v in vcf_file.fetch()]
    assert result == ["0.1", "0.2"]

    # Verify batch mode was used
    assert len(spy.call_args.args) == 2
    assert spy.call_args.kwargs["batch_size"] == 10


def test_annotate_vcf_function_with_region(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with region parameter."""
    # Note: Region filtering requires indexed VCF
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
        chr1   50  .  G   C   .    .      .    GT     0/1 0/0 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    # Create indexed VCF file
    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function with region
    # Should only annotate positions 23-30
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        region=Region("chr1", 1, 30),
    )

    # Verify output - position 50 should be excluded
    with pysam.VariantFile(str(tmp_path / "out.vcf.gz")) as vcf_file:
        result = [(v.pos, v.info["score"][0]) for v in vcf_file.fetch()]
    assert len(result) == 2
    assert result == [(23, "0.1"), (24, "0.2")]


def test_annotate_vcf_function_with_attributes_to_delete(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with attributes_to_delete parameter."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##INFO=<ID=old_score,Number=1,Type=Float,Description="Old score">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      old_score=999    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      old_score=888    GT     0/0 0/1 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function with attributes to delete
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        attributes_to_delete=["old_score"],
    )

    # Verify output - old_score should be removed
    with pysam.VariantFile(str(out_file)) as vcf_file:
        for variant in vcf_file.fetch():
            assert "old_score" not in variant.info
            assert "score" in variant.info


def test_annotate_vcf_function_multiallelic(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with multiallelic variants."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T,A .    .      .    GT     0/1 0/0 0/0
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    setup_vcf(in_file, in_content)

    # Build pipeline with allele score
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"allele_score": "two"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output - should have two scores for two alleles
    with pysam.VariantFile(str(out_file)) as vcf_file:
        for variant in vcf_file.fetch():
            assert "score" in variant.info
            # Multiallelic should have list of scores
            scores = variant.info["score"]
            assert len(scores) == 2


def test_annotate_vcf_function_with_compressed_input(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with compressed input file."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf"

    grr_file = root_path / "grr.yaml"

    # Create compressed input file
    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function with compressed input
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output was compressed
    assert (tmp_path / "out.vcf.gz").exists()

    # Verify content
    with pysam.VariantFile(str(tmp_path / "out.vcf.gz")) as vcf_file:
        result = [v.info["score"][0] for v in vcf_file.fetch()]
    assert result == ["0.1", "0.2"]


def test_annotate_vcf_function_with_compressed_input_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_vcf function with compressed input file."""
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        chr1   23  .  C   T   .    .      .    GT     0/1 0/0 0/0
        chr1   24  .  C   A   .    .      .    GT     0/0 0/1 0/0
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"

    grr_file = root_path / "grr.yaml"

    # Create compressed input file
    setup_vcf(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_vcf function with compressed input
    args = _build_annotate_vcf_args()

    annotate_vcf(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output was compressed
    assert (tmp_path / "out.vcf.gz").exists()

    # Verify content
    with pysam.VariantFile(str(tmp_path / "out.vcf.gz")) as vcf_file:
        result = [v.info["score"][0] for v in vcf_file.fetch()]
    assert result == ["0.1", "0.2"]
