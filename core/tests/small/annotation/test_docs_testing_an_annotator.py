"""The running example of the docs page "Testing an annotator".

The page ``docs/source/development/annotators/testing_an_annotator.rst``
pulls its example code from this module with ``literalinclude``. The
annotator under test is the ``FollowupAnnotator`` of the page "Writing an
annotator", copied here unchanged. The module uses the declared API only:
it imports nothing from ``gain.genomic_resources.testing``.
"""
import pathlib
import textwrap
from typing import Any

from gain import grr
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_factory import (
    load_pipeline_from_yaml,
    register_annotator_factory,
)
from gain.annotation.annotation_pipeline import (
    AnnotationPipeline,
    Annotator,
    AnnotatorInfo,
    AttributeSpec,
)
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase


# The annotator of the page "Writing an annotator" starts here.
class FollowupAnnotator(AnnotatorBase):
    """Flag a variant for follow-up from attributes already computed."""

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "followup": AttributeSpec(
                source="followup",
                value_type="str",
                description="Whether the variant is selected for follow-up",
            ),
        }

    @property
    def used_context_attributes(self) -> tuple[str, ...]:
        return ("phyloP7way", "clinical_significance")

    def _do_annotate(
        self, annotatable: Annotatable, context: dict[str, Any],
    ) -> AnnotatedValues:
        conserved = (context.get("phyloP7way") or 0) > 0
        pathogenic = context.get("clinical_significance") == "Pathogenic"
        return self._every("yes" if conserved and pathogenic else "no")


def build_followup_annotator(
    pipeline: AnnotationPipeline, info: AnnotatorInfo,
) -> Annotator:
    return FollowupAnnotator(pipeline, info)
# The annotator of the page "Writing an annotator" ends here.


# The docs example of the registration starts here.
register_annotator_factory("followup", build_followup_annotator)
# The docs example of the registration ends here.


# The docs example of the unit test starts here.
def test_followup_answers_from_the_context(tmp_path: pathlib.Path) -> None:
    """The annotator answers from the context that the test seeds."""
    repo = grr.build_genomic_resource_repository({
        "id": "empty",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(
        "- followup: {}", repo, work_dir=tmp_path / "work")

    with pipeline.open() as pipeline:
        allele = VCFAllele("chr1", 10, "A", "T")
        selected = pipeline.annotate(
            allele,
            {"phyloP7way": 1.5, "clinical_significance": "Pathogenic"})
        not_selected = pipeline.annotate(allele, {})
        no_annotatable = pipeline.annotate(None, {})

    assert selected["followup"] == "yes"
    assert not_selected == {"followup": "no"}
    assert no_annotatable == {"followup": None}
# The docs example of the unit test ends here.


# The docs example of the declared reads starts here.
def test_followup_declares_its_context_reads(tmp_path: pathlib.Path) -> None:
    """The annotator declares one attribute and two context reads."""
    repo = grr.build_genomic_resource_repository({
        "id": "empty",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(
        "- followup: {}", repo, work_dir=tmp_path / "work")

    annotator = pipeline.annotators[0]

    assert [attr.name for attr in annotator.attributes] == ["followup"]
    assert annotator.used_context_attributes == (
        "phyloP7way", "clinical_significance")
# The docs example of the declared reads ends here.


# The docs example of the resource starts here.
def write_position_score(directory: pathlib.Path) -> None:
    """Write a ``phyloP7way`` position score as a plain TSV resource."""
    directory.mkdir(parents=True)
    (directory / "genomic_resource.yaml").write_text(textwrap.dedent("""
        type: position_score
        table:
          filename: phyloP7way.tsv
          format: tsv
          chrom:
            name: chrom
          pos_begin:
            name: pos_begin
          pos_end:
            name: pos_end
        scores:
        - id: phyloP7way
          type: float
          name: phyloP7way
    """))
    (directory / "phyloP7way.tsv").write_text(
        "chrom\tpos_begin\tpos_end\tphyloP7way\n"
        "chr1\t10\t10\t1.5\n"
        "chr1\t11\t11\t-0.5\n")
# The docs example of the resource ends here.


# The docs example of the integration test starts here.
def test_followup_reads_the_upstream_score(tmp_path: pathlib.Path) -> None:
    """The annotator reads the score that the annotator before it wrote."""
    write_position_score(tmp_path / "grr" / "phyloP7way")
    repo = grr.build_genomic_resource_repository({
        "id": "local",
        "type": "directory",
        "directory": str(tmp_path / "grr"),
    })
    pipeline = load_pipeline_from_yaml(textwrap.dedent("""
        - position_score_annotator:
            resource_id: phyloP7way
        - followup: {}
    """), repo, work_dir=tmp_path / "work")

    with pipeline.open() as pipeline:
        conserved = pipeline.annotate(
            VCFAllele("chr1", 10, "A", "T"),
            {"clinical_significance": "Pathogenic"})
        not_conserved = pipeline.annotate(
            VCFAllele("chr1", 11, "C", "G"),
            {"clinical_significance": "Pathogenic"})

    assert conserved["phyloP7way"] == 1.5
    assert conserved["followup"] == "yes"
    assert not_conserved["phyloP7way"] == -0.5
    assert not_conserved["followup"] == "no"
# The docs example of the integration test ends here.


def test_the_annotator_is_the_one_of_writing_an_annotator() -> None:
    """The copy of ``FollowupAnnotator`` here equals the docs page."""
    page = (
        pathlib.Path(__file__).parents[4] / "docs" / "source"
        / "development" / "annotators" / "writing_an_annotator.rst"
    ).read_text()
    block = page.split("\n    class FollowupAnnotator(", 1)[1]
    block = block.split("\nThe module-level function", 1)[0]
    documented = textwrap.dedent(
        "    class FollowupAnnotator(" + block).strip()

    module = pathlib.Path(__file__).read_text()
    copied = module.split(
        '# The annotator of the page "Writing an annotator" starts here.\n',
    )[1].split(
        '# The annotator of the page "Writing an annotator" ends here.',
    )[0].strip()

    assert copied == documented
