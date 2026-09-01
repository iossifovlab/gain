# pylint: disable=W0621,C0114,C0116,W0212,W0613,R0917

import pathlib
import textwrap

import pytest
import pytest_mock
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_config import (
    AnnotationConfigParser,
    AnnotationConfigurationError,
)
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.normalize_allele_annotator import (
    NormalizeAlleleAnnotator,
)
from gain.genomic_resources.genomic_context import SimpleGenomicContext
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import (
    build_filesystem_test_repository,
    setup_genome,
)
from gain.testing.t4c8_import import GENOME_CONTENT


@pytest.fixture
def grr(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    setup_genome(tmp_path / "t4c8_genome_implicit_A" / "chrAll.fa",
                 GENOME_CONTENT)
    setup_genome(tmp_path / "t4c8_genome_implicit_B" / "chrAll.fa",
                 GENOME_CONTENT)
    return build_filesystem_test_repository(tmp_path)


def test_normalize_allele_annotator_config() -> None:
    _, pipeline_config = AnnotationConfigParser.parse_str(
        textwrap.dedent("""
        - normalize_allele_annotator:
            genome: t4c8_genome
        """),
    )

    assert pipeline_config[0].type == "normalize_allele_annotator"

    assert pipeline_config[0].parameters["genome"] == "t4c8_genome"


@pytest.mark.parametrize("pos,ref,alt", [
    (4, "GCAT", "GTGC"),
    (5, "CATG", "TGCG"),
    (4, "GCATG", "GTGCG"),
    (5, "CAT", "TGC"),
])
def test_normalize_allele_annotator_pipeline(
        t4c8_grr: GenomicResourceRepo,
        pos: int, ref: str, alt: str) -> None:
    config = textwrap.dedent("""
        - normalize_allele_annotator:
            genome: normalize_genome_1
            attributes:
            - source: normalized_allele
              name: normalized_allele
              internal: False
        """)

    annotation_pipeline = load_pipeline_from_yaml(config, t4c8_grr)

    with annotation_pipeline.open() as pipeline:
        assert len(pipeline.annotators) == 1
        annotator = pipeline.annotators[0]

        assert annotator.get_info().type == "normalize_allele_annotator"
        assert isinstance(annotator, NormalizeAlleleAnnotator)

        assert annotator.genome.get_sequence("1", 1, 10) == "GGGGCATGGG"

        allele = VCFAllele("1", pos, ref, alt)
        result = pipeline.annotate(allele)

        norm = result["normalized_allele"]

        assert norm.pos == 5
        assert norm.ref == "CAT"
        assert norm.alt == "TGC"


@pytest.mark.parametrize("pos,ref,alt, npos, nref, nalt", [
    (2, "TTTTTTTTTTTT", "TTTTTTTTTTT", 1, "AT", "A"),
    (2, "TTTTTTTTTTTT", "TTTTTTTTTT", 1, "ATT", "A"),
    (2, "TTTTTTTTTTTT", "TTTTTTTTTTTTT", 1, "A", "AT"),
    (2, "TTTTTTTTTTTT", "TTTTTTTTTTTTTT", 1, "A", "ATT"),
])
def test_normalize_tandem_repeats(
    pos: int, ref: str, alt: str,
    npos: int, nref: str, nalt: str,
    t4c8_grr: GenomicResourceRepo,
) -> None:
    config = textwrap.dedent("""
        - normalize_allele_annotator:
            genome: tr_genome
            attributes:
            - source: normalized_allele
              name: normalized_allele
              internal: False
        """)

    grr = t4c8_grr
    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        assert pipeline is not None

        assert len(pipeline.annotators) == 1
        annotator = pipeline.annotators[0]

        assert annotator.get_info().type == "normalize_allele_annotator"
        assert isinstance(annotator, NormalizeAlleleAnnotator)

        assert annotator.genome.get_sequence(
            "1", 2, 15) == "TTTTTTTTTTTTTT"

        allele = VCFAllele("1", pos, ref, alt)
        result = pipeline.annotate(allele)

        norm = result["normalized_allele"]

        assert norm.pos == npos
        assert norm.ref == nref
        assert norm.alt == nalt


def test_normalize_allele_annotator_pipeline_schema(
    t4c8_grr: GenomicResourceRepo,
) -> None:
    config = textwrap.dedent("""
        - normalize_allele_annotator:
            genome: tr_genome
        """)

    annotation_pipeline = load_pipeline_from_yaml(config, t4c8_grr)

    attributes = annotation_pipeline.get_attributes()
    assert len(attributes) == 1
    assert attributes[0].name == "normalized_allele"
    assert attributes[0].internal


def test_normalize_allele_annotator_resources(
    t4c8_grr: GenomicResourceRepo,
) -> None:
    config = textwrap.dedent("""
        - normalize_allele_annotator:
            genome: tr_genome
            attributes:
            - source: normalized_allele
              name: normalized_allele
              internal: False
        """)

    annotation_pipeline = load_pipeline_from_yaml(config, t4c8_grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert {res.get_id() for res in annotator.resources} == {
            "tr_genome",
        }


def test_normalize_allele_annotator_implicit_genome_from_preamble(
    grr: GenomicResourceRepo,
) -> None:
    genome_id = "t4c8_genome_implicit_A"
    config = textwrap.dedent(f"""
        preamble:
          input_reference_genome: {genome_id}
        annotators:
          - normalize_allele_annotator:
              attributes:
              - source: normalized_allele
                name: normalized_allele
        """)

    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert {res.get_id() for res in annotator.resources} == {genome_id}


def test_normalize_allele_annotator_implicit_genome_from_context(
    mocker: pytest_mock.MockerFixture,
    grr: GenomicResourceRepo,
) -> None:
    genome_id = "t4c8_genome_implicit_B"
    config = textwrap.dedent("""
        - normalize_allele_annotator:
            attributes:
            - source: normalized_allele
              name: normalized_allele
        """)

    genome = build_reference_genome_from_resource_id(genome_id, grr)
    context = SimpleGenomicContext(
        context_objects={"reference_genome": genome}, source="test_context")
    mocker.patch(
        "gain.annotation.normalize_allele_annotator.get_genomic_context",
    ).return_value = context

    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert {res.get_id() for res in annotator.resources} == {genome_id}


def test_normalize_allele_annotator_with_no_genome_anywhere_says_so(
    mocker: pytest_mock.MockerFixture,
    grr: GenomicResourceRepo,
) -> None:
    """The last resort is a message about the genome, not about the GRR.

    With a genome-less preamble and an empty context there is nothing to
    resolve, and the annotator's own descriptive error is what a curator
    can act on.  Before gain#1055 the empty id was resolved instead, so
    what surfaced was the repository's ``resource <> (None) not found``
    -- which names neither the genome nor the pipeline that lacks one.
    """
    config = textwrap.dedent("""
        preamble:
          summary: a preamble that declares no reference genome
        annotators:
          - normalize_allele_annotator:
              attributes:
              - source: normalized_allele
                name: normalized_allele
        """)

    mocker.patch(
        "gain.annotation.normalize_allele_annotator.get_genomic_context",
    ).return_value = SimpleGenomicContext(
        context_objects={}, source="test_context")

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        load_pipeline_from_yaml(config, grr)

    cause = excinfo.value.__cause__
    assert isinstance(cause, ValueError)
    assert "has no reference genome" in str(cause)
    assert "<>" not in str(cause)


def test_normalize_allele_annotator_genomeless_preamble_uses_the_context(
    mocker: pytest_mock.MockerFixture,
    grr: GenomicResourceRepo,
) -> None:
    """A preamble that omits the genome must not out-rank the context.

    ``input_reference_genome`` is optional and the parser defaults it to
    ``""``, so a preamble carrying only a ``summary`` still supplies the
    last operand of the ``or`` chain.  Guarding that chain with
    ``is None`` (gain#1055) let the empty id through as if it were a
    configured one, and the annotator went off to resolve resource id
    ``""`` instead of taking the context genome the pipeline has.
    """
    genome_id = "t4c8_genome_implicit_B"
    config = textwrap.dedent("""
        preamble:
          summary: a preamble that declares no reference genome
        annotators:
          - normalize_allele_annotator:
              attributes:
              - source: normalized_allele
                name: normalized_allele
        """)

    genome = build_reference_genome_from_resource_id(genome_id, grr)
    context = SimpleGenomicContext(
        context_objects={"reference_genome": genome}, source="test_context")
    mocker.patch(
        "gain.annotation.normalize_allele_annotator.get_genomic_context",
    ).return_value = context

    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert annotator.resource_ids == {genome_id}


def test_normalize_allele_annotator_preamble_genome_wins_over_the_context(
    mocker: pytest_mock.MockerFixture,
    grr: GenomicResourceRepo,
) -> None:
    """Precedence, pinned against a context that could have won.

    The sibling ``..._implicit_genome_from_preamble`` test leaves the
    context unpatched, so it shows only that the preamble beats nothing.
    """
    preamble_genome = "t4c8_genome_implicit_A"
    context_genome = "t4c8_genome_implicit_B"
    config = textwrap.dedent(f"""
        preamble:
          input_reference_genome: {preamble_genome}
        annotators:
          - normalize_allele_annotator:
              attributes:
              - source: normalized_allele
                name: normalized_allele
        """)

    mocker.patch(
        "gain.annotation.normalize_allele_annotator.get_genomic_context",
    ).return_value = SimpleGenomicContext(
        context_objects={
            "reference_genome": build_reference_genome_from_resource_id(
                context_genome, grr),
        },
        source="test_context")

    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert annotator.resource_ids == {preamble_genome}


def test_normalize_allele_annotator_genome_parameter_wins_over_everything(
    mocker: pytest_mock.MockerFixture,
    grr: GenomicResourceRepo,
) -> None:
    """The head of the chain, with every later operand also populated.

    The preamble and the context deliberately name the *same* other
    genome, so the assertion fails whichever of the two would have won.
    """
    parameter_genome = "t4c8_genome_implicit_A"
    other_genome = "t4c8_genome_implicit_B"
    config = textwrap.dedent(f"""
        preamble:
          input_reference_genome: {other_genome}
        annotators:
          - normalize_allele_annotator:
              genome: {parameter_genome}
              attributes:
              - source: normalized_allele
                name: normalized_allele
        """)

    mocker.patch(
        "gain.annotation.normalize_allele_annotator.get_genomic_context",
    ).return_value = SimpleGenomicContext(
        context_objects={
            "reference_genome": build_reference_genome_from_resource_id(
                other_genome, grr),
        },
        source="test_context")

    annotation_pipeline = load_pipeline_from_yaml(config, grr)

    with annotation_pipeline.open() as pipeline:
        annotator = pipeline.annotators[0]
        assert annotator.resource_ids == {parameter_genome}
