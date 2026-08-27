# pylint: disable=W0621,C0114,C0116,W0212,W0613

import pathlib
import textwrap

import pytest
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
    AnnotatorInfo,
    AttributeConfig,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPreamble,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.genomic_resources.resource_query import MAX_RESOURCE_QUERY_LENGTH
from gain.genomic_resources.testing import (
    convert_to_tab_separated,
    setup_directories,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
)


@pytest.fixture
def test_grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    root_path = tmp_path
    setup_directories(
        root_path, {
            "grr.yaml": textwrap.dedent(f"""
                id: reannotation_repo
                type: dir
                directory: "{root_path}/grr1"
            """),
            "grr_group.yaml": textwrap.dedent(f"""
                id: group_repo
                type: group
                children:
                - type: dir
                  directory: "{root_path}/grr1"
                - type: dir
                  directory: "{root_path}/grr2"
            """),
            "grr2": {
                "dummy_genome": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: reference_genome
                        filename: genome.fa
                    """),
                    "genome.fa": """blabla""",
                },
                "score_one": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score
                          type: float
                          name: s1
                        meta:
                            labels:
                                foo: ALPHA
                                bar: GAMMA
                                baz: sub_one
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  s1
                        foo    1          0.1
                    """),
                },
                "score_two": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score
                          type: float
                          name: s2
                        meta:
                            labels:
                                foo: BETA
                                bar: GAMMA
                                baz: sub_two
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  s2
                        foo    1          0.2
                    """),
                },
            },
            "grr1": {
                "dummy_genome": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: reference_genome
                        filename: genome.fa
                    """),
                    "genome.fa": """blabla""",
                },
                "score_one": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score
                          type: float
                          name: s1
                        meta:
                            labels:
                                foo: ALPHA
                                bar: GAMMA
                                baz: sub_one
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  s1
                        foo    1          0.1
                    """),
                },
                "score_two": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: position_score
                        table:
                            filename: data.txt
                        scores:
                        - id: score
                          type: float
                          name: s2
                        meta:
                            labels:
                                foo: BETA
                                bar: EPSILON
                                baz: sub_two
                                test: "spaced value"
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  s2
                        foo    1          0.2
                    """),
                },
                "score_three": {
                    "genomic_resource.yaml": textwrap.dedent("""
                        type: allele_score
                        table:
                            filename: data.txt
                            reference:
                              name: ref
                            alternative:
                              name: alt
                        scores:
                            - id: s3
                              name: s3
                              type: float
                              desc: ""
                    """),
                    "data.txt": convert_to_tab_separated("""
                        chrom  pos_begin  ref  alt  s3
                        foo    1          A    G    0.2
                    """),
                },
                "scores": {
                    "scoredir_one": {
                        "subscore": {
                            "genomic_resource.yaml": textwrap.dedent("""
                                type: position_score
                                table:
                                    filename: data.txt
                                scores:
                                - id: score
                                  type: float
                                  name: s1
                                meta:
                                    labels:
                                        foo: ALPHA
                                        bar: DELTA
                            """),
                            "data.txt": convert_to_tab_separated("""
                                chrom  pos_begin  s1
                                foo    1          0.1
                            """),
                        },
                    },
                    "scoredir_two": {
                        "subscore": {
                            "genomic_resource.yaml": textwrap.dedent("""
                                type: position_score
                                table:
                                    filename: data.txt
                                scores:
                                - id: score
                                  type: float
                                  name: s2
                                meta:
                                    labels:
                                        foo: BETA
                                        bar: DELTA
                            """),
                            "data.txt": convert_to_tab_separated("""
                                chrom  pos_begin  s2
                                foo    1          0.2
                            """),
                        },
                    },
                    "scoredir_three": {
                        "subscore": {
                            "genomic_resource.yaml": textwrap.dedent("""
                                type: allele_score
                                table:
                                    filename: data.txt
                                    reference:
                                      name: ref
                                    alternative:
                                      name: alt
                                scores:
                                    - id: s3
                                      name: s3
                                      type: float
                                      desc: ""
                            """),
                            "data.txt": convert_to_tab_separated("""
                                chrom  pos_begin  ref  alt  s3
                                foo    1          A    G    0.2
                            """),
                        },
                    },
                },
            },
        },
    )
    return build_genomic_resource_repository(file_name=str(
        root_path / "grr_group.yaml",
    ))


@pytest.fixture
def labeled_grr(tmp_path: pathlib.Path) -> GenomicResourceProtocolRepo:
    """A GRR of position scores carrying phenotype/source labels.

    Includes a resource whose id contains a dash and one carrying no
    labels at all, so a label query can be shown to select neither by
    accident.
    """
    return (
        a_grr()
        .with_resource(
            "phastcons100-way",
            a_position_score().with_labels(
                phenotype="autism spectrum", source="UCSC"),
        )
        .with_resource(
            "phastcons20_way",
            a_position_score().with_labels(
                phenotype="autism", source="NCBI"),
        )
        .with_resource(
            "mpc",
            a_position_score().with_labels(
                phenotype="schizophrenia", source="UCSC"),
        )
        .with_resource(
            "unlabeled",
            a_position_score(),
        )
        .build_repo(tmp_path)
    )


@pytest.fixture
def nonstring_labeled_grr(
    tmp_path: pathlib.Path,
) -> GenomicResourceProtocolRepo:
    """A GRR whose labels are not strings.

    ``meta.labels`` is a free-form YAML mapping, so a value is whatever
    YAML made of it -- ``perturbed: False`` is a bool and ``year: 2019``
    an int.  The production ``grr_encode`` carries tens of thousands of
    such resources.
    """
    return (
        a_grr()
        .with_resource(
            "perturbed-score",
            a_position_score().with_labels(perturbed=False, year=2019),
        )
        .with_resource(
            "clean-score",
            a_position_score().with_labels(perturbed=True, year=2024),
        )
        .build_repo(tmp_path)
    )


def test_wildcard_label_in_against_a_bool_label(
    nonstring_labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[\\"Fal\\" in perturbed]"
    """, grr=nonstring_labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "perturbed-score",
    ]


def test_wildcard_label_in_against_an_int_label(
    nonstring_labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[\\"19\\" in year]"
    """, grr=nonstring_labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "perturbed-score",
    ]


def test_wildcard_label_equals_against_an_int_label(
    nonstring_labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[year=\\"2024\\"]"
    """, grr=nonstring_labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "clean-score",
    ]


def test_wildcard_label_query_against_a_mapping_label(
    tmp_path: pathlib.Path,
) -> None:
    """A label whose value is a nested mapping is matched, not crashed on."""
    grr = (
        a_grr()
        .with_resource(
            "nested",
            a_position_score().with_labels(provenance={"source": "UCSC"}),
        )
        .build_repo(tmp_path)
    )
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[\\"UCSC\\" in provenance]"
    """, grr=grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "nested",
    ]


def test_wildcard_label_in_standalone(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[\\"tism\\" in phenotype]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons100-way", "phastcons20_way",
    ]


def test_wildcard_label_in_combined_with_and(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[source=\\"UCSC\\" and \\"tism\\" in phenotype]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons100-way",
    ]


def test_wildcard_label_in_as_first_operand_of_and(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[\\"tism\\" in phenotype and source=\\"NCBI\\"]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons20_way",
    ]


def test_wildcard_label_in_matches_nothing(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    with pytest.raises(
        AnnotationConfigurationError, match="No resources match",
    ):
        AnnotationConfigParser.parse_str("""
            - position_score: "*[\\"cancer\\" in phenotype]"
        """, grr=labeled_grr)


def test_a_no_match_wildcard_error_escapes_the_annotator_type(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    """The annotator type in the message is caller text, escaped.

    It is a YAML mapping key, so a config can hand it a newline; the
    no-match message interpolates it next to the wildcard, and the
    exception is logged (iossifovlab/gain#655). The wildcard itself
    cannot carry a control character -- the query grammar's charsets
    exclude them -- but it is escaped as well rather than leaning on
    the grammar staying that way.
    """
    forged_type = "position_score\nERROR forged.module: forged record"

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        AnnotationConfigParser.query_resources(
            forged_type, "no-such-resource-*", labeled_grr,
        )

    message = str(excinfo.value)
    assert "\n" not in message
    # Escaped, not dropped -- the type text is retained, newline neutralised.
    assert "forged record" in message


def test_a_scalar_annotators_key_is_refused_rather_than_iterated() -> None:
    """``annotators: "abc"`` must not mean three annotators named a, b, c.

    A string is iterable, so a scalar here used to be walked character by
    character, one attempted annotator per character. That turns a few
    kilobytes of quoted text into tens of thousands of parse attempts --
    and it slips past any bound that counts a config's declared annotators
    by taking the length of a list.
    """
    with pytest.raises(AnnotationConfigurationError, match="annotators"):
        AnnotationConfigParser.parse_str("""
            preamble:
              summary: x
            annotators: "aaaa"
        """)


def test_an_overlong_wildcard_is_refused_as_a_configuration_error(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    """The config path must inherit the parser's length bound.

    This is the path an anonymous ``/api/pipelines/validate`` POST travels
    (iossifovlab/gain#635), so an unbounded query here is minutes of CPU
    per request. The refusal must arrive as a configuration error, like
    every other bad wildcard, rather than as a raw parse error.
    """
    # One character over the bound, rather than the kilobytes an attacker
    # would send: without the bound the latter would hang this test for
    # minutes instead of failing it. It has to lead with a `*` -- that is
    # what makes the config layer treat it as a query rather than as a
    # plain resource id, and only a query reaches the parser at all.
    wildcard = "*" + "a" * MAX_RESOURCE_QUERY_LENGTH

    with pytest.raises(AnnotationConfigurationError, match="too long"):
        AnnotationConfigParser.parse_str(f"""
            - position_score: '{wildcard}'
        """, grr=labeled_grr)


def test_wildcard_label_in_single_quotes(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*['schizo' in phenotype]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "mpc",
    ]


def test_wildcard_two_in_conditions_on_the_same_label(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score:
            "*[\\"spectrum\\" in phenotype and \\"autism\\" in phenotype]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons100-way",
    ]


def test_wildcard_equals_and_in_on_the_same_label(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score:
            "*[phenotype=\\"autism\\" and \\"tism\\" in phenotype]"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons20_way",
    ]


def test_wildcard_with_dash_in_resource_id(
    labeled_grr: GenomicResourceProtocolRepo,
) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "phastcons*-way"
    """, grr=labeled_grr)
    assert [info.parameters["resource_id"] for info in pipeline_config] == [
        "phastcons100-way",
    ]


def test_simple_annotator_simple() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator:
            resource_id: resource
    """)

    assert pipeline_config == [
        AnnotatorInfo("annotator", [], {"resource_id": "resource"},
                      annotator_id="A0"),
    ]


def test_short_annotator_config() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator: resource
    """)

    assert pipeline_config == [
        AnnotatorInfo(
            "annotator", [], {"resource_id": "resource"}, annotator_id="A0",
        ),
    ]


def test_minimal_annotator_config() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator
    """)
    assert pipeline_config == [
        AnnotatorInfo("annotator", [], {}, annotator_id="A0"),
    ]


def test_annotator_config_with_more_parameters() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator:
                resource_id: resource
                key: value
    """)

    assert pipeline_config == [
        AnnotatorInfo(
            "annotator", [], {"resource_id": "resource", "key": "value"},
            annotator_id="A0",
        ),
    ]


def test_annotator_config_with_attributes() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
            - annotator:
                attributes:
                - att1
                - name: att2
                - name: att3
                  source: some_score
                - name: att4
                  source: some_score
                  att_param: foo
                - name: att5
                  att_param: raz
                  internal: true
                - source: att6
    """)

    assert pipeline_config == \
        [AnnotatorInfo("annotator", [
            AttributeConfig("att1", "att1", internal=None, parameters={}),
            AttributeConfig("att2", "att2", internal=None, parameters={}),
            AttributeConfig("att3", "some_score",
                          internal=None, parameters={}),
            AttributeConfig("att4", "some_score",
                          internal=None, parameters={"att_param": "foo"}),
            AttributeConfig("att5", "att5",
                          internal=True, parameters={"att_param": "raz"}),
            AttributeConfig("att6", "att6", internal=None, parameters={})],
            {}, annotator_id="A0")]


def test_annotator_config_with_params_and_attributes() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator:
            resource_id: resource
            attributes:
            - att1
            - att2
    """)

    assert pipeline_config == \
        [AnnotatorInfo("annotator", [
            AttributeConfig("att1", "att1", internal=None, parameters={}),
            AttributeConfig("att2", "att2", internal=None, parameters={}),
        ], {
            "resource_id": "resource",
        }, annotator_id="A0")]


def test_effect_annotator_extra_attributes() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - effect_annotator:
            gene_models: hg38/gene_models/refSeq_20200330
            genome: hg38/genomes/GRCh38-hg38
            promoter_len: 100
            attributes:
            - source: genes
              name: list_of_genes
              format: list
              internal: yes
            - source: genes
              format: str
            - source: genes_LGD
            - genes_missense
    """)

    assert pipeline_config == [
        AnnotatorInfo("effect_annotator", [
            AttributeConfig("list_of_genes", "genes",
                          internal=True, parameters={"format": "list"}),
            AttributeConfig("genes", "genes",
                          internal=None, parameters={"format": "str"}),
            AttributeConfig("genes_LGD", "genes_LGD",
                          internal=None, parameters={}),
            AttributeConfig("genes_missense", "genes_missense",
                          internal=None, parameters={})], {
            "gene_models": "hg38/gene_models/refSeq_20200330",
            "genome": "hg38/genomes/GRCh38-hg38",
            "promoter_len": 100}, annotator_id="A0",
        ),
    ]


def test_wildcard_basic(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: score_*
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_two"},
            annotator_id="A0_score_two",
        ),
    ]


def test_wildcard_for_new_pos_score(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score_annotator: score_*
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score_annotator", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score_annotator", [], {"resource_id": "score_two"},
            annotator_id="A0_score_two",
        ),
    ]


def test_wildcard_directory(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "scores/**/subscore"
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [],
            {"resource_id": "scores/scoredir_one/subscore"},
            annotator_id="A0_scores/scoredir_one/subscore",
        ),
        AnnotatorInfo(
            "position_score", [],
            {"resource_id": "scores/scoredir_two/subscore"},
            annotator_id="A0_scores/scoredir_two/subscore",
        ),
    ]


def test_wildcard_label_single(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: score_*[foo="ALPHA"]
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
    ]


def test_wildcard_label_spaced(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: score_*[test="spaced value"]
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_two"},
            annotator_id="A0_score_two",
        ),
    ]


def test_wildcard_label_and_dir(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[foo=\\"ALPHA\\"]"
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score", [],
            {"resource_id": "scores/scoredir_one/subscore"},
            annotator_id="A0_scores/scoredir_one/subscore",
        ),
    ]


def test_wildcard_label_multiple(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[foo=\\"ALPHA\\" and bar=\\"GAMMA\\"]"
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
    ]


def test_wildcard_label_substring(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[baz=\\"sub_*\\"]"
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_two"},
            annotator_id="A0_score_two",
        ),
    ]


def test_wildcard_label_single_quotes(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score: "*[baz='sub_*']"
    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_two"},
            annotator_id="A0_score_two",
        ),
    ]


def test_parse_preamble() -> None:
    preamble, _ = AnnotationConfigParser.parse_str("""
        preamble:
          summary: asdf
          description: lorem ipsum
          input_reference_genome: acgt_genome
          metadata:
              foo: bar
              subdata:
                  a: b
        annotators:
          - sample_annotator
    """)

    assert preamble == AnnotationPreamble(
        "asdf", "lorem ipsum", "acgt_genome", None,
        {"foo": "bar", "subdata": {"a": "b"}},
    )


def test_parse_preamble_with_valid_genome(
    test_grr: GenomicResourceRepo,
) -> None:
    preamble, _ = AnnotationConfigParser.parse_str("""
        preamble:
          input_reference_genome: dummy_genome
        annotators:
          - sample_annotator
    """, grr=test_grr)
    assert preamble is not None
    assert preamble.input_reference_genome == "dummy_genome"
    assert preamble.input_reference_genome_res is not None


def test_parse_preamble_no_preamble() -> None:
    preamble, _ = AnnotationConfigParser.parse_str("""
        - annotator:
            attributes:
            - att1
    """)
    assert preamble is None


def test_wildcard_in_complete_syntax(test_grr: GenomicResourceRepo) -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - position_score:
            resource_id: score_*
            param1: val1
            param2: val2

    """, grr=test_grr)
    assert pipeline_config == [
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_one",
                                   "param1": "val1",
                                   "param2": "val2"},
            annotator_id="A0_score_one",
        ),
        AnnotatorInfo(
            "position_score", [], {"resource_id": "score_two",
                                   "param1": "val1",
                                   "param2": "val2"},
            annotator_id="A0_score_two",
        ),
    ]


def test_annotator_info_to_dict() -> None:
    annotator_info = AnnotatorInfo(
        "sample_annotator", [
            AttributeConfig("attribute_1", "att1", internal=None),
            AttributeConfig("attribute_2", "att2", internal=None),
        ], {
            "resource_id": "resource",
            "param_1": "val1",
        }, annotator_id="A0",
    )
    expected_dict = {
        "sample_annotator": {
            "resource_id": "resource",
            "param_1": "val1",
            "attributes": [
                {"name": "attribute_1", "source": "att1"},
                {"name": "attribute_2", "source": "att2"},
            ],
        },
    }
    assert annotator_info.to_dict() == expected_dict


def test_empty_pipeline_passes() -> None:
    assert AnnotationConfigParser.parse_str("# test") is not None


def test_invalid_internal_attribute_value() -> None:
    with pytest.raises(
        TypeError,
        match="The 'internal' field in attribute att1 is not a boolean!",
    ):
        AnnotationConfigParser.parse_str("""
            - annotator:
                attributes:
                - name: att1
                  internal: tru
        """)


def test_an_unparsable_config_string_is_not_echoed_into_the_error() -> None:
    """The raised message must not carry the caller's config text.

    The no-filename branch is the one an anonymous pipeline-save POST
    reaches (iossifovlab/gain#655). The exception is logged at ERROR, so
    caller text in the message -- newlines intact -- forges log records.
    The ``ErrorMark`` carries the position; the caller already holds
    their own text.
    """
    payload = (
        "- position_score: [unclosed\n"
        "ERROR 2026-08-04 12:00:00 forged.module: forged record"
    )

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        AnnotationConfigParser.parse_str(payload)

    message = str(excinfo.value)
    assert "forged record" not in message
    assert "\n" not in message
    assert excinfo.value.error_mark is not None
    assert "At line" in message


def test_an_attribute_error_escapes_the_caller_source() -> None:
    """The attribute ``source`` in error messages is caller text, escaped.

    Both the non-boolean ``internal`` message and the aggregator-conflict
    message interpolate it, and the exceptions are logged
    (iossifovlab/gain#655).
    """
    with pytest.raises(TypeError) as excinfo:
        AnnotationConfigParser.parse_str("""
            - annotator:
                attributes:
                - name: "att1\\nERROR forged.module: forged record"
                  internal: tru
        """)

    message = str(excinfo.value)
    assert "\n" not in message
    assert "forged record" in message


def test_an_aggregator_conflict_error_escapes_the_caller_source() -> None:
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        AnnotationConfigParser.parse_str("""
            - annotator:
                attributes:
                - source: "att1\\nERROR forged.module: forged record"
                  aggregator: max
                  position_aggregator: min
        """)

    message = str(excinfo.value)
    assert "\n" not in message
    assert "forged record" in message


def test_boolean_internal() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str("""
        - annotator:
            attributes:
            - name: att1
              internal: true
            - name: att2
              internal: false
    """)
    assert pipeline_config is not None
    assert len(pipeline_config) == 1
    assert pipeline_config[0].attributes[0].name == "att1"
    assert pipeline_config[0].attributes[0].internal is True
    assert pipeline_config[0].attributes[1].name == "att2"
    assert pipeline_config[0].attributes[1].internal is False
