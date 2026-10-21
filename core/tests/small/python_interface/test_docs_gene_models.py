"""Pin the offline claims of the "Gene models" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/gene_models.rst``. The examples of the
page use ``hg38/gene_models/MANE/1.5`` and ``TP53``; the tests use the
mini-GRR gene models ``mini_genemodel_refseq`` instead, with the one
coding transcript ``transcriptA`` of ``geneA`` on ``chr1``. The tests pin
these claims:

- Both builders give a ``GeneModels``, and calls that resolve to the same
  resource return the same object.
- ``load()`` returns the object itself, ``is_loaded()`` reports the state,
  and ``close()`` does nothing. Gene models have no ``open()``.
- ``gene_models_by_gene_name`` returns ``None`` for an unknown gene, and
  ``gene_models_by_location`` returns a list.
- A ``TranscriptModel`` has ``tr_id``, ``chrom``, ``strand``, ``tx``,
  ``cds`` and ``exons``, and its region methods return ``BedRegion``
  lists.
- An exon frame is ``None`` until ``update_frames`` computes it, and a
  non-coding exon gets ``-1``.
- ``join_gene_models`` gives a loaded object and rejects an unloaded
  input, and ``from_transcript_models`` gives a loaded object.
"""
import pathlib

import pytest
from gain import grr
from gain.genomic_resources.gene_models import (
    Exon,
    GeneModels,
    TranscriptModel,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.gene_models_builder import a_gene_models
from gain.utils.regions import BedRegion

GENE_MODELS = "mini_genemodel_refseq"
GTF_GENE_MODELS = "mini_genemodel_gtf"


def _fresh_gene_models(tmp_path: pathlib.Path) -> GeneModels:
    """Gene models of a resource that no other test has loaded.

    The objects are shared across calls that resolve to the same resource,
    so a mini-GRR resource can already be loaded by another test.
    """
    return grr.build_gene_models_from_resource(
        a_gene_models().build_resource(tmp_path))


@pytest.fixture(name="genes")
def fixture_genes(repo: GenomicResourceRepo) -> GeneModels:
    """The loaded mini-GRR gene models."""
    return grr.build_gene_models_from_resource_id(GENE_MODELS, repo).load()


def test_both_builders_return_the_same_object(
    repo: GenomicResourceRepo,
) -> None:
    """Calls that resolve to the same resource share one ``GeneModels``."""
    from_id = grr.build_gene_models_from_resource_id(GENE_MODELS, repo)
    from_resource = grr.build_gene_models_from_resource(
        repo.get_resource(GENE_MODELS))

    assert isinstance(from_id, GeneModels)
    assert from_resource is from_id


def test_load_returns_the_object_itself(tmp_path: pathlib.Path) -> None:
    """``load()`` chains to the builder, and ``is_loaded()`` reports it."""
    genes = _fresh_gene_models(tmp_path)
    assert not genes.is_loaded()

    result = genes.load()

    assert result is genes
    assert genes.is_loaded()


def test_close_does_nothing(genes: GeneModels) -> None:
    """``close()`` leaves the annotation loaded and queryable."""
    genes.close()

    assert genes.is_loaded()
    assert genes.gene_models_by_gene_name("geneA") is not None


def test_gene_models_have_no_open(genes: GeneModels) -> None:
    """Gene models do not use the ``open()`` of other resource types."""
    assert not hasattr(genes, "open")


def test_unknown_gene_name_gives_none(genes: GeneModels) -> None:
    """An unknown gene gives ``None``, not an empty list."""
    assert genes.gene_models_by_gene_name("NO_SUCH_GENE") is None


def test_known_gene_name_gives_its_transcripts(genes: GeneModels) -> None:
    """The page's loop prints the id, chromosome, interval and coding."""
    transcripts = genes.gene_models_by_gene_name("geneA")

    assert transcripts is not None
    assert [
        (tm.tr_id, tm.chrom, tm.tx, tm.is_coding()) for tm in transcripts
    ] == [("transcriptA_1", "chr1", (1, 10), True)]


def test_location_lookup_gives_a_list_of_overlaps(
    genes: GeneModels,
) -> None:
    """An interval or a position gives the overlapping transcripts."""
    by_interval = genes.gene_models_by_location("chr1", 3, 20)
    by_position = genes.gene_models_by_location("chr1", 5)
    elsewhere = genes.gene_models_by_location("chr2", 3, 20)

    assert [tm.tr_id for tm in by_interval] == ["transcriptA_1"]
    assert [tm.tr_id for tm in by_position] == ["transcriptA_1"]
    assert elsewhere == []


def test_transcript_attributes(genes: GeneModels) -> None:
    """A transcript has a strand, a CDS and a list of ``Exon`` objects."""
    transcripts = genes.gene_models_by_gene_name("geneA")
    assert transcripts is not None
    tm = transcripts[0]

    assert tm.strand == "+"
    assert tm.cds == (2, 10)
    assert [(e.start, e.stop) for e in tm.exons] == [(1, 10)]
    assert all(isinstance(e, Exon) for e in tm.exons)


def test_region_methods_return_bed_region_lists(genes: GeneModels) -> None:
    """The CDS, UTR and whole-transcript regions are ``BedRegion`` lists."""
    transcripts = genes.gene_models_by_gene_name("geneA")
    assert transcripts is not None
    tm = transcripts[0]

    cds = tm.cds_regions()
    utr5 = tm.utr5_regions()
    utr3 = tm.utr3_regions()
    every = tm.all_regions()

    for regions in (cds, utr5, utr3, every):
        assert isinstance(regions, list)
        assert all(isinstance(r, BedRegion) for r in regions)
    assert [(r.chrom, r.start, r.stop) for r in cds] == [("chr1", 2, 10)]
    assert [(r.start, r.stop) for r in utr5] == [(1, 1)]
    assert utr3 == []
    assert tm.cds_len() == 9


def test_exon_frame_is_none_until_update_frames() -> None:
    """``None`` means not computed, and ``-1`` means a non-coding exon."""
    tm = TranscriptModel(
        "G1", "tx1", "tx1", "chr1", "+",
        tx=(1, 30), cds=(11, 20),
        exons=[Exon(1, 5), Exon(11, 20), Exon(25, 30)],
    )
    assert [e.frame for e in tm.exons] == [None, None, None]

    tm.update_frames()

    assert [e.frame for e in tm.exons] == [-1, 0, -1]


def test_join_gene_models_gives_a_loaded_object(
    repo: GenomicResourceRepo,
) -> None:
    """The joined object holds the transcripts of each input."""
    refseq = grr.build_gene_models_from_resource_id(GENE_MODELS, repo).load()
    gtf = grr.build_gene_models_from_resource_id(GTF_GENE_MODELS, repo).load()

    joined = GeneModels.join_gene_models(refseq, gtf)

    assert joined.is_loaded()
    assert joined.gene_models_by_gene_name("geneA") is not None


def test_join_gene_models_rejects_an_unloaded_input(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    """An unloaded input is an error, not an empty annotation."""
    loaded = grr.build_gene_models_from_resource_id(GENE_MODELS, repo).load()
    unloaded = _fresh_gene_models(tmp_path)

    with pytest.raises(ValueError, match="never loaded"):
        GeneModels.join_gene_models(loaded, unloaded)


def test_from_transcript_models_gives_a_loaded_object(
    repo: GenomicResourceRepo,
) -> None:
    """A dictionary of transcript models becomes a loaded ``GeneModels``."""
    tm = TranscriptModel(
        "G1", "tx1", "tx1", "chr1", "+",
        tx=(1, 10), cds=(2, 9), exons=[Exon(1, 10, 0)],
    )

    genes = GeneModels.from_transcript_models(
        repo.get_resource(GENE_MODELS), {"tx1": tm})

    assert genes.is_loaded()
    found = genes.gene_models_by_gene_name("G1")
    assert found is not None
    assert [t.tr_id for t in found] == ["tx1"]
    assert [t.tr_id for t in genes.gene_models_by_location("chr1", 5)] == [
        "tx1"]
