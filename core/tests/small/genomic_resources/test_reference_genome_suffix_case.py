# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Reference-genome decisions taken from a filename suffix (gain#975).

Two of them: whether the resource's file set includes the ``.gzi`` BGZF
index, and which sequence backend reads the FASTA -- pysam-bgzip or a raw
byte-offset seek.  Both used to match the suffix case-sensitively, so a
genome named ``GENOME.FA.BGZ`` was treated as uncompressed: the ``.gzi``
was dropped from the file set, and the raw-seek reader was pointed at
BGZF bytes, where it died decoding them as ASCII.  This is the defect
class gain#348 fixed for genomic position tables; these tests pin the
same rule here.

The lower-case spellings are covered by ``test_reference_genome_resource``
and are repeated here only as controls -- a case-sensitivity test that
cannot fail on the lower-case spelling proves it discriminates on case.
"""
import pathlib

import pytest
from gain.genomic_resources.reference_genome import reference_genome_files
from gain.genomic_resources.testing import setup_genome_bgz

#: One chromosome over two 10-base lines, so a fetch can cross the FASTA
#: line break -- the read the raw-seek backend has to get right, and so a
#: meaningful assertion once the correct backend is selected.
GENOME_CONTENT = """
    >chr1
    ACCCAAACGG
    GCCTTCCAAT
"""


@pytest.mark.parametrize("filename", [
    "genome.fa.gz",
    "genome.FA.GZ",
    "GENOME.FA.BGZ",
    "genome.fa.Gz",
])
def test_compressed_genome_file_set_includes_the_gzi_index(
    filename: str,
) -> None:
    assert reference_genome_files({"filename": filename}) == {
        filename, f"{filename}.fai", f"{filename}.gzi",
    }


def test_an_upper_case_plain_genome_gains_no_gzi_index() -> None:
    assert reference_genome_files({"filename": "GENOME.FA"}) == {
        "GENOME.FA", "GENOME.FA.fai",
    }


@pytest.mark.parametrize("filename", [
    "genome.fa.gz",
    "genome.FA.GZ",
    "GENOME.FA.BGZ",
    "genome.fa.Gz",
])
def test_bgzipped_genome_reads_its_sequence_whatever_the_suffix_case(
    tmp_path: pathlib.Path, filename: str,
) -> None:
    genome = setup_genome_bgz(tmp_path / filename, GENOME_CONTENT)

    with genome:
        # Spans the line break after base 10.
        assert genome.get_sequence("chr1", 6, 14) == "AACGGGCCT"
