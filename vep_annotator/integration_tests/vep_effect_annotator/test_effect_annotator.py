import gzip
from pathlib import Path
from typing import cast

from gain.annotation.annotate_columns import cli
from gain.annotation.annotation_factory import load_pipeline_from_file
from gain.genomic_resources.genomic_context import (
    clear_registered_contexts,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from vep_annotator.vep_annotator import VEPEffectAnnotator


def test_normal_run(tmp_path: Path) -> None:
    test_dir = Path(__file__).parent.resolve()
    expected_file = test_dir / "expected.tsv"
    expected = expected_file.read_text()
    out_path = tmp_path / "vep_effect_output"
    output_file = out_path / "out.tsv.gz"
    clear_registered_contexts()

    cli([
        str(test_dir / "variants.tsv.gz"),
        str(test_dir / "annotation.yaml"),
        "-w", str(out_path),
        "-o", str(output_file),
        "-v",
        "-j", "1",
        "--batch-size", "50",
        "--col-chrom", "CHROM",
        "--col-pos", "POS",
        "--col-ref", "REF",
        "--col-alt", "ALT",
        "--allow-repeated-attributes",
    ])

    with gzip.open(output_file) as output:
        content = output.read().decode()
    assert content == expected


def test_effect_annotator_genome_from_models(tmp_path: Path):
    test_dir = Path(__file__).parent.resolve()

    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_file(
        str(test_dir / "gene_models_annotation.yaml"), grr,
        work_dir=tmp_path)

    vep_annotator = cast(VEPEffectAnnotator, pipeline.annotators[0])
    with pipeline.open():
        assert vep_annotator.get_info().annotator_id == "A0"
        assert isinstance(vep_annotator, VEPEffectAnnotator)
        assert vep_annotator.gene_models_resource is not None
        assert vep_annotator.genome_resource is not None

        assert vep_annotator.gene_models_resource.resource_id == \
            "hg38/gene_models/MANE/1.2"
        assert vep_annotator.genome_resource.resource_id == \
            "hg38/genomes/GRCh38-hg38"


def test_effect_annotator_genome_from_models_overrides_args(
    tmp_path: Path,
):
    test_dir = Path(__file__).parent.resolve()

    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_file(
        str(test_dir / "gene_models_annotation.yaml"), grr,
        work_dir=tmp_path)

    vep_annotator = cast(VEPEffectAnnotator, pipeline.annotators[0])
    assert vep_annotator.get_info().annotator_id == "A0"
    assert isinstance(vep_annotator, VEPEffectAnnotator)

    with pipeline.open():
        assert vep_annotator.gene_models_resource is not None
        assert vep_annotator.genome_resource is not None

        assert vep_annotator.gene_models_resource.resource_id == \
            "hg38/gene_models/MANE/1.2"
        assert vep_annotator.genome_resource.resource_id == \
            "hg38/genomes/GRCh38-hg38"


def test_effect_annotator_genome_parameter_overrides_gene_models_label(
    tmp_path: Path,
):
    test_dir = Path(__file__).parent.resolve()

    grr = build_genomic_resource_repository()
    pipeline = load_pipeline_from_file(
        str(test_dir / "priority_gene_models_annotation.yaml"), grr,
        work_dir=tmp_path)

    vep_annotator = cast(VEPEffectAnnotator, pipeline.annotators[0])
    assert vep_annotator.get_info().annotator_id == "A0"
    assert isinstance(vep_annotator, VEPEffectAnnotator)

    with pipeline.open():
        assert vep_annotator.gene_models_resource is not None
        assert vep_annotator.genome_resource is not None

        assert vep_annotator.gene_models_resource.resource_id == \
            "hg38/gene_models/MANE/1.2"
        assert vep_annotator.genome_resource.resource_id == \
            "hg38/genomes/GRCh38.p13"
