# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest_mock
from gain.annotation import annotate_vcf as annotate_vcf_module
from gain.annotation.annotate_tabular import cli as cli_tabular
from gain.annotation.annotate_vcf import cli as cli_vcf
from gain.genomic_resources.testing import setup_tabix, setup_vcf

VCF_CONTENT = textwrap.dedent("""
    ##fileformat=VCFv4.2
    ##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
    ##contig=<ID=chr1>
    #CHROM POS ID REF ALT QUAL FILTER INFO FORMAT s1
    chr1   3   .  C   T   .    .      .    GT     0/1
    chr1   4   .  C   A   .    .      .    GT     0/1
    chr1   53  .  C   G   .    .      .    GT     0/1
    chr1   54  .  C   T   .    .      .    GT     0/1
""")

TABULAR_CONTENT = textwrap.dedent("""
    #chrom   pos
    chr1      3
    chr1      4
    chr1      53
    chr1      54

""")


def _vcf_argv(in_file, annotation_file, out_file, grr_file, *extra):
    return [str(a) for a in [
        in_file, annotation_file, "-o", out_file,
        "--grr", grr_file, "--region-size", 5, "-j", 1, *extra,
    ]]


def test_annotate_vcf_cli_closes_pipeline(
    annotate_directory_fixture: pathlib.Path,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    setup_vcf(in_file, VCF_CONTENT)

    real_get_pipeline = annotate_vcf_module.get_pipeline_from_context
    close_spies: list = []

    def _capturing_get_pipeline(context):
        pipeline = real_get_pipeline(context)
        close_spies.append(mocker.spy(pipeline, "close"))
        return pipeline

    mocker.patch.object(
        annotate_vcf_module, "get_pipeline_from_context",
        side_effect=_capturing_get_pipeline)

    cli_vcf(_vcf_argv(
        in_file, root_path / "annotation.yaml", out_file,
        root_path / "grr.yaml"))

    assert close_spies, "pipeline was never built"
    assert close_spies[0].call_count >= 1


def test_annotate_vcf_removes_default_work_dir_on_success(
    annotate_directory_fixture: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    setup_vcf(in_file, VCF_CONTENT)

    cli_vcf(_vcf_argv(
        in_file, root_path / "annotation.yaml", out_file,
        root_path / "grr.yaml"))

    assert out_file.is_file()
    assert not (tmp_path / "out_work").exists()


def test_annotate_tabular_removes_default_work_dir_on_success(
    annotate_directory_fixture: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.txt.gz"
    out_file = tmp_path / "out.txt.gz"
    setup_tabix(in_file, TABULAR_CONTENT, seq_col=0, start_col=1, end_col=1)

    cli_tabular(_vcf_argv(
        in_file, root_path / "annotation.yaml", out_file,
        root_path / "grr.yaml"))

    assert out_file.is_file()
    assert not (tmp_path / "out_work").exists()


def test_annotate_vcf_keep_work_dir_flag_preserves_dir(
    annotate_directory_fixture: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    root_path = annotate_directory_fixture
    in_file = tmp_path / "in.vcf.gz"
    out_file = tmp_path / "out.vcf.gz"
    setup_vcf(in_file, VCF_CONTENT)

    cli_vcf(_vcf_argv(
        in_file, root_path / "annotation.yaml", out_file,
        root_path / "grr.yaml", "--keep-work-dir"))

    assert out_file.is_file()
    assert (tmp_path / "out_work").is_dir()
