# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from collections.abc import Iterator

import pytest
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    a_reference_genome,
)

CHR1_LENGTH = 100
CHR2_LENGTH = 40


@pytest.fixture
def grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "grr"


@pytest.fixture
def repo(grr_dir: pathlib.Path) -> GenomicResourceRepo:
    """A tabix-backed toy GRR: one genome, two position scores.

    ``scores/one`` covers 1-20 of chr1 with the value 1.0 and 31-35 with
    2.0, leaving 21-30 and everything after 35 uncovered; its declared
    aggregator is ``max``.  ``scores/two`` covers 1-10 with 4.0 and
    declares ``mean``.  ``other/three`` is a third position score kept
    outside the ``scores/`` prefix so that a glob can be seen to exclude
    it.
    """
    grr = (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * CHR1_LENGTH)
                       .with_chromosome("chr2", "C" * CHR2_LENGTH))
        .with_resource("scores/one", a_position_score()
                       .with_score("s", "float")
                       .with_aggregator("max")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  s
                           chr1   1          20       1.0
                           chr1   31         35       2.0
                       """))
        .with_resource("scores/two", a_position_score()
                       .with_score("t", "float")
                       .with_aggregator("mean")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  t
                           chr1   1          10       4.0
                       """))
        .with_resource("other/three", a_position_score()
                       .with_score("u", "float")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  u
                           chr2   1          40       8.0
                       """))
        .with_resource("other/pair", a_position_score()
                       .with_score("p", "float")
                       .with_score("q", "float")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  p    q
                           chr1   1          10       1.0  2.0
                       """))
        .with_resource("other/label", a_position_score()
                       .with_score("v", "str")
                       .with_tabix()
                       .with_data("""
                           chrom  pos_begin  pos_end  v
                           chr1   1          10       lo
                       """))
    )
    return grr.build_repo(grr_dir)


@pytest.fixture
def genome(repo: GenomicResourceRepo) -> Iterator[ReferenceGenome]:
    genome = build_reference_genome_from_resource(
        repo.get_resource("genome")).open()
    yield genome
    genome.close()
