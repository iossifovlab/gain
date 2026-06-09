# pylint: disable=missing-function-docstring,redefined-outer-name
# flake8: noqa
import pathlib
import textwrap

import pysam
import pytest
from gain.annotation.annotate_tabular import (
    _annotate_tabular_helper,
    cli as cli_tabular,
)
from gain.annotation.annotate_vcf import cli as cli_vcf
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import ReannotationPipeline
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_denovo,
    setup_directories,
    setup_genome,
    setup_vcf,
)
import pytest_mock
import gain.annotation.annotate_tabular
import gain.annotation.annotate_vcf
from gain.testing.foobar_import import foobar_genes, foobar_genome

pytestmark = pytest.mark.usefixtures("clean_genomic_context")


@pytest.fixture
def reannotation_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path
    foobar_genome(root_path / "grr")
    foobar_genes(root_path / "grr")
    setup_genome(
        root_path / "foobar_genome_2" / "chrAll.fa",
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

    setup_directories(
        root_path, {
            "grr.yaml": textwrap.dedent(f"""
                id: reannotation_repo
                type: dir
                directory: "{root_path}/grr"
            """),
            "grr": {
                "foobar_genome": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: genome
                        filename: chrAll.fa
                    """),
                },
                "foobar_genome_2": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: genome
                        filename: chrAll.fa
                    """),
                },
                "foobar_genes": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: gene_models
                        filename: genes.txt
                        format: refflat
                    """),
                },
                "foobar_chain": {
                    "genomic_resource.yaml": """
                        type: liftover_chain
                        filename: test.chain
                    """,
                    "test.chain": "blabla",
                },
                "reannotation_old_pipeline": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: annotation_pipeline
                        filename: pipeline.yaml
                    """),
                    "pipeline.yaml": textwrap.dedent("""
                        preamble:
                          input_reference_genome: foobar_genome
                        annotators:
                          - position_score: one
                          - effect_annotator:
                              genome: foobar_genome
                              gene_models: foobar_genes
                          - gene_score_annotator:
                              resource_id: gene_score1
                              input_gene_list: gene_list
                          - gene_score_annotator:
                              resource_id: gene_score2
                              input_gene_list: gene_list
                    """),
                },
                "one": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score
                          type: float
                          name: s1
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  s1
                        foo    4          0.1
                        foo    18         0.2
                        bar    4          1.1
                        bar    18         1.2
                    """),
                },
                "gene_score1": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: gene_score
                        filename: score.csv
                        scores:
                        - id: gene_score1
                          desc: Test gene score
                          histogram:
                            type: number
                            number_of_bins: 100
                            view_range:
                              min: 0.0
                              max: 56.0
                    """),
                    "score.csv": textwrap.dedent("""
                        gene,gene_score1
                        g1,10.1
                        g2,20.2
                    """),
                },
                "gene_score2": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: gene_score
                        filename: score.csv
                        scores:
                        - id: gene_score2
                          desc: Test gene score
                          histogram:
                            type: number
                            number_of_bins: 100
                            view_range:
                              min: 0.0
                              max: 56.0
                    """),
                    "score.csv": textwrap.dedent("""
                        gene,gene_score2
                        g1,20.2
                        g2,40.4
                    """),
                },
            },
            "reannotation_old.yaml": textwrap.dedent("""
                preamble:
                  input_reference_genome: foobar_genome
                annotators:
                  - position_score: one
                  - effect_annotator:
                      genome: foobar_genome
                      gene_models: foobar_genes
                  - gene_score_annotator:
                      resource_id: gene_score1
                      input_gene_list: gene_list
                  - gene_score_annotator:
                      resource_id: gene_score2
                      input_gene_list: gene_list
            """),
            "reannotation_old_internal.yaml": textwrap.dedent("""
                preamble:
                  input_reference_genome: foobar_genome
                annotators:
                  - position_score: one
                  - effect_annotator:
                      genome: foobar_genome
                      gene_models: foobar_genes
                  - gene_score_annotator:
                      resource_id: gene_score1
                      input_gene_list: gene_list
                  - gene_score_annotator:
                      resource_id: gene_score2
                      input_gene_list: gene_list
                      attributes:
                      - source: gene_score2
                        name: gene_score2
                        internal: true
            """),
            "reannotation_new.yaml": textwrap.dedent("""
                preamble:
                  input_reference_genome: foobar_genome
                annotators:
                  - position_score: one
                  - effect_annotator:
                      genome: foobar_genome
                      gene_models: foobar_genes
                      attributes:
                      - worst_effect
                      - gene_list
                  - gene_score_annotator:
                      resource_id: gene_score1
                      input_gene_list: gene_list
            """),
        },
    )
    return build_genomic_resource_repository(file_name=str(
        root_path / "grr.yaml",
    ))


def test_annotate_tabular_reannotation(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects\tgene_score1\tgene_score2\n"  
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\tbla\n"
    )
    out_expected_header = [
        "chrom", "pos", "score", "worst_effect", "gene_score1",
    ]
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_tabular,
                     "ReannotationPipeline")

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
        ]
    ])

    with open(out_file, "rt", encoding="utf8") as _:
        out_file_header = "".join(_.readline()).strip().split("\t")
    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert out_file_header == out_expected_header


def test_annotate_tabular_reannotation_internal(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects\tgene_score1\n"
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\n"
    )
    out_expected_header = [
        "chrom", "pos", "score", "worst_effect", "gene_score1",
    ]
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_old = tmp_path / "reannotation_old_internal.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_tabular,
                     "ReannotationPipeline")

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
        ]
    ])
    with open(out_file, "rt", encoding="utf8") as _:
        out_file_header = "".join(_.readline()).strip().split("\t")
    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert out_file_header == out_expected_header


def test_annotate_tabular_reannotation_batched(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects\tgene_score1\tgene_score2\n"
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\tbla\n"
        "chr1\t24\t0.1\tbla\tbla\tbla\tbla\tbla\n"
        "chr1\t25\t0.1\tbla\tbla\tbla\tbla\tbla\n"
        "chr1\t26\t0.1\tbla\tbla\tbla\tbla\tbla\n"
    )
    out_expected_header = [
        "chrom", "pos", "score", "worst_effect", "gene_score1",
    ]
    in_file = tmp_path / "in.txt"
    out_path = tmp_path / "out.txt"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_tabular,
                     "ReannotationPipeline")

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_path,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
            "--batch-size", 2,
        ]
    ])

    with open(out_path, "rt", encoding="utf8") as out_file:
        out_file_header = "".join(out_file.readline()).strip().split("\t")
        lines = out_file.readlines()
    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert out_file_header == out_expected_header
    assert len(lines) == 4


def test_annotate_tabular_full_reannotation_identical_pipeline(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
) -> None:
    # Regression for #108: with --full-reannotation over a previously
    # annotated input where the old and new pipelines are IDENTICAL, every
    # output column must be present AND recomputed (none dropped).
    #
    # The two pipelines are built from the same config with the same
    # work_dir, so their annotator infos compare equal -- the worst case
    # in which no annotator is "new" or "rerun". Under the bug, that left
    # ReannotationPipeline.annotators empty while every prior attribute was
    # marked deleted, so the output ended up with no annotation columns.
    in_content = (
        "chrom\tpos\tscore\tworst_effect\tworst_effect_genes\tgene_effects"
        "\teffect_details\tgene_score1\tgene_score2\n"
        "foo\t4\t9.9\tbla\tbla\tbla\tbla\t9.9\t9.9\n"
    )
    out_expected_header = [
        "chrom", "pos", "score", "worst_effect", "worst_effect_genes",
        "gene_effects", "effect_details", "gene_score1", "gene_score2",
    ]
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    config = (tmp_path / "reannotation_old.yaml").read_text()

    # Built directly (not via cli()) with a shared work_dir on purpose: the
    # real CLI injects a differing per-run work_dir into each pipeline, so
    # old/new annotator infos never compare equal and the equal-infos worst
    # case can't be reproduced through CLI arg parsing.
    pipeline_new = load_pipeline_from_yaml(
        config, reannotation_grr, work_dir=work_dir)
    pipeline_previous = load_pipeline_from_yaml(
        config, reannotation_grr, work_dir=work_dir)
    assert pipeline_new.get_info() == pipeline_previous.get_info()

    reannotation_pipeline = ReannotationPipeline(
        pipeline_new, pipeline_previous, full_reannotation=True)

    ref_genome = build_reference_genome_from_resource_id(
        "foobar_genome", reannotation_grr).open()

    args = {
        "columns_args": {},
        "input_separator": "\t",
        "output_separator": "\t",
    }
    _annotate_tabular_helper(
        input_path=str(in_file),
        pipeline=reannotation_pipeline,
        output_path=str(out_file),
        args=args,
        reference_genome=ref_genome,
        attributes_to_delete=reannotation_pipeline.deleted_attributes,
    )

    with open(out_file, "rt", encoding="utf8") as f:
        out_file_header = f.readline().strip().split("\t")
        data_line = f.readline().strip().split("\t")

    # no output column dropped
    assert out_file_header == out_expected_header
    # the score column was recomputed from the GRR (0.1), not carried over
    # from the input value (9.9)
    score_idx = out_file_header.index("score")
    assert data_line[score_idx] == "0.1"


def test_annotate_tabular_reannotation_prints_plan(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The reannotation plan must always be printed to stderr on a
    # reannotation run (visible at default verbosity).
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects"
        "\tgene_score1\tgene_score2\n"
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\tbla\n"
    )
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Reannotation plan" in captured.err
    assert "COPIED" in captured.err
    assert "ADDED" in captured.err
    assert "COMPUTED" in captured.err
    assert "DELETED" in captured.err
    # the output file is still produced and unaffected by the plan print
    assert out_file.exists()


def test_annotate_tabular_dry_run(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --dry-run prints the plan and exits without writing output.
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects"
        "\tgene_score1\tgene_score2\n"
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\tbla\n"
    )
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "--dry-run",
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Reannotation plan" in captured.err
    # no output written
    assert not out_file.exists()


def test_annotate_tabular_dry_run_plain(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --dry-run without --reannotate prints the plain all-ADDED plan and exits.
    assert reannotation_grr is not None
    in_content = (
        "chrom\tpos\n"
        "foo\t4\n"
    )
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "-n",
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Annotation plan:" in captured.err
    assert "ADDED" in captured.err
    assert not out_file.exists()


def test_annotate_vcf_reannotation(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    assert reannotation_grr is not None
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##INFO=<ID=worst_effect,Number=A,Type=String,Description="">
        ##INFO=<ID=effect_details,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_effects,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_list,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score1,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score2,Number=A,Type=String,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER \
INFO                                                  \
                                               FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      \
score=0.1;worst_effect=splice-site;effect_details=bla;gene_effects=bla;\
gene_list=g1;gene_score1=10.1;gene_score2=20.2 GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_vcf,
                     "ReannotationPipeline")

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
        ]
    ])
    out_vcf = pysam.VariantFile(str(out_file))

    info_keys = set(out_vcf.header.info.keys())

    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert info_keys == {  # pylint: disable=no-member
        "score", "worst_effect", "gene_list", "gene_score1",
    }


def test_annotate_tabular_reannotation_with_resource_id(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    in_content = (
        "chrom\tpos\tscore\tworst_effect\teffect_details\tgene_effects\tgene_score1\tgene_score2\n"
        "chr1\t23\t0.1\tbla\tbla\tbla\tbla\tbla\n"
    )
    in_file = tmp_path / "in.txt"
    out_file = tmp_path / "out.txt"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_denovo(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_tabular, "ReannotationPipeline")

    cli_tabular([
        str(a) for a in [
            in_file, annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", "reannotation_old_pipeline",
            "-j", 1,
        ]
    ])

    with open(out_file, "rt", encoding="utf8") as f:
        out_file_header = f.readline().strip().split("\t")
    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert out_file_header == [
        "chrom", "pos", "score", "worst_effect", "gene_score1",
    ]


def test_annotate_vcf_reannotation_with_resource_id(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Passing a GRR resource id (not a filesystem path) as --reannotate
    # must work for annotate_vcf too: the reannotation plan is printed to
    # stderr (header + the four bucket labels) and the VCF output is still
    # written. Parity with the annotate_tabular resource-id test above.
    assert reannotation_grr is not None
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##INFO=<ID=worst_effect,Number=A,Type=String,Description="">
        ##INFO=<ID=effect_details,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_effects,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_list,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score1,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score2,Number=A,Type=String,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER \
INFO                                                  \
                                               FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      \
score=0.1;worst_effect=splice-site;effect_details=bla;gene_effects=bla;\
gene_list=g1;gene_score1=10.1;gene_score2=20.2 GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", "reannotation_old_pipeline",
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Reannotation plan" in captured.err
    assert "COPIED" in captured.err
    assert "ADDED" in captured.err
    assert "COMPUTED" in captured.err
    assert "DELETED" in captured.err
    # the output VCF is still produced
    assert out_file.exists()


def test_annotate_vcf_reannotation_batch(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    mocker: pytest_mock.MockerFixture,
) -> None:
    assert reannotation_grr is not None

    info = ("worst_effect=splice-site;effect_details=bla;gene_effects=bla"
            ";gene_list=g1;gene_score1=10.1;gene_score2=20.2")

    in_content = textwrap.dedent(f"""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##INFO=<ID=worst_effect,Number=A,Type=String,Description="">
        ##INFO=<ID=effect_details,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_effects,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_list,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score1,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score2,Number=A,Type=String,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER INFO             FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      score=0.1;{info} GT     0/1 0/0 0/0
        foo    24  .  C   T   .    .      score=0.1;{info} GT     0/1 0/0 0/0
        foo    48  .  C   T   .    .      score=0.1;{info} GT     0/1 0/0 0/0
        foo    96  .  C   T   .    .      score=0.1;{info} GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    spy = mocker.spy(gain.annotation.annotate_vcf,
                     "ReannotationPipeline")

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
            "--batch-size", 2,
        ]
    ])
    out_vcf = pysam.VariantFile(str(out_file))

    info_keys = set(out_vcf.header.info.keys())

    # built twice: once for the printed plan, once for the actual annotation
    assert spy.call_count == 2
    assert info_keys == {  # pylint: disable=no-member
        "score", "worst_effect", "gene_list", "gene_score1",
    }


def test_annotate_vcf_reannotation_prints_plan(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The reannotation plan must always be printed to stderr on a
    # reannotation run (visible at default verbosity), and the output
    # VCF must still be produced.
    assert reannotation_grr is not None
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##INFO=<ID=worst_effect,Number=A,Type=String,Description="">
        ##INFO=<ID=effect_details,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_effects,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_list,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score1,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score2,Number=A,Type=String,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER \
INFO                                                  \
                                               FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      \
score=0.1;worst_effect=splice-site;effect_details=bla;gene_effects=bla;\
gene_list=g1;gene_score1=10.1;gene_score2=20.2 GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Reannotation plan" in captured.err
    assert "COPIED" in captured.err
    assert "ADDED" in captured.err
    assert "COMPUTED" in captured.err
    assert "DELETED" in captured.err
    # the output file is still produced and unaffected by the plan print
    assert out_file.exists()


def test_annotate_vcf_dry_run(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --dry-run prints the plan and exits without writing output.
    assert reannotation_grr is not None
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##INFO=<ID=worst_effect,Number=A,Type=String,Description="">
        ##INFO=<ID=effect_details,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_effects,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_list,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score1,Number=A,Type=String,Description="">
        ##INFO=<ID=gene_score2,Number=A,Type=String,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER \
INFO                                                  \
                                               FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      \
score=0.1;worst_effect=splice-site;effect_details=bla;gene_effects=bla;\
gene_list=g1;gene_score1=10.1;gene_score2=20.2 GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_old = tmp_path / "reannotation_old.yaml"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "--reannotate", annotation_file_old,
            "--dry-run",
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Reannotation plan" in captured.err
    # no output written
    assert not out_file.exists()


def test_annotate_vcf_dry_run_plain(
    tmp_path: pathlib.Path,
    reannotation_grr: GenomicResourceRepo,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --dry-run without --reannotate prints the plain all-ADDED plan and exits.
    assert reannotation_grr is not None
    in_content = textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##INFO=<ID=score,Number=A,Type=Float,Description="">
        ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
        ##contig=<ID=foo>
        #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT m1  d1  c1
        foo    12  .  C   T   .    .      .    GT     0/1 0/0 0/0
    """)

    in_file = tmp_path / "in.vcf"
    out_file = tmp_path / "out.vcf"
    annotation_file_new = tmp_path / "reannotation_new.yaml"
    grr_file = tmp_path / "grr.yaml"
    work_dir = tmp_path / "work"

    setup_vcf(in_file, in_content)

    cli_vcf([
        str(a) for a in [
            in_file,
            annotation_file_new,
            "-o", out_file,
            "-w", work_dir,
            "--grr", grr_file,
            "-n",
            "-j", 1,
        ]
    ])

    captured = capsys.readouterr()
    assert "Annotation plan:" in captured.err
    assert "ADDED" in captured.err
    assert not out_file.exists()
