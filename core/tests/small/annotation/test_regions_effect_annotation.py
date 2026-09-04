# pylint: disable=redefined-outer-name,C0114,C0116,protected-access,fixme

import textwrap

import pytest
from gain.annotation.annotatable import Annotatable, CNVAllele, Position, Region
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceProtocolRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
    setup_genome,
)


@pytest.fixture
def fixture_repo(
    tmp_path_factory: pytest.TempPathFactory,
) -> GenomicResourceProtocolRepo:
    root_path = tmp_path_factory.mktemp("regions_effect_annotation")
    setup_directories(root_path, {
        "gene_models": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: gene_models
                filename: gene_models.tsv
                format: "refflat"
            """),

            "gene_models.tsv": convert_to_tab_separated("""
                #geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds 
                g1        tx1  chr1  +      3       17    3        17     2         3,13       6,17
                g1        tx2  chr1  +      3       9     3        6      1         3          6
                g2        tx3  chr1  -      20      39    23       35     1         23         35
                """)  # ruff: ignore[missing-trailing-comma, line-too-long, trailing-whitespace]

        },
        "genome": {
            "genomic_resource.yaml": textwrap.dedent("""
                type: genome
                filename: genome.fa
            """),
        },
    })
    setup_genome(
        root_path / "genome" / "genome.fa",
        textwrap.dedent(f"""
            >chr1
            {25 * 'AGCT'}
            >chr2
            {25 * 'AGCT'}
            """),
    )
    return build_filesystem_test_repository(root_path)


@pytest.mark.parametrize(
    "annotatable, expected_gene_list, effect_type, length, txs", [
        (Region("chr1", 1, 19), ["g1"], "unknown", 19,
         {"g1": ["tx2", "tx1"]}),
        (Region("chr1", 1, 29), ["g1", "g2"], "unknown", 29,
         {"g1": ["tx2", "tx1"], "g2": ["tx3"]}),
        (Position("chr1", 10), ["g1"], "unknown", 1,
         {"g1": ["tx1"]}),
        (CNVAllele("chr1", 1, 29, Annotatable.Type.LARGE_DELETION),
         ["g1", "g2"], "CNV-", 29,
         {"g1": ["tx2", "tx1"], "g2": ["tx3"]}),
        (CNVAllele("chr1", 1, 29, Annotatable.Type.LARGE_DUPLICATION),
         ["g1", "g2"], "CNV+", 29,
         {"g1": ["tx2", "tx1"], "g2": ["tx3"]}),
    ],
)
def test_effect_annotator(
        annotatable: Annotatable,
        expected_gene_list: list[str],
        effect_type: str, length: int,
        txs: dict[str, list[str]],
        fixture_repo: GenomicResourceProtocolRepo,
) -> None:

    pipeline_config = textwrap.dedent("""
        - effect_annotator:
            genome: genome
            gene_models: gene_models
    """)

    pipeline = load_pipeline_from_yaml(pipeline_config, fixture_repo)

    result = None
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(annotatable)
        print(annotatable, result)

    print(annotatable, result)
    assert result is not None
    gene_list = sorted(result["gene_list"])
    assert gene_list == expected_gene_list
    assert result["worst_effect"] == effect_type
    assert result["gene_effects"] == "|".join([
        f"{g}:{effect_type}" for g in expected_gene_list])
    assert result["effect_details"] == "|".join([
        f"{t}:{g}:{effect_type}:{length}"
        for g, ts in txs.items()
        for t in ts
    ])


@pytest.mark.parametrize("configured", [
    pytest.param("5", id="number"),
    # The annotation editor's form controls hold text, so a cutoff saved
    # from there arrives quoted; it has to mean the same cutoff.
    pytest.param('"5"', id="quoted"),
])
@pytest.mark.parametrize("annotatable, effect_type, length", [
    (Region("chr1", 1, 19), "unknown", 19),
    (CNVAllele("chr1", 1, 29, Annotatable.Type.LARGE_DELETION),
     "CNV-", 29),
    (CNVAllele("chr1", 1, 29, Annotatable.Type.LARGE_DUPLICATION),
     "CNV+", 29),
])
def test_effect_annotator_region_length_cutoff(
    annotatable: Annotatable,
    effect_type: str,
    length: int,
    configured: str,
    fixture_repo: GenomicResourceProtocolRepo,
) -> None:

    pipeline_config = textwrap.dedent(f"""
        - effect_annotator:
            genome: genome
            gene_models: gene_models
            region_length_cutoff: {configured}
    """)

    pipeline = load_pipeline_from_yaml(pipeline_config, fixture_repo)

    result = None
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(annotatable)
        print(annotatable, result)

    print(annotatable, result)
    assert result is not None
    assert result["worst_effect"] == effect_type
    assert result["gene_effects"] == f"intergenic:{effect_type}"
    assert result["effect_details"] == \
        f"intergenic:intergenic:{effect_type}:{length}"
    assert result.get("gene_list") == []


@pytest.mark.parametrize("parameter, configured", [
    pytest.param("region_length_cutoff", "true", id="cutoff-is-a-bool"),
    pytest.param("region_length_cutoff", "big", id="cutoff-spells-no-number"),
    pytest.param("region_length_cutoff", "-1", id="cutoff-is-negative"),
    pytest.param("promoter_len", "1.5", id="promoter-is-fractional"),
    pytest.param("promoter_len", "-100", id="promoter-is-negative"),
])
def test_a_length_that_is_no_length_is_refused_by_name(
    parameter: str, configured: str,
    fixture_repo: GenomicResourceProtocolRepo,
) -> None:
    """Refused as the PIPELINE LOADS, naming the key the user wrote.

    Both are counts of bases the annotator compares lengths against, so
    every one of these used to reach the arithmetic instead: a quoted or
    boolean value raised ``TypeError`` on the FIRST annotated variant,
    per variant, addressed to nobody who could act on it (gain#1166).
    A fractional promoter length is refused rather than truncated --
    1.5 bases is a typo, and picking 1 for the author hides it.
    """
    pipeline_config = textwrap.dedent(f"""
        - effect_annotator:
            genome: genome
            gene_models: gene_models
            {parameter}: {configured}
    """)

    with pytest.raises(AnnotationConfigurationError, match=parameter):
        load_pipeline_from_yaml(pipeline_config, fixture_repo)


def test_promoter_len_reaches_the_effect_engine_as_an_int(
    fixture_repo: GenomicResourceProtocolRepo,
) -> None:
    """The engine counts bases with it.

    ``EffectAnnotator`` offsets positions by ``promoter_len``, so a
    ``100.0`` read out of a quoted parameter would put a float where an
    index belongs.
    """
    pipeline_config = textwrap.dedent("""
        - effect_annotator:
            genome: genome
            gene_models: gene_models
            promoter_len: "100"
    """)

    pipeline = load_pipeline_from_yaml(pipeline_config, fixture_repo)

    annotator = pipeline.annotators[0]
    promoter_len = annotator.effect_annotator.promoter_len  # type: ignore
    assert promoter_len == 100
    assert isinstance(promoter_len, int)
