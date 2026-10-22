"""Pin the offline claims of the "Reference genomes" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/reference_genomes.rst``. The examples
of the page use ``hg38/genomes/GRCh38-hg38``; the tests use the mini-GRR
genome ``mini_genome`` instead, with two chromosomes of 10 nucleotides:
``chr1`` is ``TATGAAATAA``. The tests pin these claims:

- Both builders, from a resource id and from a resource, give a
  ``ReferenceGenome``.
- A genome is initially closed, ``open()`` returns the genome itself, and
  a ``with`` block closes it but does not open it.
- Chromosome names and lengths answer while the genome is closed, and
  ``get_sequence`` needs an open genome.
- ``chromosomes`` follows the FASTA index, ``get_all_chrom_lengths``
  returns the internal dictionary, and ``chrom_prefix`` comes from the
  contig names.
- ``get_sequence`` uses 1-based coordinates with both ends included.
- ``fetch`` yields the sequence one nucleotide at a time.
- ``split_into_regions`` divides a genome, or one chromosome, into
  regions, and the last region of each chromosome has no ``end``.
- ``is_pseudoautosomal`` reads the ``PARS`` of the resource definition.
"""
import pathlib
import types

import pytest
from gain import grr
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import setup_directories, setup_genome
from gain.utils.regions import Region

GENOME = "mini_genome"


@pytest.fixture(name="genome")
def fixture_genome(repo: GenomicResourceRepo) -> ReferenceGenome:
    """The closed mini-GRR genome."""
    return grr.build_reference_genome_from_resource_id(GENOME, repo)


def test_both_builders_give_a_reference_genome(
    repo: GenomicResourceRepo,
) -> None:
    """The builders take a resource id or a ``GenomicResource``."""
    from_id = grr.build_reference_genome_from_resource_id(GENOME, repo)
    from_resource = grr.build_reference_genome_from_resource(
        repo.get_resource(GENOME))

    assert isinstance(from_id, ReferenceGenome)
    assert isinstance(from_resource, ReferenceGenome)
    assert from_resource.resource_id == from_id.resource_id == GENOME


def test_genome_is_initially_closed(genome: ReferenceGenome) -> None:
    """A newly built genome is closed."""
    assert not genome.is_open()


def test_open_returns_the_genome_itself(genome: ReferenceGenome) -> None:
    """``open()`` returns the genome, so it chains to the builder."""
    result = genome.open()

    assert result is genome
    assert genome.is_open()
    genome.close()


def test_with_block_closes_but_does_not_open(
    genome: ReferenceGenome,
) -> None:
    """A ``with`` block closes the genome that ``open()`` opened."""
    with genome.open() as opened:
        assert opened is genome
        assert opened.get_sequence("chr1", 1, 3) == "TAT"

    assert not genome.is_open()


def test_close_closes_an_open_genome(genome: ReferenceGenome) -> None:
    """``close()`` closes the genome, and ``is_open()`` reports it."""
    genome.open()

    genome.close()

    assert not genome.is_open()


def test_names_and_lengths_answer_while_closed(
    genome: ReferenceGenome,
) -> None:
    """Chromosome names and lengths need no open genome."""
    assert genome.chromosomes == ["chr1", "chr2"]
    assert genome.get_chrom_length("chr1") == 10
    assert genome.get_all_chrom_lengths() == {"chr1": 10, "chr2": 10}
    assert not genome.is_open()


def test_get_sequence_requires_an_open_genome(
    genome: ReferenceGenome,
) -> None:
    """``get_sequence`` on a closed genome fails."""
    with pytest.raises(AssertionError):
        genome.get_sequence("chr1", 1, 3)


def test_all_chrom_lengths_is_the_internal_dictionary(
    genome: ReferenceGenome,
) -> None:
    """``get_all_chrom_lengths`` returns the same dictionary each time."""
    assert genome.get_all_chrom_lengths() is genome.get_all_chrom_lengths()


def test_chrom_prefix_comes_from_the_contig_names(
    genome: ReferenceGenome,
) -> None:
    """The mini genome names its contigs ``chr1``, so the prefix is chr."""
    assert genome.chrom_prefix == "chr"


def test_get_sequence_is_one_based_and_inclusive(
    genome: ReferenceGenome,
) -> None:
    """Positions 1 to 3 of ``TATGAAATAA`` are ``TAT``."""
    with genome.open():
        assert genome.get_sequence("chr1", 1, 3) == "TAT"
        assert genome.get_sequence("chr1", 4, 4) == "G"


def test_fetch_yields_one_nucleotide_at_a_time(
    genome: ReferenceGenome,
) -> None:
    """``fetch`` is a generator of single nucleotides, not one string."""
    with genome.open():
        result = genome.fetch("chr1", 1, 10, buffer_size=3)

        assert isinstance(result, types.GeneratorType)
        nucleotides = list(result)

    assert nucleotides == list("TATGAAATAA")


def test_split_into_regions_of_one_chromosome(
    genome: ReferenceGenome,
) -> None:
    """The page's ``chromosome=`` argument restricts the division."""
    regions = list(genome.split_into_regions(4, chromosome="chr1"))

    assert [(r.chrom, r.start, r.stop) for r in regions] == [
        ("chr1", 1, 4), ("chr1", 5, 8), ("chr1", 9, None),
    ]
    assert all(isinstance(r, Region) for r in regions)


def test_last_region_of_each_chromosome_has_no_end(
    genome: ReferenceGenome,
) -> None:
    """``Region(chrom, start, None)`` closes each chromosome."""
    regions = list(genome.split_into_regions(4))

    last = [r for r in regions if r.end is None]
    assert [(r.chrom, r.start) for r in last] == [("chr1", 9), ("chr2", 9)]


def test_is_pseudoautosomal_reads_pars(tmp_path: pathlib.Path) -> None:
    """The ``PARS`` of the definition mark the pseudoautosomal regions."""
    # ``setup_genome`` writes its own config, so the config comes second.
    setup_genome(tmp_path / "genome.fa", ">chrX\nACGTACGTACGT\n")
    setup_directories(tmp_path, {
        "genomic_resource.yaml": (
            "type: genome\n"
            "filename: genome.fa\n"
            "PARS:\n"
            "  X:\n"
            "    - chrX:1-5\n"
            "  Y: null\n"
        ),
    })
    repo = grr.build_genomic_resource_repository({
        "id": "pars", "type": "directory", "directory": str(tmp_path),
    })
    genome = grr.build_reference_genome_from_resource(repo.get_resource(""))

    assert genome.is_pseudoautosomal("chrX", 3)
    assert not genome.is_pseudoautosomal("chrX", 10)
