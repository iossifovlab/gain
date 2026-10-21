"""Pin the offline claims of the Python interface entry pages.

The tests run the code that the pages describe and assert the results
that the pages state. They read no ``.rst`` file. The pages are under
``docs/source/``:

- ``python_interface.rst``, the landing page, has no offline claim: it
  names the chapters and links to them, and the Sphinx build checks the
  links.
- ``development/resources_in_python.rst``, the "Genomic resources" chapter
  page, has no offline claim of its own: it introduces the resource pages,
  and the tests of each page pin what the page states.
- ``development/overview.rst``, "Overview and examples", runs five
  examples on public GRR resources (``hg38/genomes/GRCh38-hg38``,
  ``hg38/gene_models/MANE/1.5``, ``hg38/scores/phastCons100way``). The
  tests run the same calls on mini-GRR resources of the same kinds. The
  outputs of the page depend on the public GRR, so the tests pin the shape
  of each result instead.

The tests pin these claims of "Overview and examples":

- ``gain.grr`` exports the repository functions, the builders and the
  types they return. ``GenomicContext`` and the pandas and anndata types
  are not exported. That each name is the object of its defining module is
  pinned by ``tests/small/grr/test_grr_facade.py``.
- Example 1: a genome lists its chromosomes and their lengths.
- Example 2: a transcript's ``tx`` gives the gene interval, and the
  segments that ``fetch_region_segments_scores`` yields, clipped to the
  interval, give one value for each position.
- Example 3: ``get_all_resources``, ``get_id`` and ``get_type`` count the
  resources of each type.
- Example 4: ``load_pipeline_from_yaml`` builds an effect annotation
  pipeline, ``annotate`` gives one dictionary of the five effect
  attributes, and ``batch_annotate`` gives one dictionary for each
  annotatable.
- Example 5: the entry-point group ``gain.annotation.annotators`` names
  annotators, and an ``AnnotatorBase`` subclass reads the attributes of
  earlier annotators from the context, answers with ``_every``, and gets
  ``batch_annotate`` from the base class.
"""
import pathlib
from collections import Counter
from importlib.metadata import entry_points
from typing import Any

import pytest
from gain import grr
from gain.annotation.annotatable import Annotatable, VCFAllele
from gain.annotation.annotation_factory import (
    load_pipeline_from_yaml,
    register_annotator_factory,
)
from gain.annotation.annotation_pipeline import AttributeSpec
from gain.annotation.annotator_base import AnnotatedValues, AnnotatorBase
from gain.genomic_resources.repository import GenomicResourceRepo

# Unique to this module, because the registration is global to the process.
FOLLOWUP_ANNOTATOR = "docs_overview_followup"


class _FollowupAnnotator(AnnotatorBase):
    """The shape of the page's ``ExperimentalFollowupAnnotator``."""

    def get_attribute_specs(self) -> dict[str, AttributeSpec]:
        return {
            "followup": AttributeSpec(
                source="followup",
                value_type="str",
                description="Whether the position score is high",
                internal_default=False,
                is_default=True,
            ),
        }

    def _do_annotate(
        self, annotatable: Annotatable | None, context: dict[str, Any],
    ) -> AnnotatedValues:
        score = context.get("pos_bedgraph_0")
        return self._every(
            "yes" if score is not None and score > 0.25 else "no")


register_annotator_factory(FOLLOWUP_ANNOTATOR, _FollowupAnnotator)


def test_grr_exports_builders_and_types() -> None:
    """The functions and the GAIn types are in ``gain.grr``."""
    for name in (
        "build_genomic_resource_repository",
        "build_reference_genome_from_resource_id",
        "build_gene_models_from_resource_id",
        "build_score_from_resource_id",
        "build_gene_score_from_resource_id",
        "build_gene_set_collection_from_resource_id",
        "build_liftover_chain_from_resource_id",
        "load_data_frame_from_resource_id",
        "load_ann_data_from_resource_id",
        "ReferenceGenome",
        "GeneModels",
        "GenomicScore",
    ):
        assert name in grr.__all__


def test_grr_does_not_export_context_or_library_types() -> None:
    """``GenomicContext``, ``DataFrame`` and ``AnnData`` stay outside."""
    for name in ("GenomicContext", "DataFrame", "AnnData"):
        assert name not in grr.__all__
        assert not hasattr(grr, name)


def test_directory_repository_of_an_absolute_path(
    mini_grr_dir: pathlib.Path,
) -> None:
    """The page builds a ``directory`` repository of a working directory."""
    repo = grr.build_genomic_resource_repository({
        "id": "local_grr",
        "type": "directory",
        "directory": str(mini_grr_dir),
    })

    assert repo.find_resource("mini_genome") is not None


def test_example_1_chromosome_lengths(repo: GenomicResourceRepo) -> None:
    """Each chromosome of the genome has a length."""
    genome = grr.build_reference_genome_from_resource_id(
        "mini_genome", repo).open()

    lengths = [
        (chrom, genome.get_chrom_length(chrom))
        for chrom in genome.chromosomes
    ]

    assert lengths == [("chr1", 10), ("chr2", 10)]
    genome.close()


def test_example_2_position_scores_across_a_gene(
    repo: GenomicResourceRepo,
) -> None:
    """The clipped segments give one value for each position of the gene."""
    gene_models = grr.build_gene_models_from_resource_id(
        "mini_genemodel_refseq", repo).load()
    transcripts = gene_models.gene_models_by_gene_name("geneA")
    assert transcripts is not None
    tx = transcripts[0]
    chrom, start, end = tx.chrom, tx.tx[0], tx.tx[1]
    score = grr.build_score_from_resource_id(
        "mini_positionscore_bw", repo).open()

    xs = []
    ys = []
    for pos_begin, pos_end, values in score.fetch_region_segments_scores(
            chrom, start, end):
        for p in range(max(pos_begin, start), min(pos_end, end) + 1):
            xs.append(p)
            ys.append(values[0])
    score.close()

    assert (chrom, start, end) == ("chr1", 1, 10)
    assert xs == list(range(1, 11))
    assert ys == [0.0] * 5 + [pytest.approx(0.1)] * 5


def test_example_3_resource_counts_by_type(
    repo: GenomicResourceRepo,
) -> None:
    """The loop counts the resources of each type."""
    types = ["genome", "gene_models", "position_score", "allele_score",
             "fragment_score"]
    counts: Counter[str] = Counter()

    for resource in repo.get_all_resources():
        rid = resource.get_id()
        rtype = resource.get_type()
        if rid.startswith("mini_") and rtype in types:
            counts[rtype] += 1

    assert counts == {
        "genome": 1, "gene_models": 2, "position_score": 12,
        "allele_score": 6, "fragment_score": 1,
    }


def test_example_4_annotate_and_batch_annotate(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """``annotate`` gives one result, ``batch_annotate`` one per allele."""
    pipeline = load_pipeline_from_yaml("""
    - effect_annotator:
        gene_models: mini_genemodel_refseq
        genome: mini_genome
    """, repo, work_dir=tmp_path)
    allele = VCFAllele("chr1", 5, "A", "T")

    result = pipeline.annotate(allele)
    batch_result = pipeline.batch_annotate([allele, allele])

    assert set(result) == {
        "worst_effect", "worst_effect_genes", "gene_effects",
        "effect_details", "gene_list"}
    assert result["worst_effect_genes"] == "geneA"
    assert result["gene_list"] == ["geneA"]
    assert batch_result == [result, result]


def test_example_5_entry_point_group_names_annotators() -> None:
    """``gain.annotation.annotators`` is the group of the plugin's entry."""
    names = {
        ep.name for ep in entry_points(group="gain.annotation.annotators")}

    assert {"effect_annotator", "position_score"} <= names


def test_example_5_plugin_reads_earlier_attributes(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """The last annotator combines what the earlier ones produced."""
    pipeline = load_pipeline_from_yaml(f"""
    - position_score_annotator:
        resource_id: mini_positionscore_bedgraph_tabix_0
    - {FOLLOWUP_ANNOTATOR}: {{}}
    """, repo, work_dir=tmp_path)

    low = pipeline.annotate(VCFAllele("chr1", 2, "A", "T"))
    batch = pipeline.batch_annotate([
        VCFAllele("chr1", 2, "A", "T"), VCFAllele("chr1", 6, "A", "T")])

    assert low["followup"] == "no"
    assert [r["followup"] for r in batch] == ["no", "yes"]
    assert "batch_annotate" not in vars(_FollowupAnnotator)
