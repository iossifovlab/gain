"""``statistics/chrom_lengths.json`` as written and read back (gain#1576).

One block per source, contig to length; the table's own block carries the
reason for a contig the table had no length for, and no other block does.
"""

import json
import pathlib

from gain.genomic_resources.genomic_position_table import (
    ChromLengthSource,
    ContigExtent,
)
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
    ChromLength,
    DerivedFrom,
    StoredChromLengths,
    load_chrom_lengths,
    save_chrom_lengths,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_basic_resource

GENOME = ChromLengthSource.REFERENCE_GENOME
ESTIMATE = ChromLengthSource.TABIX_ESTIMATE

#: A labelled tabix score's records: chr1 in the genome and the table,
#: chrM in the table only, chrUn proven empty, chrX unmeasurable.
LENGTHS = {
    "chr1": ChromLength({GENOME: 3000, ESTIMATE: 3052}, None),
    "chrM": ChromLength({ESTIMATE: 48}, None),
    "chrUn": ChromLength({GENOME: 500}, ContigExtent.EMPTY),
    "chrX": ChromLength({}, ContigExtent.UNDETERMINED),
}
DERIVED_FROM = DerivedFrom("genome", {"data.txt.gz": "abc", "x.tbi": None})


def _a_resource(tmp_path: pathlib.Path) -> GenomicResource:
    return a_basic_resource().build_resource(tmp_path)


def test_every_source_gets_a_block_and_the_tables_carries_the_reasons(
    tmp_path: pathlib.Path,
) -> None:
    """A contig a source did not answer is simply absent from its block."""
    resource = _a_resource(tmp_path)

    save_chrom_lengths(
        resource, StoredChromLengths(LENGTHS, DERIVED_FROM), ESTIMATE)

    assert json.loads((tmp_path / CHROM_LENGTHS_FILE).read_text()) == {
        "format": 1,
        "derived_from": {
            "reference_genome": "genome",
            "files_md5": {"data.txt.gz": "abc", "x.tbi": None},
        },
        "sources": {
            "reference_genome": {"chr1": 3000, "chrUn": 500},
            "tabix_estimate": {
                "chr1": 3052, "chrM": 48,
                "chrUn": "empty", "chrX": "undetermined",
            },
        },
    }


def test_what_was_saved_is_what_loads_in_the_same_order(
    tmp_path: pathlib.Path,
) -> None:
    """The round trip keeps every answer, every reason and the table's
    contig order, which the table's block -- written first -- carries."""
    resource = _a_resource(tmp_path)
    save_chrom_lengths(
        resource, StoredChromLengths(LENGTHS, DERIVED_FROM), ESTIMATE)

    loaded = load_chrom_lengths(resource)

    assert loaded == StoredChromLengths(LENGTHS, DERIVED_FROM)
    assert list(loaded.lengths) == list(LENGTHS)


def test_a_resource_without_the_file_loads_none(
    tmp_path: pathlib.Path,
) -> None:
    assert load_chrom_lengths(_a_resource(tmp_path)) is None
