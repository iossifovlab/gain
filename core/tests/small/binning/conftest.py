# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from collections.abc import Iterator

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)
from gain.genomic_resources.testing.data_frame_builder import a_data_frame

CHR1_LENGTH = 100
CHR2_LENGTH = 40
SHORT_CHR1_LENGTH = 50

#: Two samples' fragments, ``frags/s1`` and ``frags/s2`` -- the shape of a
#: single-cell fragment file: ``cell``, the barcode every fragment
#: carries, and ``count``, the read pairs behind it.  ``frags/s2`` has no
#: chr2.  Each carries its ``sample_id`` label, which the metadata
#: table's ``sample_id`` column is filtered by.
S1_FRAGMENTS = """
    chrom  pos_begin  pos_end  cell  count
    chr1   3          12       AAA   2
    chr1   10         25       BBB   5
    chr1   11         14       CCC   1
    chr1   15         40       AAA   3
    chr1   33         34       DDD   4
    chr2   5          9        BBB   6
"""
S2_FRAGMENTS = """
    chrom  pos_begin  pos_end  cell  count
    chr1   4          8        AAA   7
    chr1   12         20       EEE   1
    chr1   16         22       EEE   4
    chr1   35         50       EEE   2
"""
#: The cell metadata of both samples, and of a third no fragment resource
#: names.  In S1, AAA is T and BBB is B; CCC's class is empty and DDD is
#: not in the table at all, so their fragments reach no track.  In S2 the
#: barcode AAA is a different cell, of class B.  S3's row brings a class,
#: X, that only an unfiltered table would show.  Written verbatim: a
#: whitespace block has no spelling for an empty cell.
CELL_META = """sample_id,barcode,class
S1,AAA,T
S1,BBB,B
S1,CCC,
S2,AAA,B
S2,EEE,T
S3,BBB,X
"""


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
    it.  ``frags/s1``, ``frags/s2`` and ``meta/cells`` are two samples'
    fragment scores and their cell metadata table (see ``S1_FRAGMENTS``,
    ``S2_FRAGMENTS`` and ``CELL_META``).
    """
    grr = (
        a_grr()
        .with_resource("genome", a_reference_genome()
                       .with_chromosome("chr1", "A" * CHR1_LENGTH)
                       .with_chromosome("chr2", "C" * CHR2_LENGTH))
        # A second genome, with chr1 only and shorter, so which genome a
        # run resolved is visible in what it accepts and produces.
        .with_resource("genomes/short", a_reference_genome()
                       .with_chromosome("chr1", "G" * SHORT_CHR1_LENGTH))
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
        .with_resource("frags/s1", a_fragment_score()
                       .with_score("cell", "str")
                       .with_score("count", "int")
                       .with_labels(sample_id="S1")
                       .with_tabix()
                       .with_data(S1_FRAGMENTS))
        .with_resource("frags/s2", a_fragment_score()
                       .with_score("cell", "str")
                       .with_score("count", "int")
                       .with_labels(sample_id="S2")
                       .with_tabix()
                       .with_data(S2_FRAGMENTS))
        .with_resource("meta/cells", a_data_frame()
                       .with_raw_content(CELL_META))
    )
    return grr.build_repo(grr_dir)


@pytest.fixture
def indexed_repo(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
) -> GenomicResourceRepo:
    """The toy GRR with its full-text index published.

    The index is what answers a ``search_term``; the test repository
    comes with manifests but no index, so it is published the way an
    operator would, through ``grr_manage``.  The repository reads the
    index from disk on every search, so ``repo`` itself sees it.
    """
    cli_manage(["repo-index", "-R", str(grr_dir)])
    return repo


@pytest.fixture
def genome(repo: GenomicResourceRepo) -> Iterator[ReferenceGenome]:
    genome = build_reference_genome_from_resource(
        repo.get_resource("genome")).open()
    yield genome
    genome.close()
