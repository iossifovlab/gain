# pylint: disable=W0621,C0114,C0116
"""
Black-box tests verifying each annotator produces output keyed by the
configured attribute name (not the internal spec source name).

Each annotator is exercised twice:
- default name  : name == source (should pass everywhere)
- renamed       : name != source (catches the source-keyed return bug)
"""

import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import (
    Position,
    Region,
    VCFAllele,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    build_inmemory_test_repository,
    convert_to_tab_separated,
    setup_directories,
    setup_genome,
    setup_gzip,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def allele_score_grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "allele_res": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.mem
                    reference:
                        name: ref
                    alternative:
                        name: alt
                scores:
                - id: af
                  type: float
                  name: af
            """),
            "data.mem": convert_to_tab_separated("""
                chrom  pos_begin  ref  alt  af
                chr1   10         A    C    0.05
                chr1   20         G    T    0.10
            """),
        },
    })


@pytest.fixture(scope="module")
def liftover_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceRepo:
    root = tmp_path_factory.mktemp("liftover_grr")
    setup_genome(root / "src_genome" / "genome.fa", textwrap.dedent("""
        >chrA
        ACGTACGTACGTACGTACGT
    """))
    setup_genome(root / "tgt_genome" / "genome.fa", textwrap.dedent("""
        >chrB
        ACGTACGTACGTACGTACGT
    """))
    setup_gzip(
        root / "chain" / "liftover.chain.gz",
        convert_to_tab_separated("""
        chain 100 chrA 20 + 0 20 chrB 20 + 0 20 1
        20 0 0
        0
        """),
    )
    setup_directories(root, {
        "src_genome": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: genome
                filename: genome.fa
            """),
        },
        "tgt_genome": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: genome
                filename: genome.fa
            """),
        },
        "chain": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: liftover_chain
                filename: liftover.chain.gz
                meta:
                  labels:
                    source_genome: src_genome
                    target_genome: tgt_genome
            """),
        },
    })
    return build_filesystem_test_repository(root)


@pytest.fixture(scope="module")
def gene_set_grr(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceRepo:
    from gain.genomic_resources.repository_factory import (
        build_genomic_resource_repository,
    )
    from gain.testing.t4c8_import import t4c8_genes, t4c8_genome
    root = tmp_path_factory.mktemp("gene_set_grr")
    t4c8_genome(root / "grr")
    t4c8_genes(root / "grr")
    setup_directories(root, {
        "grr.yaml": textwrap.dedent(f"""
            id: gene_set_test
            type: dir
            directory: "{root}/grr"
        """),
        "grr": {
            "gs_collection": {
                "genomic_resource.yaml": textwrap.dedent("""
                    id: gs_collection
                    type: gene_set
                    format: directory
                    directory: sets
                """),
                "sets": {
                    "gs_alpha.txt": textwrap.dedent("""gs_alpha
                        alpha gene set
                        t4
                        c8
                    """),
                },
            },
        },
    })
    return build_genomic_resource_repository(
        file_name=str(root / "grr.yaml"),
    )


@pytest.fixture(scope="module")
def cnv_grr() -> GenomicResourceRepo:
    return build_inmemory_test_repository({
        "cnvs": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: cnv_collection
                table:
                  filename: data.mem
                scores:
                - id: frequency
                  name: frequency
                  type: float
                  desc: population frequency
            """),
            "data.mem": convert_to_tab_separated("""
                chrom  pos_begin  pos_end  frequency
                chr1   5          15       0.01
                chr1   50         100      0.02
            """),
        },
    })


# ---------------------------------------------------------------------------
# position_score_annotator
# ---------------------------------------------------------------------------

def test_position_score_default_name(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - position_score: genomic_scores/score_one
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 4))
    assert result["score_one"] == pytest.approx(0.01)


def test_position_score_renamed_attribute(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - position_score:
            resource_id: genomic_scores/score_one
            attributes:
            - source: score_one
              name: my_score
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 4))
    assert result["my_score"] == pytest.approx(0.01)
    assert "score_one" not in result


# ---------------------------------------------------------------------------
# allele_score_annotator
# ---------------------------------------------------------------------------

def test_allele_score_default_name(allele_score_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - allele_score: allele_res
    """), allele_score_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 10, "A", "C"))
    assert result["af"] == pytest.approx(0.05)


def test_allele_score_renamed_attribute(allele_score_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - allele_score:
            resource_id: allele_res
            attributes:
            - source: af
              name: my_af
    """), allele_score_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 10, "A", "C"))
    assert result["my_af"] == pytest.approx(0.05)
    assert "af" not in result


# ---------------------------------------------------------------------------
# liftover_annotator
# ---------------------------------------------------------------------------

def test_liftover_default_name(liftover_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - liftover_annotator:
            chain: chain
            attributes:
            - source: liftover_annotatable
              name: liftover_annotatable
              internal: false
    """), liftover_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chrA", 5, "A", "C"))
    assert result["liftover_annotatable"] is not None
    assert result["liftover_annotatable"].chrom == "chrB"


def test_liftover_renamed_attribute(liftover_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - liftover_annotator:
            chain: chain
            attributes:
            - source: liftover_annotatable
              name: lifted_allele
              internal: false
    """), liftover_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chrA", 5, "A", "C"))
    assert result["lifted_allele"] is not None
    assert result["lifted_allele"].chrom == "chrB"
    assert "liftover_annotatable" not in result


# ---------------------------------------------------------------------------
# normalize_allele_annotator
# ---------------------------------------------------------------------------

def test_normalize_allele_default_name(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - normalize_allele_annotator:
            genome: normalize_genome_1
            attributes:
            - source: normalized_allele
              name: normalized_allele
              internal: false
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("1", 4, "GCAT", "GTGC"))
    assert result["normalized_allele"].pos == 5


def test_normalize_allele_renamed_attribute(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - normalize_allele_annotator:
            genome: normalize_genome_1
            attributes:
            - source: normalized_allele
              name: my_norm
              internal: false
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("1", 4, "GCAT", "GTGC"))
    assert result["my_norm"].pos == 5
    assert "normalized_allele" not in result


# ---------------------------------------------------------------------------
# chrom_mapping_annotator
# ---------------------------------------------------------------------------

def test_chrom_mapping_default_name(tmp_path: pathlib.Path) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - chrom_mapping:
            add_prefix: chr
            attributes:
            - source: renamed_chromosome
              name: renamed_chromosome
              internal: false
    """), None, work_dir=tmp_path)  # type: ignore[arg-type]
    with pipeline.open() as p:
        result = p.annotate(Position("1", 5))
    assert result["renamed_chromosome"].chrom == "chr1"


def test_chrom_mapping_renamed_attribute(tmp_path: pathlib.Path) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - chrom_mapping:
            add_prefix: chr
            attributes:
            - source: renamed_chromosome
              name: my_chrom
              internal: false
    """), None, work_dir=tmp_path)  # type: ignore[arg-type]
    with pipeline.open() as p:
        result = p.annotate(Position("1", 5))
    assert result["my_chrom"].chrom == "chr1"
    assert "renamed_chromosome" not in result


# ---------------------------------------------------------------------------
# effect_annotator
# ---------------------------------------------------------------------------

def test_effect_annotator_default_name(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - effect_annotator:
            gene_models: t4c8_genes
            genome: t4c8_genome
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert result["worst_effect"] is not None
    assert result["effect_details"] is not None
    assert result["gene_effects"] is not None


def test_effect_annotator_renamed_attribute(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - effect_annotator:
            gene_models: t4c8_genes
            genome: t4c8_genome
            attributes:
            - source: worst_effect
              name: my_worst_effect
            - source: effect_details
              name: my_effect_details
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert result["my_worst_effect"] is not None
    assert result["my_effect_details"] is not None
    assert "worst_effect" not in result
    assert "effect_details" not in result


# ---------------------------------------------------------------------------
# simple_effect_annotator
# ---------------------------------------------------------------------------

def test_simple_effect_default_name(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - simple_effect_annotator:
            gene_models: t4c8_genes
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(Region("chr1", 5, 20))
    assert result["worst_effect"] is not None


def test_simple_effect_renamed_attribute(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - simple_effect_annotator:
            gene_models: t4c8_genes
            attributes:
            - source: worst_effect
              name: my_worst
    """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(Region("chr1", 5, 20))
    assert result["my_worst"] is not None
    assert "worst_effect" not in result


# ---------------------------------------------------------------------------
# gene_score_annotator
# ---------------------------------------------------------------------------

_GENE_SCORE_PREFIX = textwrap.dedent("""
    - effect_annotator:
        gene_models: t4c8_genes
        genome: t4c8_genome
        attributes:
        - source: gene_list
          name: gene_list
          internal: true
""")


def test_gene_score_default_name(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        _GENE_SCORE_PREFIX + textwrap.dedent("""
        - gene_score_annotator:
            resource_id: gene_scores/t4c8_score
            input_gene_list: gene_list
        """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert "t4" in result["t4c8_score"]
    assert result["t4c8_score"]["t4"] == pytest.approx(10.123456789)


def test_gene_score_renamed_attribute(t4c8_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        _GENE_SCORE_PREFIX + textwrap.dedent("""
        - gene_score_annotator:
            resource_id: gene_scores/t4c8_score
            input_gene_list: gene_list
            attributes:
            - source: t4c8_score
              name: my_gene_score
        """), t4c8_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert "t4" in result["my_gene_score"]
    assert result["my_gene_score"]["t4"] == pytest.approx(10.123456789)
    assert "t4c8_score" not in result


# ---------------------------------------------------------------------------
# gene_set_annotator
# ---------------------------------------------------------------------------

_GENE_SET_PREFIX = textwrap.dedent("""
    - effect_annotator:
        gene_models: t4c8_genes
        genome: t4c8_genome
        attributes:
        - source: gene_list
          name: gene_list
          internal: true
""")


def test_gene_set_default_name(gene_set_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        _GENE_SET_PREFIX + textwrap.dedent("""
        - gene_set_annotator:
            resource_id: gs_collection
            input_gene_list: gene_list
        """), gene_set_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert "gs_alpha" in result["in_sets"]


def test_gene_set_renamed_attribute(gene_set_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(
        _GENE_SET_PREFIX + textwrap.dedent("""
        - gene_set_annotator:
            resource_id: gs_collection
            input_gene_list: gene_list
            attributes:
            - source: in_sets
              name: my_in_sets
        """), gene_set_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("chr1", 7, "A", "T"))
    assert "gs_alpha" in result["my_in_sets"]
    assert "in_sets" not in result


# ---------------------------------------------------------------------------
# cnv_collection_annotator
# ---------------------------------------------------------------------------

def test_cnv_collection_default_name(cnv_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - cnv_collection: cnvs
    """), cnv_grr)
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 10))
    assert result["count"] == 1


def test_cnv_collection_renamed_attribute(cnv_grr: GenomicResourceRepo) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - cnv_collection:
            resource_id: cnvs
            attributes:
            - source: count
              name: my_count
    """), cnv_grr)
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 10))
    assert result["my_count"] == 1
    assert "count" not in result


# ---------------------------------------------------------------------------
# debug_annotator
# ---------------------------------------------------------------------------

def test_debug_annotator_default_name() -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - debug_annotator: {}
    """), None)  # type: ignore[arg-type]
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 4))
    assert result["hi"] == "hello world"


def test_debug_annotator_renamed_attribute() -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - debug_annotator:
            attributes:
            - source: hi
              name: my_greeting
    """), None)  # type: ignore[arg-type]
    with pipeline.open() as p:
        result = p.annotate(Position("chr1", 4))
    assert result["my_greeting"] == "hello world"
    assert "hi" not in result
