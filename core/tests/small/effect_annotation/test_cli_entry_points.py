# pylint: disable=W0621,C0114,C0116
"""Run the two effect annotation entry points end to end on t4c8.

``annotate_variant_effects`` runs ``cli_columns`` and
``annotate_variant_effects_vcf`` runs ``cli_vcf``. Each test passes the
t4c8 genome and gene models files through ``--reference-genome-filename``
and ``--gene-models-filename``, then reads the output file.

On the ``+`` strand transcript ``t4``, the codon ``GAG`` (Glu) at
chr1:14-16 is the second codon of the coding sequence.
"""

import gzip
import io
import pathlib
import sys
import textwrap

import pysam
import pytest
from gain.effect_annotation import cli
from gain.genomic_resources.repository_factory import GRR_DEFINITION_FILE_ENV
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)
from gain.testing.t4c8_import import GMM_CONTENT
from gain.testing.t4c8_import import t4c8_genome as setup_t4c8_genome


@pytest.fixture(scope="module")
def t4c8_root(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """A directory GRR with the ``t4c8_genome`` and ``t4c8_genes`` resources.

    The fixture writes the gene models files and does not load them, so the
    CLI builds and loads its own gene models object.
    """
    root_path = tmp_path_factory.mktemp("t4c8_cli")
    setup_t4c8_genome(root_path)
    setup_directories(root_path / "t4c8_genes", {
        "genes.txt": convert_to_tab_separated(GMM_CONTENT),
        "genomic_resource.yaml": textwrap.dedent("""\
            type: gene_models
            filename: genes.txt
            format: refflat
        """),
    })
    return root_path


@pytest.fixture(scope="module")
def t4c8_args(t4c8_root: pathlib.Path) -> list[str]:
    """The genome and gene models options of the t4c8 files."""
    return [
        "--reference-genome-filename",
        str(t4c8_root / "t4c8_genome" / "chrAll.fa"),
        "--gene-models-filename",
        str(t4c8_root / "t4c8_genes" / "genes.txt"),
        "--gene-models-fileformat", "refflat",
    ]


def run_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    entry_point: str,
    argv: list[str],
) -> None:
    monkeypatch.setattr(sys, "argv", [entry_point, *argv])
    if entry_point == "annotate_variant_effects":
        cli.cli_columns()
    else:
        cli.cli_vcf()


VCF_CONTENT = textwrap.dedent("""\
    ##fileformat=VCFv4.2
    ##contig=<ID=chr1>
    #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
    chr1\t14\t.\tG\tA\t.\t.\t.
    chr1\t14\t.\tG\tT\t.\t.\t.
    chr1\t99\t.\tA\t.\t.\t.\t.
    chr1\t99\t.\tA\tC\t.\t.\t.
""")


def read_rows(path: pathlib.Path) -> list[list[str]]:
    return [
        line.split("\t") for line in path.read_text().splitlines()
    ]


def test_columns_annotates_ref_alt_variants(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.tsv"
    input_path.write_text(textwrap.dedent("""\
        chrom\tpos\tref\talt
        chr1\t14\tG\tA
        chr1\t99\tA\tC
    """))
    output_path = tmp_path / "out.tsv"

    run_entry_point(monkeypatch, "annotate_variant_effects", [
        *t4c8_args, str(input_path), str(output_path)])

    assert read_rows(output_path) == [
        ["chrom", "pos", "ref", "alt",
         "worst_effect", "gene_effects", "effect_details"],
        ["chr1", "14", "G", "A", "missense", "t4:missense",
         "tx1:t4:missense:2/14(Glu->Lys)"],
        ["chr1", "99", "A", "C", "intergenic", "intergenic:intergenic",
         "intergenic:intergenic:intergenic:intergenic"],
    ]


def test_columns_reads_the_resources_of_the_default_grr(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_root: pathlib.Path,
) -> None:
    grr_definition_path = tmp_path / "grr_definition.yaml"
    grr_definition_path.write_text(textwrap.dedent(f"""\
        id: t4c8_cli
        type: directory
        directory: {t4c8_root}
    """))
    monkeypatch.setenv(GRR_DEFINITION_FILE_ENV, str(grr_definition_path))
    input_path = tmp_path / "in.tsv"
    input_path.write_text("chrom\tpos\tref\talt\nchr1\t14\tG\tA\n")
    output_path = tmp_path / "out.tsv"

    run_entry_point(monkeypatch, "annotate_variant_effects", [
        "--reference-genome-resource-id", "t4c8_genome",
        "--gene-models-resource-id", "t4c8_genes",
        str(input_path), str(output_path)])

    assert read_rows(output_path)[1] == [
        "chr1", "14", "G", "A", "missense", "t4:missense",
        "tx1:t4:missense:2/14(Glu->Lys)"]


def test_columns_reads_location_and_variant_from_a_gzipped_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.tsv.gz"
    with gzip.open(input_path, "wt") as infile:
        infile.write("id,loc,var\n")
        infile.write("v1,chr1:14,sub(G->A)\n")
    output_path = tmp_path / "out.tsv"

    run_entry_point(monkeypatch, "annotate_variant_effects", [
        *t4c8_args,
        "--input-sep", ",",
        "--location-col", "loc", "--variant-col", "var",
        "--annotation-attributes", "WE:worst_effect,gene_effects",
        str(input_path), str(output_path)])

    assert read_rows(output_path) == [
        ["id", "loc", "var", "WE", "gene_effects"],
        ["v1", "chr1:14", "sub(G->A)", "missense", "t4:missense"],
    ]


def test_vcf_annotates_each_alt_allele_into_info_fields(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.vcf"
    input_path.write_text(VCF_CONTENT)
    output_path = tmp_path / "out.vcf"

    run_entry_point(monkeypatch, "annotate_variant_effects_vcf", [
        *t4c8_args, str(input_path), str(output_path)])

    with pysam.VariantFile(str(output_path)) as outfile:
        rows = [
            (rec.pos, rec.alts,
             rec.info["WE"], rec.info["GE"], rec.info["ED"])
            for rec in outfile
        ]
    # The record with no alternate allele at chr1:99 is not written.
    assert rows == [
        (14, ("A",),
         ("missense",),
         ("t4:missense",),
         ("tx1:t4:missense:2/14(Glu->Lys)",)),
        (14, ("T",),
         ("nonsense",),
         ("t4:nonsense",),
         ("tx1:t4:nonsense:2/14(Glu->End)",)),
        (99, ("C",),
         ("intergenic",),
         ("intergenic:intergenic",),
         ("intergenic:intergenic:intergenic:intergenic",)),
    ]


def test_vcf_writes_one_value_per_alt_allele_of_a_multiallelic_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.vcf"
    input_path.write_text(textwrap.dedent("""\
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        chr1\t14\t.\tG\tA,T\t.\t.\t.
    """))
    output_path = tmp_path / "out.vcf"

    run_entry_point(monkeypatch, "annotate_variant_effects_vcf", [
        *t4c8_args, str(input_path), str(output_path)])

    with pysam.VariantFile(str(output_path)) as outfile:
        rows = [
            (rec.alts, rec.info["WE"], rec.info["GE"], rec.info["ED"])
            for rec in outfile
        ]
    assert rows == [
        (("A", "T"),
         ("missense", "nonsense"),
         ("t4:missense", "t4:nonsense"),
         ("tx1:t4:missense:2/14(Glu->Lys)",
          "tx1:t4:nonsense:2/14(Glu->End)")),
    ]


def test_vcf_writes_to_stdout_without_an_output_file_name(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.vcf"
    input_path.write_text(VCF_CONTENT)

    run_entry_point(monkeypatch, "annotate_variant_effects_vcf", [
        *t4c8_args, str(input_path)])

    records = [
        line.split("\t")
        for line in capsys.readouterr().out.splitlines()
        if not line.startswith("#")
    ]
    assert [(rec[1], rec[4], rec[7]) for rec in records] == [
        ("14", "A", ";".join([
            "WE=missense", "GE=t4:missense",
            "ED=tx1:t4:missense:2/14(Glu->Lys)"])),
        ("14", "T", ";".join([
            "WE=nonsense", "GE=t4:nonsense",
            "ED=tx1:t4:nonsense:2/14(Glu->End)"])),
        ("99", "C", ";".join([
            "WE=intergenic", "GE=intergenic:intergenic",
            "ED=intergenic:intergenic:intergenic:intergenic"])),
    ]


def open_paths() -> set[pathlib.Path]:
    """Return the paths of the files that this process has open."""
    fd_dir = pathlib.Path("/proc/self/fd")
    if not fd_dir.is_dir():
        pytest.skip("needs /proc/self/fd")
    paths = set()
    for fd in fd_dir.iterdir():
        try:
            paths.add(pathlib.Path(fd.readlink()))
        except OSError:
            continue
    return paths


def read_gzip_rows(path: pathlib.Path) -> list[list[str]]:
    """Read a gzip file to its end.

    ``gzip`` raises an ``EOFError`` on a stream that was not closed.
    """
    with gzip.open(path, "rt") as infile:
        return [line.split("\t") for line in infile.read().splitlines()]


def test_columns_closes_the_input_and_the_output_files(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.tsv"
    input_path.write_text("chrom\tpos\tref\talt\nchr1\t14\tG\tA\n")
    output_path = tmp_path / "out.tsv.gz"

    run_entry_point(monkeypatch, "annotate_variant_effects", [
        *t4c8_args, str(input_path), str(output_path)])

    assert {input_path, output_path}.isdisjoint(open_paths())
    assert read_gzip_rows(output_path)[1][4] == "missense"


def test_columns_closes_the_files_when_the_annotation_raises(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    t4c8_args: list[str],
) -> None:
    input_path = tmp_path / "in.tsv"
    input_path.write_text(
        "chrom\tpos\tref\talt\nchr1\t14\tG\tA\nchr1\tabc\tG\tA\n")
    output_path = tmp_path / "out.tsv.gz"

    # The ``excinfo`` traceback keeps the frame of ``cli_columns`` alive, so
    # reference counting does not close the files in place of ``close``.
    with pytest.raises(ValueError, match="abc") as excinfo:
        run_entry_point(monkeypatch, "annotate_variant_effects", [
            *t4c8_args, str(input_path), str(output_path)])

    assert excinfo.traceback
    assert {input_path, output_path}.isdisjoint(open_paths())
    assert [row[4] for row in read_gzip_rows(output_path)] == [
        "worst_effect", "missense"]


def test_columns_does_not_close_stdin_and_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    t4c8_args: list[str],
) -> None:
    stdin = io.StringIO("chrom\tpos\tref\talt\nchr1\t14\tG\tA\n")
    monkeypatch.setattr(sys, "stdin", stdin)

    run_entry_point(monkeypatch, "annotate_variant_effects", [
        *t4c8_args, "-", "-"])

    assert not stdin.closed
    assert not sys.stdout.closed
    assert capsys.readouterr().out.splitlines()[1].split("\t")[4] == \
        "missense"
