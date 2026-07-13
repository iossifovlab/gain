# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap

import pytest
from gain.annotation.annotatable import Annotatable, Region, VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.score_annotator import AlleleScoreAnnotator
from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    convert_to_tab_separated,
    setup_directories,
    setup_vcf,
)


@pytest.fixture
def annotation_pipeline(tmp_path: pathlib.Path) -> AnnotationPipeline:
    root_path = tmp_path
    setup_directories(
        root_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: ID
                          type: str
                          desc: "variant ID"
                          name: ID
                        - id: freq
                          type: float
                          desc: ""
                          name: freq
                    default_annotation:
                    - source: freq
                      name: allele_freq
                    - source: ID
                      name: variant_id

                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative ID   freq
                    1      10         A          G           ag   0.02
                    1      10         A          C           ac   0.03
                    1      10         A          T           at   0.04
                    1      16         CA         G           cag  0.03
                    1      16         C          T           ct   0.04
                    1      16         C          A           ca   0.05
                    1      16         C          CA          cca  1.0
                    1      16         C          CG          ccg  2.0
                """),
            },
        })
    local_repo = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(root_path / "grr"),
    })
    annotation_configuration = textwrap.dedent("""
        - allele_score:
            resource_id: allele_score
    """)
    return load_pipeline_from_yaml(annotation_configuration, local_repo)


def test_allele_score_annotator_attributes(
    annotation_pipeline: AnnotationPipeline,
) -> None:

    pipeline = annotation_pipeline
    annotator = pipeline.annotators[0]

    assert isinstance(annotator, AlleleScoreAnnotator)
    assert not annotator.is_open()

    attributes = annotator.attributes
    assert len(attributes) == 2
    assert attributes[0].name == "variant_id"
    assert attributes[0].source == "ID"
    assert attributes[0].spec is not None
    assert attributes[0].spec.value_type == "str"
    assert attributes[0].spec.description == "variant ID"
    assert attributes[1].name == "allele_freq"
    assert attributes[1].source == "freq"
    assert attributes[1].spec is not None
    assert attributes[1].spec.value_type == "float"
    assert attributes[1].spec.description == ""


@pytest.mark.parametrize("variant, expected", [
    (("1", 10, "A", "G"), 0.02),
    (("1", 10, "A", "C"), 0.03),
    (("1", 10, "A", "T"), 0.04),
    (("1", 16, "C", "T"), 0.04),
    (("1", 16, "C", "A"), 0.05),
    (("1", 16, "CA", "G"), 0.03),
    (("1", 16, "C", "CG"), 2.0),
    (("1", 16, "C", "CA"), 1.0),
])
def test_allele_score_with_default_score_annotation(
    variant: tuple, expected: float,
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: ID
                          type: str
                          desc: "variant ID"
                          name: ID
                        - id: freq
                          type: float
                          desc: ""
                          name: freq
                    default_annotation:
                    - source: freq
                      name: allele_freq
                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative ID  freq
                    1      10         A          G           ag  0.02
                    1      10         A          C           ac  0.03
                    1      10         A          T           at  0.04
                    1      16         CA         G           .   0.03
                    1      16         C          T           ct  0.04
                    1      16         C          A           ca  0.05
                    1      16         C          CA          ca  1.0
                    1      16         C          CG          ca  2.0
                """),
            },
        })
    local_repo = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(root_path / "grr"),
    })
    annotation_configuration = textwrap.dedent("""
        - allele_score:
            resource_id: allele_score
    """)
    pipeline = load_pipeline_from_yaml(annotation_configuration, local_repo)

    annotatable = VCFAllele(*variant)
    result = pipeline.annotate(annotatable)
    assert len(result) == 1
    assert result["allele_freq"] == expected


@pytest.mark.parametrize("allele, expected", [
    (("chr1", 1, "C", "A"), 0.001),
    (("chr1", 11, "C", "A"), 0.1),
    (("chr1", 21, "C", "A"), 0.2),
    (("chr1", 31, "C", "CA"), 0.3),
    (("chr1", 31, "C", "CG"), 1.0),
])
def test_allele_annotator_add_chrom_prefix_vcf_table(
        tmp_path: pathlib.Path, allele: tuple, expected: float) -> None:

    setup_directories(
        tmp_path, {
            "allele_score1": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.vcf.gz
                        format: vcf_info
                        chrom_mapping:
                            add_prefix: chr
                    scores:
                    - id: test100way
                      type: float
                      desc: "test values"
                      name: test100way
                    """),
            },
        })

    setup_vcf(
        tmp_path / "allele_score1" / "data.vcf.gz",
        textwrap.dedent("""
        ##fileformat=VCFv4.1
        ##INFO=<ID=test100way,Number=1,Type=Float,Description="test values">
        ##contig=<ID=1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        1      1   .  C   A   .    .      test100way=0.001;
        1      11  .  C   A   .    .      test100way=0.1;
        1      21  .  C   A   .    .      test100way=0.2;
        1      31  .  C   CA  .    .      test100way=0.3;
        1      31  .  C   G   .    .      test100way=0.4;
        1      31  .  C   CG  .    .      test100way=1.0;
        """))
    repo = build_filesystem_test_repository(tmp_path)

    pipeline_config = textwrap.dedent("""
            - allele_score:
                resource_id: allele_score1
                attributes:
                - source: test100way
            """)

    pipeline = load_pipeline_from_yaml(pipeline_config, repo)
    with pipeline.open() as work_pipeline:
        annotatable = VCFAllele(*allele)
        result = work_pipeline.annotate(annotatable)

        print(annotatable, result)
        assert result.get("test100way") == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize("annotatable, expected", [
    (VCFAllele("1", 10, "A", "G"), (0.02, "ag")),
    (VCFAllele("1", 10, "A", "C"), (0.03, "ac")),
    (VCFAllele("1", 10, "A", "T"), (0.04, "at")),
    (VCFAllele("1", 16, "C", "T"), (0.04, "ct")),
    (VCFAllele("1", 16, "C", "A"), (0.05, "ca")),
    (VCFAllele("1", 16, "CA", "G"), (0.03, "cag")),
    (VCFAllele("1", 16, "C", "CG"), (2.0, "ccg")),
    (VCFAllele("1", 16, "C", "CA"), (1.0, "cca")),
])
def test_allele_score_annotator_with_default_annotation(
    annotation_pipeline: AnnotationPipeline,
    annotatable: Annotatable, expected: tuple,
) -> None:
    pipeline = annotation_pipeline
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(annotatable)
        assert len(result) == 2
        assert result["allele_freq"] == expected[0]
        assert result["variant_id"] == expected[1]


def test_allele_score_annotator_region_with_default_annotation(
    tmp_path: pathlib.Path,
) -> None:
    root_path = tmp_path
    setup_directories(
        root_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: ID
                          type: str
                          desc: "variant ID"
                          name: ID
                        - id: freq
                          type: float
                          desc: ""
                          name: freq
                    default_annotation:
                    - source: freq
                      name: allele_freq
                    - source: ID
                      name: variant_id
                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative ID   freq
                    1      10         A          G           ag   0.02
                    1      10         A          C           ac   0.03
                    1      10         A          T           at   0.04
                    1      16         CA         G           cag  0.03
                    1      16         C          T           ct   0.04
                    1      16         C          A           ca   0.05
                    1      16         C          CA          cca  1.0
                    1      16         C          CG          ccg  2.0
                """),
            },
        })
    local_repo = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(root_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
        """),
        local_repo,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 10, 20))
    assert len(result) == 2
    assert result["allele_freq"] == 2.0
    assert set(result["variant_id"]) == {
        "ag", "ac", "at", "cag", "ct", "ca", "cca", "ccg",
    }


_ALLELE_SCORE_GRR_CONF = """
    type: allele_score
    allele_score_mode: alleles
    table:
        filename: data.txt
        reference:
          name: reference
        alternative:
          name: alternative
    scores:
        - id: ID
          type: str
          desc: "variant ID"
          name: ID
        - id: freq
          type: float
          desc: ""
          name: freq
"""

_ALLELE_SCORE_DATA = convert_to_tab_separated("""
    chrom  pos_begin  reference  alternative ID   freq
    1      10         A          G           ag   0.02
    1      10         A          C           ac   0.03
    1      10         A          T           at   0.04
    1      16         CA         G           cag  0.03
    1      16         C          T           ct   0.04
    1      16         C          A           ca   0.05
    1      16         C          CA          cca  1.0
    1      16         C          CG          ccg  2.0
""")


@pytest.fixture
def allele_score_repository(
    tmp_path: pathlib.Path,
) -> GenomicResourceRepo:
    setup_directories(
        tmp_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: _ALLELE_SCORE_GRR_CONF,
                "data.txt": _ALLELE_SCORE_DATA,
            },
        })
    return build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })


def test_allele_attribute_listed_with_default_false(
    annotation_pipeline: AnnotationPipeline,
) -> None:
    annotator = annotation_pipeline.annotators[0]
    assert isinstance(annotator, AlleleScoreAnnotator)
    attr_descs = annotator.get_attribute_specs()
    assert "allele" in attr_descs
    assert attr_descs["allele"].is_default is False
    assert attr_descs["allele"].source == "allele"


def test_allele_score_exact_match_allele_attribute(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                attributes:
                - source: freq
                  name: allele_freq
                - source: allele
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(VCFAllele("1", 10, "A", "G"))
    assert result["allele_freq"] == pytest.approx(0.02)
    assert result["allele"] == ["1:10:A:G"]


def test_allele_score_exact_match_allele_attribute_renamed(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                attributes:
                - source: freq
                  name: allele_freq
                - source: allele
                  name: variant_key
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(VCFAllele("1", 10, "A", "G"))
    assert result["allele_freq"] == pytest.approx(0.02)
    assert result["variant_key"] == ["1:10:A:G"]


def test_allele_score_exact_match_allele_with_include_attributes(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                attributes:
                - source: freq
                  name: allele_freq
                - source: allele
                  include_attributes: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(VCFAllele("1", 10, "A", "G"))
    assert result["allele_freq"] == pytest.approx(0.02)
    assert result["allele"] == ["1:10:A:G:0.02"]


def test_allele_score_exact_match_allele_filtered(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                allele_filter: "freq > 0.03"
                attributes:
                - source: freq
                  name: allele_freq
                - source: allele
                  include_attributes: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(VCFAllele("1", 10, "A", "G"))
    assert result["allele_freq"] is None
    assert result["allele"] is None


@pytest.mark.parametrize("allele_filter, expected_alleles", [
    (
        "freq > 0.03",
        {"1:10:A:T", "1:16:C:T", "1:16:C:A", "1:16:C:CA", "1:16:C:CG"},
    ),
    (
        "freq < 0.04",
        {"1:10:A:G", "1:10:A:C", "1:16:CA:G"},
    ),
    (
        "freq == 0.04",
        {"1:10:A:T", "1:16:C:T"},
    ),
    (
        "freq > 0.03 and freq < 0.1",
        {"1:10:A:T", "1:16:C:T", "1:16:C:A"},
    ),
    (
        "freq < 0.03 or freq > 1.0",
        {"1:10:A:G", "1:16:C:CG"},
    ),
    # integer literal: bare 0 must parse as number, not variable
    (
        "freq > 0",
        {"1:10:A:G", "1:10:A:C", "1:10:A:T", "1:16:CA:G",
         "1:16:C:T", "1:16:C:A", "1:16:C:CA", "1:16:C:CG"},
    ),
    # negative literal
    (
        "freq > -1",
        {"1:10:A:G", "1:10:A:C", "1:10:A:T", "1:16:CA:G",
         "1:16:C:T", "1:16:C:A", "1:16:C:CA", "1:16:C:CG"},
    ),
])
def test_allele_score_region_allele_filter(
    allele_score_repository: GenomicResourceRepo,
    allele_filter: str,
    expected_alleles: set,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent(f"""
            - allele_score:
                resource_id: allele_score
                allele_filter: "{allele_filter}"
                attributes:
                - source: allele
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 10, 16))
    assert set(result["allele"]) == expected_alleles


def test_allele_score_filter_digit_prefixed_score_name(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(
        tmp_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: 1000G
                          type: float
                          desc: ""
                          name: 1000G
                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative  1000G
                    1      10         A          G            0.01
                    1      10         A          C            0.05
                    1      10         A          T            0.10
                """),
            },
        })
    repo = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                allele_filter: "1000G > 0.03"
                attributes:
                - source: allele
        """),
        repo,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 10, 10))
    assert set(result["allele"]) == {"1:10:A:C", "1:10:A:T"}


def test_allele_score_region_allele_with_include_attributes(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                allele_filter: "freq > 0.03"
                attributes:
                - source: allele
                  include_attributes: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 10, 16))
    alleles = set(result["allele"])
    assert "1:10:A:T:0.02" not in alleles
    assert "1:10:A:T:0.03" not in alleles
    assert "1:10:A:T:0.04" in alleles
    assert "1:16:C:T:0.04" in alleles
    assert "1:16:C:A:0.05" in alleles
    assert not any(a.startswith("1:10:A:G") for a in alleles)


def test_allele_score_region_with_no_lines(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                allele_filter: "freq > 0.03"
                attributes:
                - source: allele
                  include_attributes: freq
                - source: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 200, 300))
    assert result == {"freq": None, "allele": None}


def test_allele_score_region_filter_all_alleles(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                allele_filter: "freq > 2.0"
                attributes:
                - source: allele
                  include_attributes: freq
                - source: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(Region("1", 10, 16))
    assert result["allele"] == []
    assert result["freq"] is None


def test_allele_score_include_multiple_attributes(
    allele_score_repository: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                attributes:
                - source: allele
                  include_attributes:
                    - freq
                    - ID
                - source: ID
                - source: freq
        """),
        allele_score_repository,
    )
    with pipeline.open() as work_pipeline:
        result = work_pipeline.annotate(VCFAllele("1", 10, "A", "G"))
    assert result["allele"] == ["1:10:A:G:0.02,ag"]


def test_allele_score_region_vcf_repeated_annotation_idempotent(
    tmp_path: pathlib.Path,
) -> None:
    """Annotating the same position twice must return identical results.

    Regression test for a bug where the VCF backend stored pos_end =
    variant.pos (the 1-based VCF POS) for all variants, instead of the actual
    end position (variant.stop).  A multi-base variant whose span overlaps a
    query position was included by the tabix path on the first call but
    silently dropped by the LineBuffer on the second call, because
    buffer.fetch skips records whose pos_end < query pos_begin.
    """
    setup_directories(
        tmp_path, {
            "allele_score": {
                "genomic_resource.yaml": textwrap.dedent("""
                    type: allele_score
                    table:
                        filename: data.vcf.gz
                        format: vcf_info
                    scores:
                    - id: label
                      type: str
                      desc: "variant label"
                      name: label
                """),
            },
        })
    # pos 10: dinucleotide GG→AA spans positions 10-11 (1-based)
    # pos 11: SNV G→A sits exactly at position 11
    # A region query for position 11 should include both records.
    setup_vcf(
        tmp_path / "allele_score" / "data.vcf.gz",
        textwrap.dedent("""
##fileformat=VCFv4.1
##INFO=<ID=label,Number=1,Type=String,Description="variant label">
##contig=<ID=chr1>
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   10  .  GG  AA  .    .      label=dinucleotide
chr1   11  .  G   A   .    .      label=snv
chr1   20  .  A   T   .    .      label=other
"""))
    repo = build_filesystem_test_repository(tmp_path)
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                mode: region
                attributes:
                - source: label
        """),
        repo,
    )
    annotatable = VCFAllele("chr1", 11, "G", "A")
    with pipeline.open() as work_pipeline:
        result_one = work_pipeline.annotate(annotatable)
        result_two = work_pipeline.annotate(annotatable)

    assert result_one == result_two
    assert set(result_one["label"]) == {"dinucleotide", "snv"}


def test_allele_score_value_count_aggregator_on_string_attribute(
    tmp_path: pathlib.Path,
) -> None:
    setup_directories(
        tmp_path / "grr", {
            "allele_score": {
                GR_CONF_FILE_NAME: """
                    type: allele_score
                    allele_score_mode: alleles
                    table:
                        filename: data.txt
                        reference:
                          name: reference
                        alternative:
                          name: alternative
                    scores:
                        - id: classification
                          type: str
                          desc: "variant classification"
                          name: classification
                """,
                "data.txt": convert_to_tab_separated("""
                    chrom  pos_begin  reference  alternative  classification
                    1      10         A          G            pathogenic
                    1      10         A          C            benign
                    1      10         A          T            pathogenic
                    1      20         C          T            benign
                    1      20         C          A            vus
                    1      20         C          G            benign
                """),
            },
        })
    repo = build_genomic_resource_repository({
        "id": "allele_score_local",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(
        textwrap.dedent("""
            - allele_score:
                resource_id: allele_score
                attributes:
                - source: classification
                  aggregator: value_count
        """),
        repo,
    )
    with pipeline.open() as work_pipeline:
        result_pos10 = work_pipeline.annotate(Region("1", 10, 10))
        result_pos20 = work_pipeline.annotate(Region("1", 20, 20))

    assert result_pos10["classification"] == {"pathogenic": 2, "benign": 1}
    assert result_pos20["classification"] == {"benign": 2, "vus": 1}
