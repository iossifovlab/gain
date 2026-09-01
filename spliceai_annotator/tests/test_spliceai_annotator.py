# pylint: disable=W0621,C0114,C0116,W0212,W0613,R0917
import textwrap

import pytest
import pytest_mock
from gain.annotation.annotatable import VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.genomic_resources.genomic_context_base import (
    SimpleGenomicContext,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceRepo

from spliceai_annotator.spliceai_annotator import SpliceAIAnnotator


def test_spliceai_annotator(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    """Test SpliceAI annotator."""
    pipeline = spliceai_annotation_pipeline
    assert pipeline is not None
    assert pipeline.annotators is not None
    assert len(pipeline.annotators) == 1
    assert isinstance(pipeline.annotators[0], SpliceAIAnnotator)


def test_spliceai_annotate_simple_acceptor(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    annotatable = VCFAllele("10", 94077, "A", "C")
    result = spliceai_annotation_pipeline.annotate(annotatable)
    assert result is not None
    assert result["delta_score"] == \
        "C|TUBB8|0.15|0.27|0.00|0.05|89|-23|-267|193"


def test_spliceai_annotate_del_acceptor(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    annotatable = VCFAllele("10", 94076, "CAC", "C")
    result = spliceai_annotation_pipeline.annotate(annotatable)
    assert result is not None
    assert result["delta_score"] == \
        "C|TUBB8|0.19|0.15|0.00|0.05|90|-22|289|175"


def test_spliceai_annotate_del_acceptor_2(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    annotatable = VCFAllele("10", 94076, "CA", "C")
    result = spliceai_annotation_pipeline.annotate(annotatable)
    assert result is not None
    assert result["delta_score"] == \
        "C|TUBB8|0.24|0.20|0.00|0.05|90|-22|-266|194"


def test_spliceai_annotate_del_acceptor_too_long(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotatable = VCFAllele(  # 100nt deletion
        "10", 94076,
        "CACTCGACGGCCAGGTATACGGTCATCAGTGGTCACCACCATAATGCAGAAAGAGCCAAGCGTCACAC"
        "GTGAGGTGAGAGCACCGTTCGCCCTGCAGGTGGA",
        "C")
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    result = spliceai_annotator.annotate(annotatable, {})
    assert result is not None
    assert result["delta_score"] is None


def test_spliceai_annotate_del_acceptor_long(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotatable = VCFAllele(
        "10", 94076,
        "CACTCGACGGCCAGGTATACGGTCATCAGTGGTCACCACCATAATGCAGAAAGAGCCAAGCGTCACAC",
        "C")
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    result = spliceai_annotator.annotate(annotatable, {})
    assert result is not None
    # ref_len-1 (66) > distance (50): refused as "deletion longer than
    # distance" (batch/sequential padding diverges beyond this point).
    assert result["delta_score"] is None


def test_spliceai_annotate_del_acceptor_long_batch(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotatable = VCFAllele(
        "10", 94076,
        "CACTCGACGGCCAGGTATACGGTCATCAGTGGTCACCACCATAATGCAGAAAGAGCCAAGCGTCACAC",
        "C")
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    result = spliceai_annotator.batch_annotate([annotatable], [{}])
    assert result is not None
    # ref_len-1 (66) > distance (50): refused (see the sequential test above).
    assert result[0]["delta_score"] is None


def test_spliceai_annotate_ins_acceptor(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    annotatable = VCFAllele("10", 94076, "C", "CCCCC")
    result = spliceai_annotator.annotate(annotatable, {})
    assert result is not None
    assert result["delta_score"] == \
        "CCCCC|TUBB8|0.03|0.27|0.00|0.02|1|-22|39|-21"
    # "CCCCC|TUBB8|0.15|0.27|0.00|0.05|90|-22|-266|194"


def test_spliceai_annotate_ins_acceptor_batch(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    annotatable = VCFAllele("10", 94076, "C", "CCCCC")
    result = spliceai_annotator.batch_annotate([annotatable], [{}])
    assert result is not None
    assert result[0]["delta_score"] == \
        "CCCCC|TUBB8|0.03|0.27|0.00|0.02|1|-22|39|-21"
    # "CCCCC|TUBB8|0.15|0.27|0.00|0.05|90|-22|-266|194"


def test_spliceai_annotate_ins_acceptor_long(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotatable = VCFAllele("10", 94076, "C", 60 * "CA")
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    result = spliceai_annotator.annotate(annotatable, {})
    assert result is not None
    assert result["delta_score"] == (
        "CACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACA"
        "CACACACACACACACACACACACACACACACACACACACACACACACACACA|"
        "TUBB8|0.02|0.03|0.01|0.10|-22|1|39|-21"
    )


def test_spliceai_annotate_ins_acceptor_long_batch(
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    annotatable = VCFAllele("10", 94076, "C", 60 * "CA")
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=10101,
    )
    mocker.patch.object(
        spliceai_annotator, "_distance",
        new=50,
    )

    result = spliceai_annotator.batch_annotate([annotatable], [{}])
    assert result is not None
    assert result[0]["delta_score"] == (
        "CACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACACA"
        "CACACACACACACACACACACACACACACACACACACACACACACACACACA|"
        "TUBB8|0.02|0.03|0.01|0.10|-22|1|39|-21"
    )


def test_spliceai_annotate_simple_donor(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    annotatable = VCFAllele("10", 94555, "C", "T")
    result = spliceai_annotation_pipeline.annotate(annotatable)
    assert result is not None
    assert result["delta_score"] == \
        "T|TUBB8|0.01|0.18|0.15|0.62|-2|110|-190|0"


def test_spliceai_batch_annotate(
    spliceai_annotation_pipeline: AnnotationPipeline,
) -> None:
    annotatables = [
        VCFAllele("10", 94076, "C", "CCCCC"),
        VCFAllele("10", 94076, "CAC", "C"),
        VCFAllele("10", 94077, "A", "C"),
        VCFAllele("10", 94555, "C", "T"),
    ]
    result = spliceai_annotation_pipeline.batch_annotate(annotatables)
    assert result is not None
    assert len(result) == 4

    assert result[0]["delta_score"] == \
        "CCCCC|TUBB8|0.15|0.27|0.00|0.05|90|-22|-266|194"
    assert result[1]["delta_score"] == \
        "C|TUBB8|0.19|0.15|0.00|0.05|90|-22|289|175"
    assert result[2]["delta_score"] == \
        "C|TUBB8|0.15|0.27|0.00|0.05|89|-23|-267|193"
    assert result[3]["delta_score"] == \
        "T|TUBB8|0.01|0.18|0.15|0.62|-2|110|-190|0"


@pytest.mark.parametrize(
    "chrom,pos,ref,alt, xalt_len",
    [
        ("10", 11, "G", "C", 21),
        ("10", 11, "GTA", "G", 21 - 2),
        ("10", 11, "G", "GCCC", 21 + 3),
    ],
)
def test_spliceai_padding(
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    xalt_len: int,
    spliceai_annotator: SpliceAIAnnotator,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # ACGTACGTACGTACGTACGTA
    # 0.        1........2
    # 123456789012345678901
    #                  NNNN
    mocker.patch.object(
        spliceai_annotator, "_width",
        new=21,
    )

    annotatable = VCFAllele(chrom, pos, ref, alt)
    width = spliceai_annotator._width
    assert width == 21

    seq = 5 * "ACGT" + "A"

    xref, xalt = spliceai_annotator._seq_padding(
        seq,
        (5, 17),
        annotatable,
    )

    assert len(xref) == 21
    assert len(xalt) == xalt_len


def test_spliceai_annotate_renamed_attribute(
    spliceai_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - spliceai_annotator:
            genome: hg19/genome_10
            gene_models: hg19/gene_models_small
            distance: 500
            attributes:
            - name: my_delta
              source: delta_score
    """), spliceai_grr)
    with pipeline.open() as p:
        result = p.annotate(VCFAllele("10", 94077, "A", "C"))
    assert "my_delta" in result
    assert "delta_score" not in result
    assert result["my_delta"] == \
        "C|TUBB8|0.15|0.27|0.00|0.05|89|-23|-267|193"


def test_spliceai_batch_annotate_renamed_attribute(
    spliceai_grr: GenomicResourceRepo,
) -> None:
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - spliceai_annotator:
            genome: hg19/genome_10
            gene_models: hg19/gene_models_small
            distance: 500
            attributes:
            - name: my_delta
              source: delta_score
    """), spliceai_grr)
    with pipeline.open() as p:
        results = p.batch_annotate([VCFAllele("10", 94077, "A", "C")])
    assert "my_delta" in results[0]
    assert "delta_score" not in results[0]
    assert results[0]["my_delta"] == \
        "C|TUBB8|0.15|0.27|0.00|0.05|89|-23|-267|193"


def test_spliceai_annotator_genomeless_preamble_uses_the_context(
    mocker: pytest_mock.MockerFixture,
    spliceai_grr: GenomicResourceRepo,
) -> None:
    """The third copy of the gain#1055 chain, in this plugin package.

    ``__init__`` ends its ``or`` chain on the preamble's
    ``input_reference_genome``, which the parser defaults to ``""`` when
    the key is absent.  Guarding that with ``is None`` read the empty id
    as a configured one, so a pipeline whose preamble carries only a
    ``summary`` never reached the context fallback and died resolving
    resource id ``""``.

    ``hg19/gene_models_small`` declares no ``reference_genome`` label, so
    the preamble really is the last operand standing before the context.
    The pipeline is deliberately left unopened -- constructing the
    annotator resolves the resources, which is all this observes, while
    opening it would load the models.
    """
    genome_id = "hg19/genome_10"
    gene_models = "hg19/gene_models_small"
    config = textwrap.dedent(f"""
        preamble:
          summary: a preamble that declares no reference genome
        annotators:
          - spliceai_annotator:
              gene_models: {gene_models}
              distance: 500
              attributes:
              - delta_score
    """)

    genome = build_reference_genome_from_resource_id(genome_id, spliceai_grr)
    context = SimpleGenomicContext(
        context_objects={"reference_genome": genome}, source="test_context")
    mocker.patch(
        "spliceai_annotator.spliceai_annotator.get_genomic_context",
    ).return_value = context

    pipeline = load_pipeline_from_yaml(config, spliceai_grr)

    annotator = pipeline.annotators[0]
    assert annotator.resource_ids == {genome_id, gene_models}
