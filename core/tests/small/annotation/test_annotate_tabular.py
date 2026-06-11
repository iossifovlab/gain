# pylint: disable=W0621,C0114,C0116,W0212,W0613,C0302
import gzip
import logging
import os
import pathlib
import textwrap
from typing import Any

import gain.annotation.annotate_tabular
import pysam
import pytest
import pytest_mock
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.annotate_tabular import (
    _add_tasks_tabixed,
    _adjust_default_input_separator,
    _count_tabular_rows,
    _CSVBatchSource,
    _CSVBatchWriter,
    _CSVHeader,
    _CSVSource,
    _CSVWriter,
    annotate_tabular,
    cli,
)
from gain.annotation.annotation_factory import (
    build_annotation_pipeline,
)
from gain.annotation.processing_pipeline import (
    Annotation,
    AnnotationsWithSource,
)
from gain.annotation.record_to_annotatable import (
    build_annotatable_from_dict,
    build_record_to_annotatable,
)
from gain.genomic_resources.genomic_context_base import (
    GenomicContext,
    SimpleGenomicContext,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_http_test_protocol,
    setup_denovo,
    setup_directories,
    setup_genome,
    setup_gzip,
    setup_tabix,
)
from gain.task_graph.cli_tools import TaskGraphCli
from gain.task_graph.graph import TaskGraph
from gain.task_graph.logging import FsspecHandler
from gain.utils.regions import Region as GenomicRegion

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


@pytest.mark.parametrize(
    "record,expected", [
        ({"chrom": "chr1", "pos": "3"},
         Position("chr1", 3)),

        ({"chrom": "chr1", "pos": "4", "ref": "C", "alt": "CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"vcf_like": "chr1:4:C:CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"chrom": "chr1", "pos_beg": "4", "pos_end": "30"},
         Region("chr1", 4, 30)),
    ],
)
def test_default_columns(
        record: dict[str, str], expected: Annotatable) -> None:
    annotatable = build_record_to_annotatable(
        {}, set(record.keys())).build(record)
    assert str(annotatable) == str(expected)


@pytest.mark.parametrize(
    "record,expected", [
        ({"location": "chr1:13", "variant": "sub(A->T)"},
         VCFAllele("chr1", 13, "A", "T")),

        ({"location": "chr1:3-13", "variant": "duplication"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DUPLICATION)),
    ],
)
def test_cshl_variants_without_context(
    record: dict[str, str], expected: Annotatable,
) -> None:
    allele = build_record_to_annotatable(
            {}, set(record.keys())).build(record)
    assert str(allele) == str(expected)


@pytest.mark.parametrize(
    "record", [
        {"location": "chr1:13", "variant": "ins(TT)"},
        {"location": "chr1:13", "variant": "del(2)"},
    ],
)
def test_cshl_variants_without_context_indels(
    record: dict[str, str],
) -> None:
    with pytest.raises(
            ValueError, match="genome is required for ins/del variants"):

        build_record_to_annotatable(
                {}, set(record.keys())).build(record)


@pytest.fixture
def gc_fixture(tmp_path: pathlib.Path) -> GenomicContext:
    genome = setup_genome(
        tmp_path / "acgt_gpf" / "genome" / "allChr.fa",
        f"""
        >chr1
        {25 * "ACGT"}
        >chr2
        {25 * "ACGT"}
        >chr3
        {25 * "ACGT"}
        """,
    )
    return SimpleGenomicContext(
        {"reference_genome": genome}, source="test_gc_fixture")


@pytest.mark.parametrize(
    "record,expected", [
        ({"chrom": "chr1", "pos": "3"},
         Position("chr1", 3)),

        ({"chrom": "chr1", "pos": "4", "ref": "C", "alt": "CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"vcf_like": "chr1:4:C:CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"chrom": "chr1", "pos_beg": "4", "pos_end": "30"},
         Region("chr1", 4, 30)),

        ({"location": "chr1:13", "variant": "sub(A->T)"},
         VCFAllele("chr1", 13, "A", "T")),

        ({"location": "chr1:14", "variant": "ins(A)"},
         VCFAllele("chr1", 13, "A", "AA")),

        ({"location": "chr1:13", "variant": "del(1)"},
         VCFAllele("chr1", 12, "TA", "T")),

        ({"location": "chr1:3-13", "variant": "duplication"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DUPLICATION)),

        ({"location": "chr1:3-13", "variant": "CNV+"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DUPLICATION)),

        ({"location": "chr1:3-13", "variant": "deletion"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DELETION)),

        ({"location": "chr1:3-13", "variant": "CNV-"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DELETION)),
    ],
)
def test_build_record(
        record: dict[str, str],
        expected: Annotatable,
        gc_fixture: GenomicContext) -> None:
    ref_genome = gc_fixture.get_reference_genome()
    annotatable = build_record_to_annotatable(
        {}, set(record.keys()), ref_genome,
    ).build(record)
    assert str(annotatable) == str(expected)


@pytest.mark.parametrize(
    "record,expected", [
        ({"chrom": "chr1", "pos": "3"},
         Position("chr1", 3)),

        ({"chrom": "chr1", "pos": "4", "ref": "C", "alt": "CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"vcf_like": "chr1:4:C:CT"},
         VCFAllele("chr1", 4, "C", "CT")),

        ({"chrom": "chr1", "pos_beg": "4", "pos_end": "30"},
         Region("chr1", 4, 30)),

        ({"location": "chr1:13", "variant": "sub(A->T)"},
         VCFAllele("chr1", 13, "A", "T")),

        ({"location": "chr1:14", "variant": "ins(A)"},
         VCFAllele("chr1", 13, "A", "AA")),

        ({"location": "chr1:13", "variant": "del(1)"},
         VCFAllele("chr1", 12, "TA", "T")),

        ({"location": "chr1:3-13", "variant": "duplication"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DUPLICATION)),

        ({"location": "chr1:3-13", "variant": "CNV+"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DUPLICATION)),

        ({"location": "chr1:3-13", "variant": "deletion"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DELETION)),

        ({"location": "chr1:3-13", "variant": "CNV-"},
         CNVAllele("chr1", 3, 13, CNVAllele.Type.LARGE_DELETION)),
    ],
)
def test_build_annotatable(
    record: dict[str, str],
    expected: Annotatable,
    gc_fixture: GenomicContext,
) -> None:
    ref_genome = gc_fixture.get_reference_genome()
    annotatable = build_annotatable_from_dict(record, ref_genome)
    assert str(annotatable) == str(expected)


def test_build_record_to_annotatable_failures() -> None:
    with pytest.raises(
            ValueError, match="no record to annotatable could be found"):
        build_record_to_annotatable({}, set())

    with pytest.raises(
            ValueError, match="no record to annotatable could be found"):
        build_record_to_annotatable({"gosho": "pesho"}, set())


@pytest.mark.parametrize(
    "parameters,record,expected", [
        ({"col_chrom": "chromosome", "col_pos": "position"},
         {"chromosome": "chr1", "position": "4", "ref": "C", "alt": "CT"},
         VCFAllele("chr1", 4, "C", "CT")),
    ],
)
def test_renamed_columns(
        parameters: dict[str, str],
        record: dict[str, str],
        expected: Annotatable) -> None:
    annotatable = build_record_to_annotatable(
        parameters, set(record.keys())).build(record)
    assert str(annotatable) == str(expected)


def test_count_tabular_rows_skips_header(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.txt"
    in_file.write_text("chrom\tpos\nchr1\t1\nchr1\t2\nchr1\t3\n")
    assert _count_tabular_rows(str(in_file), 100) == 3


def test_count_tabular_rows_caps_at_limit(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.txt"
    rows = "".join(f"chr1\t{i}\n" for i in range(50))
    in_file.write_text(f"chrom\tpos\n{rows}")
    assert _count_tabular_rows(str(in_file), 10) == 10


def test_count_tabular_rows_handles_gzip(tmp_path: pathlib.Path) -> None:
    in_file = tmp_path / "in.txt.gz"
    with gzip.open(in_file, "wt") as out_file:
        out_file.write("chrom\tpos\nchr1\t1\nchr1\t2\n")
    assert _count_tabular_rows(str(in_file), 100) == 2


def test_renamed_columns_excludes() -> None:
    record = {
        "chromosome": "chr1",
        "position": "4",
        "ref": "C",
        "alt": "CT",
    }
    annotatable = build_record_to_annotatable(
        {
            "col_chrom": "chromosome",
            "col_pos": "position",
            "col_alt": "-",
        },
        set(record.keys()),
    ).build(record)
    assert str(annotatable) == str(Position("chr1", 4))


def test_annotate_tabular_basic_setup(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_non_splittable_forces_sequential(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"  # plaintext, no tabix index -> no split
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    process_graph = mocker.patch(
        "gain.task_graph.cli_tools.TaskGraphCli.process_graph")

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 5,  # must be overridden to 1 because input can't be split
        ]
    ])

    process_graph.assert_called_once()
    assert process_graph.call_args.kwargs["jobs"] == 1


def test_annotate_tabular_cli_runs_in_work_dir_and_restores_cwd(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"
    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"
    setup_denovo(in_file, in_content)

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

    # During the run, the working directory is the work_dir so that
    # worker-side htslib index downloads land inside it.
    assert os.path.realpath(captured_cwd["value"]) == \
        os.path.realpath(work_dir)
    # The original working directory is restored once the tool finishes.
    assert os.getcwd() == cwd_before


@pytest.mark.grr_http
def test_annotate_tabular_http_grr_contains_tbi_in_work_dir(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
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
        chr1   23   23   0.1
        chr1   24   24   0.2
        """).strip(),
        seq_col=0, start_col=1, end_col=2)

    in_file = tmp_path / "in.txt"
    setup_denovo(in_file, textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """))

    with build_http_test_protocol(repo_path) as http_proto:
        annotation_file = tmp_path / "annotation.yaml"
        annotation_file.write_text("- position_score: score_one\n")
        grr_file = tmp_path / "grr.yaml"
        grr_file.write_text(f"type: http\nurl: {http_proto.url}\n")

        out_file = tmp_path / "out.txt"
        work_dir = tmp_path / "work"

        # Launch from a clean directory: htslib downloads the remote .tbi
        # index relative to the working directory, and that must be work_dir
        # rather than the directory the user launched the tool from.
        launch_dir = tmp_path / "launch"
        launch_dir.mkdir()
        monkeypatch.chdir(launch_dir)
        cwd_before = os.getcwd()

        cli([
            str(a) for a in [
                in_file, annotation_file, "--grr", grr_file,
                "-o", out_file, "-w", work_dir, "-j", 1,
                # keep the work dir so the downloaded .tbi can be inspected
                "--keep-work-dir",
            ]
        ])

    assert list(launch_dir.glob("*.tbi")) == [], \
        "remote tabix index leaked into the launch directory"
    assert list(work_dir.rglob("*.tbi")), \
        "expected the remote tabix index to be contained in work_dir"
    assert os.getcwd() == cwd_before
    assert out_file.read_text().splitlines()[0].split("\t")[-1] == "score"


@pytest.mark.grr_http
def test_annotate_tabular_aborts_on_large_input_over_http_grr(
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
        chr1   23   23   0.1
        chr1   24   24   0.2
        """).strip(),
        seq_col=0, start_col=1, end_col=2)

    big_input = tmp_path / "big.txt"
    rows = "".join(f"chr1\t{23 + i}\n" for i in range(5001))
    big_input.write_text(f"chrom\tpos\n{rows}")

    small_input = tmp_path / "small.txt"
    small_input.write_text("chrom\tpos\nchr1\t23\nchr1\t24\n")

    with build_http_test_protocol(repo_path) as http_proto:
        annotation_file = tmp_path / "annotation.yaml"
        annotation_file.write_text("- position_score: score_one\n")
        grr_file = tmp_path / "grr.yaml"
        grr_file.write_text(f"type: http\nurl: {http_proto.url}\n")

        # 5001 rows trips the hard limit; the guard must fire before any
        # annotation work is attempted against the remote resource.
        with pytest.raises(ValueError, match=r"score_one \(http\)"):
            cli([
                str(big_input), str(annotation_file), "--grr", str(grr_file),
                "-o", str(tmp_path / "out.txt"), "-w", str(tmp_path / "work"),
                "-j", "1",
            ])

        # --allow-remote-resources skips the guard; a small input keeps the
        # override leg from issuing thousands of network lookups.
        cli([
            str(small_input), str(annotation_file), "--grr", str(grr_file),
            "-o", str(tmp_path / "out2.txt"), "-w", str(tmp_path / "work2"),
            "-j", "1", "--allow-remote-resources",
        ])
        assert (tmp_path / "out2.txt").exists()


def test_annotate_tabular_splittable_keeps_jobs(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"  # tabixed -> splittable
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(
        in_file, in_content,
        seq_col=0, start_col=1, end_col=1, line_skip=1, force=True)

    process_graph = mocker.patch(
        "gain.task_graph.cli_tools.TaskGraphCli.process_graph")

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 5,  # splittable input must keep the user's -j untouched
        ]
    ])

    process_graph.assert_called_once()
    assert process_graph.call_args.kwargs["jobs"] == 5


@pytest.mark.parametrize("suffix", [".gz", ".bgz"])
def test_annotate_tabular_csi_indexed_input_no_duplication(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    suffix: str,
) -> None:
    """A compressed input indexed with .csi (not .tbi) must be annotated
    exactly once per record across region splitting.

    Regression for iossifovlab/gain#52: ``_CSVSource.__enter__`` and
    ``_add_tasks_tabixed`` used to detect the tabix index with a ``.tbi``-only
    check, while the splittability gate accepts ``.tbi`` or ``.csi``. A
    ``.csi``-only input was therefore split into one region per contig but
    opened whole-file, and every part emitted the entire file -> duplicated
    (and unsortable) output. Parametrized over .gz/.bgz to also guard the
    .bgz reading path.
    """
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr2    33
    """)
    root_path = annotate_directory_fixture
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    # setup_tabix always writes a .gz + .csi; rename to .bgz for that case
    setup_tabix(
        tmp_path / "in.txt.gz", in_content,
        seq_col=0, start_col=1, end_col=1, line_skip=1, csi=True, force=True)
    in_file = tmp_path / f"in.txt{suffix}"
    if suffix != ".gz":
        (tmp_path / "in.txt.gz").rename(in_file)
        (tmp_path / "in.txt.gz.csi").rename(tmp_path / f"in.txt{suffix}.csi")

    # only a .csi index exists, no .tbi -> exercises the gate/reader mismatch
    assert (tmp_path / f"in.txt{suffix}.csi").exists()
    assert not (tmp_path / f"in.txt{suffix}.tbi").exists()

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir, "--grr", grr_file, "-j", 1,
        ]
    ])

    rows = list(pysam.TabixFile(str(out_file)).fetch())
    assert len(rows) == 2


def test_add_tasks_tabixed_rejects_uncompressed_output_path() -> None:
    """``_add_tasks_tabixed`` must reject an ``output_path`` with no
    compression suffix.

    Regression for iossifovlab/gain#62: without the suffix the derived
    ``working_path`` equals ``output_path``, so the compress task would
    ``tabix_compress(out, out, force=True)`` (and then ``os.remove`` the
    output), truncating/deleting the file in place. Make the precondition
    explicit so a future caller can't silently trigger data loss.
    """
    with pytest.raises(AssertionError, match="compression suffix"):
        _add_tasks_tabixed(
            args={},
            task_graph=TaskGraph(),
            output_path="out.txt",
            pipeline_config=[],
            grr_definition={},
            ref_genome_id=None,
        )


@pytest.mark.parametrize("suffix", [".gz", ".bgz"])
def test_annotate_tabular_plain_compressed_input(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    suffix: str,
) -> None:
    """A plain (b)gzip input with no tabix index (non-splittable) is read
    and annotated once per record.

    Locks in .bgz reading parity with .gz on the plaintext path; .bgz input
    support landed in 091f9faf3 without test coverage.
    """
    root_path = annotate_directory_fixture
    raw = tmp_path / "in.txt"
    raw.write_text("chrom\tpos\nchr1\t23\nchr2\t33\n")
    in_file = tmp_path / f"in.txt{suffix}"
    pysam.tabix_compress(str(raw), str(in_file), force=True)
    raw.unlink()
    # no index -> not splittable -> plaintext path
    assert not (tmp_path / f"in.txt{suffix}.tbi").exists()

    out_file = tmp_path / "out.txt.gz"
    cli([
        str(a) for a in [
            in_file, root_path / "annotation.yaml",
            "-o", out_file, "-w", tmp_path / "work",
            "--grr", root_path / "grr.yaml", "-j", 1,
        ]
    ])

    with gzip.open(out_file, "rt") as out:
        lines = [ln for ln in out.read().splitlines() if ln.strip()]
    assert "score" in lines[0]  # annotation column added
    assert len(lines) == 3  # header + 2 data rows, each once


def test_annotate_tabular_tbi_indexed_bgz_input(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A .bgz input with a .tbi index (splittable) is annotated once per
    record. Companion to the .gz+.tbi coverage and #52's .bgz+.csi case.
    """
    root_path = annotate_directory_fixture
    setup_tabix(
        tmp_path / "in.txt.gz", textwrap.dedent("""
            chrom   pos
            chr1    23
            chr2    33
        """),
        seq_col=0, start_col=1, end_col=1, line_skip=1, force=True)
    in_file = tmp_path / "in.txt.bgz"
    (tmp_path / "in.txt.gz").rename(in_file)
    (tmp_path / "in.txt.gz.tbi").rename(tmp_path / "in.txt.bgz.tbi")

    out_file = tmp_path / "out.txt.gz"
    cli([
        str(a) for a in [
            in_file, root_path / "annotation.yaml",
            "-o", out_file, "-w", tmp_path / "work",
            "--grr", root_path / "grr.yaml", "-j", 1,
        ]
    ])

    assert len(list(pysam.TabixFile(str(out_file)).fetch())) == 2


def test_annotate_tabular_cli_preserves_bgz_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """`-o out.bgz` produces a .bgz output, not .gz (regression for #54)."""
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.bgz"
    setup_tabix(
        in_file, textwrap.dedent("""
            chrom   pos
            chr1    23
            chr2    33
        """),
        seq_col=0, start_col=1, end_col=1, line_skip=1, force=True)

    cli([
        str(a) for a in [
            in_file, root_path / "annotation.yaml",
            "-o", out_file, "-w", tmp_path / "work",
            "--grr", root_path / "grr.yaml", "-j", 1,
        ]
    ])

    assert out_file.exists()
    assert (tmp_path / "out.txt.bgz.tbi").exists()
    assert not (tmp_path / "out.txt.gz").exists()
    assert len(list(pysam.TabixFile(str(out_file)).fetch())) == 2


def test_annotate_tabular_mirrors_bgz_input_to_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A .bgz input with a plainly-named output mirrors the .bgz suffix."""
    root_path = annotate_directory_fixture
    raw = tmp_path / "in.txt"
    raw.write_text("chrom\tpos\nchr1\t23\nchr2\t33\n")
    in_file = tmp_path / "in.txt.bgz"
    pysam.tabix_compress(str(raw), str(in_file), force=True)
    raw.unlink()

    cli([
        str(a) for a in [
            in_file, root_path / "annotation.yaml",
            "-o", tmp_path / "out.txt", "-w", tmp_path / "work",
            "--grr", root_path / "grr.yaml", "-j", 1,
        ]
    ])

    assert (tmp_path / "out.txt.bgz").exists()
    assert not (tmp_path / "out.txt.gz").exists()
    assert not (tmp_path / "out.txt").exists()
    with gzip.open(tmp_path / "out.txt.bgz", "rt") as out:
        lines = [ln for ln in out.read().splitlines() if ln.strip()]
    assert "score" in lines[0]
    assert len(lines) == 3


def test_annotate_tabular_batch_mode(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    spy = mocker.spy(_CSVBatchSource, "__init__")

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--batch-size", 1,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()

    assert out_file_content == out_expected_content

    # assert correct batch size was actually passed to the reader
    assert len(spy.call_args.args) == 6
    assert spy.call_args.args[-1] == 1  # the last arg is the batch size


def test_annotate_tabular_produce_tabix_correctly_position(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """
    Even if the input file has unorthodox columns, if it's tabixed and
    the correct arguments are provided, a tabix file should always be produced.

    This test covers the RecordToPosition annotatable case.
    """

    in_content = textwrap.dedent("""
        #dummyCol1 chrom   dummyCol2 pos  dummyCol3
        ?          chr1    ?         23   ?
        ?          chr1    ?         24   ?
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=1, start_col=3, end_col=3)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "-j", 1,
        ]
    ])

    assert len(list(pysam.TabixFile(str(out_file)).fetch())) == 2
    assert not (tmp_path / "out.txt").exists()


def test_annotate_tabular_produce_tabix_correctly_vcf_allele(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """
    Even if the input file has unorthodox columns, if it's tabixed and
    the correct arguments are provided, a tabix file should always be produced.

    This test covers the RecordToVcfAllele annotatable case.
    """

    in_content = textwrap.dedent("""
        #dummyCol1 chrom   dummyCol2 pos      dummyCol3  ref  dummyCol4  alt
        ?          chr1    ?         23       ?          A    ?          G
        ?          chr1    ?         24       ?          A    ?          G
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=1, start_col=3, end_col=3)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "-j", 1,
        ]
    ])

    assert len(list(pysam.TabixFile(str(out_file)).fetch())) == 2
    assert not (tmp_path / "out.txt").exists()


def test_annotate_tabular_produce_tabix_correctly_region_or_cnv_annotatable(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """
    Even if the input file has unorthodox columns, if it's tabixed and
    the correct arguments are provided, a tabix file should always be produced.

    Covers the RecordToRegion and RecordToCNVAllele annotatable cases.
    """

    in_content = textwrap.dedent("""
        #dummyCol1 chrom   dummyCol2 pos_beg  dummyCol3  pos_end  dummyCol4
        ?          chr1    ?         23       ?          24       ?
        ?          chr1    ?         24       ?          24       ?
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=1, start_col=3, end_col=5)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "-j", 1,
        ]
    ])

    assert len(list(pysam.TabixFile(str(out_file)).fetch())) == 2


def test_annotate_tabular_idempotence(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    for _ in range(10):
        cli([
            str(a) for a in [
                in_file, annotation_file, "--grr", grr_file, "-o", out_file,
                "-w", work_dir,
                "-j", 1,
                "--force",
            ]
        ])
        out_file_content = out_file.read_text()
        assert out_file_content == out_expected_content


def test_annotate_tabular_multiple_chrom(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
        chr2    33
        chr2    34
        chr3    43
        chr3    44
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
        "chr2\t33\t0.3\n"
        "chr2\t34\t0.4\n"
        "chr3\t43\t0.5\n"
        "chr3\t44\t0.6\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    in_file_gz = in_file.with_suffix(".txt.gz")
    out_file = tmp_path / "out.txt.gz"
    out_file_tbi = tmp_path / "out.txt.gz.tbi"
    work_dir = tmp_path / "output"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)
    pysam.tabix_compress(str(in_file), str(in_file_gz), force=True)
    pysam.tabix_index(str(in_file_gz), force=True, line_skip=1, seq_col=0,
                      start_col=1, end_col=1)

    cli([
        str(a) for a in [
            in_file_gz, annotation_file, "-w", work_dir, "--grr", grr_file,
            "-o", out_file, "-j", 1,
            # keep the work dir so the leftover-parts assertion can inspect it
            "--keep-work-dir",
        ]
    ])

    with gzip.open(out_file, "rt") as out:
        out_file_content = out.read()
    assert out_file_content == out_expected_content
    assert os.path.exists(out_file_tbi)
    leftover = set(os.listdir(work_dir)) - {".task-log", ".task-status"}
    assert all(
        os.path.isdir(os.path.join(work_dir, d))
        and not os.listdir(os.path.join(work_dir, d))
        for d in leftover
    ), f"Unexpected non-empty entries in work_dir: {leftover}"


def test_annotate_tabular_multiple_chrom_repeated_attr(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
        chr2    33
        chr2    34
        chr3    43
        chr3    44
    """)
    out_expected_content = (
        "chrom\tpos\tscore_A0\tscore_A1\n"
        "chr1\t23\t0.1\t0.1\n"
        "chr1\t24\t0.2\t0.2\n"
        "chr2\t33\t0.3\t0.3\n"
        "chr2\t34\t0.4\t0.4\n"
        "chr3\t43\t0.5\t0.5\n"
        "chr3\t44\t0.6\t0.6\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    in_file_gz = in_file.with_suffix(".txt.gz")
    out_file = tmp_path / "out.txt.gz"
    out_file_tbi = tmp_path / "out.txt.gz.tbi"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_duplicate.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)
    pysam.tabix_compress(str(in_file), str(in_file_gz), force=True)
    pysam.tabix_index(str(in_file_gz), force=True, line_skip=1, seq_col=0,
                      start_col=1, end_col=1)

    cli([
        str(a) for a in [
            in_file_gz, annotation_file, "-w", work_dir, "--grr", grr_file,
            "-o", out_file, "-j", 1,
            # keep the work dir so the leftover-parts assertion can inspect it
            "--keep-work-dir",
            "--allow-repeated-attributes",
        ]
    ])

    with gzip.open(out_file, "rt") as out:
        out_file_content = out.read()
    assert out_file_content == out_expected_content
    assert os.path.exists(out_file_tbi)
    leftover = set(os.listdir(work_dir)) - {".task-log", ".task-status"}
    assert all(
        os.path.isdir(os.path.join(work_dir, d))
        and not os.listdir(os.path.join(work_dir, d))
        for d in leftover
    ), f"Unexpected non-empty entries in work_dir: {leftover}"


def test_annotate_tabular_none_values(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom  pos        ref        alt
        chr1   23         C          T
        chr1   24         C          A
        chr1   24         C          G
        chr1   24         C          T
        chr1   25         C          T
        chr1   26         C          G
    """)
    expected = (
        "chrom\tpos\tref\talt\tscore\n"
        "chr1\t23\tC\tT\t0.1\n"
        "chr1\t24\tC\tA\t0.3\n"
        "chr1\t24\tC\tG\t0.4\n"
        "chr1\t24\tC\tT\t\n"
        "chr1\t25\tC\tT\t\n"
        "chr1\t26\tC\tG\t\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.tsv"
    out_file = tmp_path / "out.tsv"
    work_dir = tmp_path / "output"
    annotation_file = root_path / "annotation_multiallelic.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])

    result = pathlib.Path(out_file).read_text()
    assert result == expected


def test_annotate_tabular_repeated_attributes(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore_A0\tscore_A1\n"
        "chr1\t23\t0.1\t0.101\n"
        "chr1\t24\t0.2\t0.201\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation_repeated_attributes.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--allow-repeated-attributes",
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_with_pipeline_from_grr(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    pipeline = "res_pipeline"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, pipeline, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_autodetect_columns_with_underscore(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos_beg   pos_end
        chr1    23        23
        chr1    24        24
    """)
    out_expected_content = (
        "chrom\tpos_beg\tpos_end\tscore\n"
        "chr1\t23\t23\t0.1\n"
        "chr1\t24\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_float_precision(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr4    53
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr4\t53\t0.123\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_internal_attributes(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore_1\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation_internal_attributes.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_csv_source(
    tmp_path: pathlib.Path,
) -> None:
    csv_path = tmp_path / "data.csv"
    setup_denovo(csv_path, """
        #chrom  pos
        chr1    1
        chr1    2
        chr1    3
        chr1    4
        chr1    5
        chr1    6
    """)

    with _CSVSource(str(csv_path), None, {}, "\t") as source:
        result = list(source.fetch())
        assert len(result) == 6
        for idx, item in enumerate(result):
            assert item == \
                AnnotationsWithSource(
                    {"chrom": "chr1", "pos": str(idx + 1)},
                    [Annotation(Position("chr1", idx + 1),
                                {"chrom": "chr1", "pos": str(idx + 1)})],
                )


def test_csv_batch_source(
    tmp_path: pathlib.Path,
) -> None:
    csv_path = tmp_path / "data.csv"
    setup_denovo(csv_path, """
        #chrom  pos
        chr1    1
        chr1    2
        chr2    1
        chr2    2
        chr3    1
        chr3    2
    """)

    with _CSVBatchSource(str(csv_path), None, {}, "\t", 2) as source:
        result = list(source.fetch())
        assert len(result) == 3
        for idx, batch in enumerate(result):
            chrom = f"chr{idx + 1}"
            assert batch == (
                AnnotationsWithSource(
                    {"chrom": chrom, "pos": "1"},
                    [Annotation(Position(chrom, 1),
                                {"chrom": chrom, "pos": "1"})],
                ),
                AnnotationsWithSource(
                    {"chrom": chrom, "pos": "2"},
                    [Annotation(Position(chrom, 2),
                                {"chrom": chrom, "pos": "2"})],
                ),
            )


def test_csv_source_no_header_in_file(
    tmp_path: pathlib.Path,
) -> None:
    csv_path = tmp_path / "data.csv"
    setup_denovo(csv_path, """
        chr1    23
        chr1    24
    """)

    with pytest.raises(
        ValueError,
        match="no record to annotatable could be found",
    ), _CSVSource(str(csv_path), None, {}, "\t") as source:
        list(source.fetch())


def test_csv_batch_source_no_header_in_file(
    tmp_path: pathlib.Path,
) -> None:
    csv_path = tmp_path / "data.csv"
    setup_denovo(csv_path, """
        chr1    23
        chr1    24
    """)

    with pytest.raises(
        ValueError,
        match="no record to annotatable could be found",
    ), _CSVBatchSource(str(csv_path), None, {}, "\t", 1) as source:
        list(source.fetch())


def test_csv_source_tabixed_fetch_without_region(
    tmp_path: pathlib.Path,
) -> None:
    csv_path = tmp_path / "data.csv.gz"
    setup_tabix(csv_path, """
        #chrom  pos
        chr1   23
        chr1   24
    """, seq_col=0, start_col=1, end_col=1)

    with _CSVSource(str(csv_path), None, {}, "\t") as source:
        result = list(source.fetch())
        assert len(result) == 2
        assert result[0] == AnnotationsWithSource(
            {"chrom": "chr1", "pos": "23"},
            [Annotation(Position("chr1", 23),
                        {"chrom": "chr1", "pos": "23"})],
        )
        assert result[1] == AnnotationsWithSource(
            {"chrom": "chr1", "pos": "24"},
            [Annotation(Position("chr1", 24),
                        {"chrom": "chr1", "pos": "24"})],
        )


def test_csv_source_tabixed_fetch_non_ascii_values(
    tmp_path: pathlib.Path,
) -> None:
    """A tabix-indexed input with non-ASCII (UTF-8) column values is read
    without crashing.

    Regression for iossifovlab/gain#120: ``_CSVSource.__enter__`` opened the
    tabix input without an encoding, so pysam defaulted to ASCII decoding and
    raised ``UnicodeDecodeError`` on the first non-ASCII byte -- e.g. a ClinVar
    ``clinical_disease_name`` like ``Roussy-Lévy_syndrome``. gain itself writes
    such files as UTF-8 (plain ``open``), so reannotating its own output broke.
    """
    csv_path = tmp_path / "data.csv.gz"
    setup_tabix(csv_path, """
        #chrom  pos   disease
        chr1   23    Roussy-Lévy_syndrome
        chr1   24    Charcot-Marie-Tooth_disease
    """, seq_col=0, start_col=1, end_col=1)

    with _CSVSource(str(csv_path), None, {}, "\t") as source:
        result = list(source.fetch())
        assert len(result) == 2
        assert result[0].source["disease"] == "Roussy-Lévy_syndrome"


def test_csv_writer_bad_input(tmp_path: pathlib.Path) -> None:
    out_path = str(tmp_path / "data.csv")
    header = _CSVHeader(
        ["chrom", "pos"], ["SCORE"])

    writer = _CSVWriter(str(out_path), "\t", header)
    with pytest.raises(KeyError), writer:
        # no "SCORE" column in input which we're trying to write
        writer.filter(AnnotationsWithSource(
            {"chrom": "chr1", "pos": "23"},
            [Annotation(Position("chr1", 23),
                        {"chrom": "chr1", "pos": "23"})],
        ))


def test_csv_batch_writer_bad_input(tmp_path: pathlib.Path) -> None:
    out_path = str(tmp_path / "data.csv")
    header = _CSVHeader(
        ["chrom", "pos"], ["SCORE"],
    )

    writer = _CSVBatchWriter(str(out_path), "\t", header)
    with pytest.raises(KeyError), writer:
        # no "SCORE" column in input which we're trying to write
        writer.filter([AnnotationsWithSource(
            {"chrom": "chr1", "pos": "23"},
            [Annotation(Position("chr1", 23),
                        {"chrom": "chr1", "pos": "23"})],
        )])


def test_cli_nonexistent_input_file(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    root_path = annotate_directory_fixture
    in_file = tmp_path / "blabla_does_not_exist_input.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    with pytest.raises(
        ValueError,
        match=r"blabla_does_not_exist_input.txt does not exist!",
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


def test_cli_no_pipeline_in_context(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    with pytest.raises(
        ValueError,
        match="no valid annotation pipeline configured",
    ):
        cli([
            str(in_file),
            "--grr", str(grr_file),
            "-o", str(out_file),
            "-w", str(work_dir),
            "-j", "1",
        ])


def test_cli_renamed_columns(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        CHROMOSOME   POSITION
        chr1         23
        chr1         24
    """)
    out_expected_content = (
        "CHROMOSOME\tPOSITION\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--col-chrom", "CHROMOSOME",
            "--col-pos", "POSITION",
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_cli_annotatables_that_need_ref_genome(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        location  variant
        chr1:23   sub(C->T)
        chr1:24   sub(C->A)
        chr2:33   ins(AAA)
        chr2:34   del(3)
    """)
    out_expected_content = (
        "location\tvariant\tscore\n"
        "chr1:23\tsub(C->T)\t0.1\n"
        "chr1:24\tsub(C->A)\t0.2\n"
        "chr2:33\tins(AAA)\t0.3\n"
        "chr2:34\tdel(3)\t0.35\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "--col-location", "location",
            "--col-variant", "variant",
            "-w", work_dir,
            "-j", 1,
            "-R", "test_genome",
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_concatenate_empty_regions(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        #chrom   pos
        chr1      3
        chr1      4
        chr1      53
        chr1      54

    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=0, start_col=1, end_col=1)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--region-size", 5,
            "-j", 1,
        ]
    ])

    with gzip.open(str(out_file), "rt") as res:
        out_file_content = res.readlines()
        assert len(out_file_content) == 5


def test_annotate_tabular_region_boundary(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        #chrom   pos
        chr1      1
        chr1      2
        chr1      3
        chr1      4
        chr1      5
        chr1      51
        chr1      52

    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=0, start_col=1, end_col=1)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--region-size", 2,
            "-j", 1,
        ]
    ])

    with gzip.open(str(out_file), "rt") as res:
        out_file_content = res.readlines()
        assert len(out_file_content) == 8


def test_annotate_tabular_keep_parts(
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
        chrom   pos
        chr1    23
        chr1    24
        chr2    33
        chr2    34
        chr3    43
        chr3    44
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    in_file_gz = in_file.with_suffix(".txt.gz")
    out_file = tmp_path / "out.txt.gz"
    out_file_tbi = tmp_path / "out.txt.gz.tbi"
    work_dir = tmp_path / "output"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)
    pysam.tabix_compress(str(in_file), str(in_file_gz), force=True)
    pysam.tabix_index(str(in_file_gz), force=True, line_skip=1, seq_col=0,
                      start_col=1, end_col=1)

    cli([
        str(a) for a in [
            in_file_gz, annotation_file, "-w", work_dir, "--grr", grr_file,
            "-o", out_file, "-j", 1,
            # keep the work dir so the leftover-parts assertion can inspect it
            "--keep-work-dir",
            "--keep-parts",
        ]
    ])

    assert os.path.exists(out_file)
    assert os.path.exists(out_file_tbi)
    expected = {
        ".task-log",
        ".task-status",
        "in.txt.gz_annotation_chr1_1_47",
        "in.txt.gz_annotation_chr2_1_47",
        "in.txt.gz_annotation_chr3_1_47",
    }
    actual = set(os.listdir(work_dir))
    annotator_dirs = actual - expected
    assert all(
        os.path.isdir(os.path.join(work_dir, d))
        and not os.listdir(os.path.join(work_dir, d))
        for d in annotator_dirs
    ), f"Unexpected non-empty entries in work_dir: {annotator_dirs}"
    assert expected.issubset(actual)


@pytest.mark.parametrize("verbosity, expected_level", [
    ("", logging.WARNING),
    ("-v", logging.INFO),
    ("-vv", logging.DEBUG),
    ("-vvv", logging.DEBUG),
    ("-vvvv", logging.DEBUG),
])
def test_annotate_tabular_logging_level(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
    verbosity: str,
    expected_level: int,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"
    log_file = tmp_path / "log.txt"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    handler = FsspecHandler(str(log_file))
    mocker.patch(
        "gain.task_graph.logging.FsspecHandler",
        return_value=handler,
    )

    cli([
        str(a) for a in [
            in_file, annotation_file, "--grr", grr_file, "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            verbosity,
        ] if a
    ])

    assert handler.level == expected_level


def test_annotate_tabular_append_columns(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chrom   pos  score
        chr1    23   1.0
        chr1    24   2.0
    """)
    out_expected_content = (
        "chrom\tpos\tscore\tscore\n"
        "chr1\t23\t1.0\t0.1\n"
        "chr1\t24\t2.0\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

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
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_annotate_tabular_adjust_output_separator(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    sep: str,
) -> None:
    in_content = (
        f"chrom{sep}pos{sep}score\n"
        f"chr1{sep}23{sep}1.0\n"
        f"chr1{sep}24{sep}2.0\n"
    )
    out_expected_content = (
        f"chrom{sep}pos{sep}score{sep}score\n"
        f"chr1{sep}23{sep}1.0{sep}0.1\n"
        f"chr1{sep}24{sep}2.0{sep}0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_directories(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--in-sep", sep,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


@pytest.mark.parametrize(
    "isep,osep", [
        (",", ";"),
        ("\t", ","),
        (",", "\t"),
    ])
def test_annotate_tabular_output_separator(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    isep: str,
    osep: str,
) -> None:
    in_content = (
        f"chrom{isep}pos{isep}score\n"
        f"chr1{isep}23{isep}1.0\n"
        f"chr1{isep}24{isep}2.0\n"
    )
    out_expected_content = (
        f"chrom{osep}pos{osep}score{osep}score\n"
        f"chr1{osep}23{osep}1.0{osep}0.1\n"
        f"chr1{osep}24{osep}2.0{osep}0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_directories(in_file, in_content)

    cli([
        str(a) for a in [
            in_file,
            annotation_file,
            "--grr", grr_file,
            "-o", out_file,
            "-w", work_dir,
            "-j", 1,
            "--in-sep", isep,
            "--out-sep", osep,
        ]
    ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_csv_extension_defaults_to_comma(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = (
        "chrom,pos,score\n"
        "chr1,23,1.0\n"
        "chr1,24,2.0\n"
    )
    out_expected_content = (
        "chrom,pos,score,score\n"
        "chr1,23,1.0,0.1\n"
        "chr1,24,2.0,0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.csv"
    out_file = tmp_path / "out.csv"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_directories(in_file, in_content)

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
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


@pytest.mark.parametrize(
    "filename,expected", [
        ("data.csv", ","),
        ("data.CSV", ","),
        ("data.csv.gz", ","),
        ("data.csv.bgz", ","),
        ("data.Csv.GZ", ","),
        ("path/to/my.data.csv", ","),
        ("data.txt", "\t"),
        ("data.tsv", "\t"),
        ("data.tsv.gz", "\t"),
        ("data", "\t"),
        ("data.csvx", "\t"),
    ])
def test_adjust_default_input_separator_from_extension(
    filename: str,
    expected: str,
) -> None:
    args = {"input": filename, "input_separator": None}
    assert _adjust_default_input_separator(args)["input_separator"] == expected


@pytest.mark.parametrize("filename", ["data.csv", "data.txt"])
def test_adjust_default_input_separator_explicit_flag_wins(
    filename: str,
) -> None:
    args = {"input": filename, "input_separator": "\t"}
    assert _adjust_default_input_separator(args)["input_separator"] == "\t"


def test_adjust_default_input_separator_logs_only_on_inference(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="annotate_tabular"):
        _adjust_default_input_separator(
            {"input": "data.csv", "input_separator": None})
    assert any(
        "defaulting --input-separator to comma" in r.message
        for r in caplog.records
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="annotate_tabular"):
        _adjust_default_input_separator(
            {"input": "data.txt", "input_separator": None})
        _adjust_default_input_separator(
            {"input": "data.csv", "input_separator": "\t"})
    assert caplog.records == []


def test_annotate_tabular_cross_region_boundary(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        #chrom    pos   pos_end
        chr1      21    25
        chr1      22    26
        chr1      23    27
        chr1      24    28
        chr1      25    29
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=0, start_col=1, end_col=2)

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--region-size", 2,
            "-j", 1,
        ]
    ])

    with gzip.open(str(out_file), "rt") as res:
        out_file_content = res.readlines()
        assert len(out_file_content) == 6


def test_annotate_tabular_no_regions(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = textwrap.dedent("""
        #chrom    pos   pos_end
        chr1      21    25
        chr1      22    26
        chr1      23    27
        chr1      24    28
        chr1      25    29
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_tabix(in_file, in_content,
                seq_col=0, start_col=1, end_col=2)

    spy = mocker.spy(
        gain.annotation.annotate_tabular, "_annotate_csv")

    cli([
        str(a) for a in [
            in_file, annotation_file, "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--region-size", 0,
            "-j", 1,
        ]
    ])

    assert spy.call_count == 1


def test_annotate_tabular_version_report(
    capsys: pytest.CaptureFixture,
) -> None:
    capsys.readouterr()

    with pytest.raises(SystemExit):
        cli(["--version"])

    out, _err = capsys.readouterr()
    assert out.startswith("GAIn version: ")


def test_cli_annotatables_that_need_ref_genome_but_do_not_have_it(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        location  variant
        chr1:23   sub(C->T)
        chr1:24   sub(C->A)
        chr2:33   ins(AAA)
        chr2:34   del(3)
    """)
    out_expected_content = (
        "location\tvariant\tscore\n"
        "chr1:23\tsub(C->T)\t0.1\n"
        "chr1:24\tsub(C->A)\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    with pytest.raises(
        ValueError,
        match=r"errors occured during reading of CSV file .*"
        r"genome is required for ins/del variants",
    ):
        cli([
            str(a) for a in [
                in_file, annotation_file, "--grr", grr_file, "-o", out_file,
                "--col-location", "location",
                "--col-variant", "variant",
                "-w", work_dir,
                "-j", 1,
            ]
        ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_cli_annotatables_dae_that_need_ref_genome_but_do_not_have_it(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    in_content = textwrap.dedent("""
        chr   pos   variant
        chr1  23   sub(C->T)
        chr1  24   sub(C->A)
        chr2  33   ins(AAA)
        chr2  34   del(3)
    """)
    out_expected_content = (
        "chr\tpos\tvariant\tscore\n"
        "chr1\t23\tsub(C->T)\t0.1\n"
        "chr1\t24\tsub(C->A)\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    annotation_file = root_path / "annotation.yaml"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    with pytest.raises(
        ValueError,
        match=r"errors occured during reading of CSV file .*"
        r"genome is required for ins/del variants",
    ):
        cli([
            str(a) for a in [
                in_file, annotation_file, "--grr", grr_file, "-o", out_file,
                "--col-chrom", "chr",
                "--col-pos", "pos",
                "--col-variant", "variant",
                "-w", work_dir,
                "-j", 1,
            ]
        ])
    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def _build_annotate_tabular_args(**overrides: Any) -> dict[str, Any]:
    """Build args dict for annotate_tabular function with sensible defaults."""
    defaults = {
        "input_separator": "\t",
        "output_separator": "\t",
        "batch_size": 0,
        "columns_args": {
            "col_chrom": "chrom",
            "col_pos": "pos",
            "col_pos_beg": "pos_beg",
            "col_pos_end": "pos_end",
            "col_ref": "ref",
            "col_alt": "alt",
            "col_location": "location",
            "col_variant": "variant",
            "col_vcf_like": "vcf_like",
            "col_cnv_type": "cnv_type",
        },
    }
    defaults.update(overrides)
    return defaults


def test_annotate_tabular_function_basic(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with basic position data."""

    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_function_preserves_bgz_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """The library annotate_tabular() honors an explicit .bgz output."""
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt.bgz"
    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline = build_annotation_pipeline([{"position_score": "one"}], grr)
    args = _build_annotate_tabular_args()

    annotate_tabular(str(in_file), pipeline, str(out_file), args)

    assert out_file.exists()
    assert not (tmp_path / "out.txt.gz").exists()
    with gzip.open(out_file, "rt") as out:
        lines = [ln for ln in out.read().splitlines() if ln.strip()]
    assert lines[0] == "chrom\tpos\tscore"
    assert len(lines) == 3


def test_annotate_tabular_function_with_batch_mode(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    """Test annotate_tabular function with batch processing."""
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    spy = mocker.spy(_CSVBatchSource, "__init__")

    # Test annotate_tabular function with batch mode
    args = _build_annotate_tabular_args(batch_size=10)

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content

    # Verify batch mode was used
    assert len(spy.call_args.args) == 6
    assert spy.call_args.args[5] == 10  # batch_size is the 6th positional arg


def test_annotate_tabular_function_with_vcf_alleles(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with VCF alleles."""
    in_content = textwrap.dedent("""
        chrom   pos   ref   alt
        chr1    23    C     T
        chr1    24    C     A
    """)
    out_expected_content = (
        "chrom\tpos\tref\talt\tscore\n"
        "chr1\t23\tC\tT\t0.1\n"
        "chr1\t24\tC\tA\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_function_with_region(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function - region parameter is accepted."""

    # Note: Region filtering only works with tabix-indexed files in the
    # full CLI workflow. For the direct function call, we just verify
    # the parameter is accepted without error.
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function - region parameter is accepted
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        region=GenomicRegion("chr1", 1, 30),
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_function_with_attributes_to_delete(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with attributes_to_delete parameter."""
    in_content = textwrap.dedent("""
        chrom   pos   old_score
        chr1    23    999
        chr1    24    888
    """)
    out_expected_content = (
        "chrom\tpos\tscore\n"
        "chr1\t23\t0.1\n"
        "chr1\t24\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function with attributes to delete
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        attributes_to_delete=["old_score"],
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_function_with_compressed_input(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with compressed input file."""
    in_content = textwrap.dedent("""
        chrom   pos
        chr1    23
        chr1    24
    """)

    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    # Create compressed input file (gzipped without tabix index)
    setup_gzip(in_file, in_content)

    # Build pipeline
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function with compressed input
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
    )

    # Verify output was compressed
    assert (tmp_path / "out.txt.gz").exists()

    # Verify content
    with gzip.open(tmp_path / "out.txt.gz", "rt") as f:
        out_file_content = f.read()

    assert "chrom\tpos\tscore\n" in out_file_content
    assert "chr1\t23\t0.1\n" in out_file_content
    assert "chr1\t24\t0.2\n" in out_file_content


def test_annotate_tabular_function_with_reference_genome(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with reference genome for CSHL format."""
    in_content = textwrap.dedent("""
        location  variant
        chr1:23   sub(C->T)
        chr1:24   sub(C->A)
    """)
    out_expected_content = (
        "location\tvariant\tscore\n"
        "chr1:23\tsub(C->T)\t0.1\n"
        "chr1:24\tsub(C->A)\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"

    grr_file = root_path / "grr.yaml"

    setup_denovo(in_file, in_content)

    # Build pipeline and get reference genome
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    ref_genome = build_reference_genome_from_resource_id(
        "test_genome", grr,
    )
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function with reference genome
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        reference_genome=ref_genome,
    )

    out_file_content = out_file.read_text()
    assert out_file_content == out_expected_content


def test_annotate_tabular_function_with_compressed_input_output(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Test annotate_tabular function with reference genome for CSHL format."""
    in_content = textwrap.dedent("""
        location  variant
        chr1:23   sub(C->T)
        chr1:24   sub(C->A)
    """)
    out_expected_content = (
        "location\tvariant\tscore\n"
        "chr1:23\tsub(C->T)\t0.1\n"
        "chr1:24\tsub(C->A)\t0.2\n"
    )
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"

    grr_file = root_path / "grr.yaml"

    setup_gzip(in_file, in_content)

    # Build pipeline and get reference genome
    grr = build_genomic_resource_repository(file_name=str(grr_file))
    ref_genome = build_reference_genome_from_resource_id(
        "test_genome", grr,
    )
    pipeline_config = [
        {"position_score": "one"},
    ]
    pipeline = build_annotation_pipeline(
        pipeline_config, grr,
    )

    # Test annotate_tabular function with reference genome
    args = _build_annotate_tabular_args()

    annotate_tabular(
        str(in_file),
        pipeline,
        str(out_file),
        args,
        reference_genome=ref_genome,
    )

    with gzip.open(str(out_file), "rt") as result:
        out_file_content = result.read()
    assert out_file_content == out_expected_content
