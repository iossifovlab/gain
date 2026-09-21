"""The stored chromosome lengths are read before the table is (gain#1578).

``GenomicScoreImplementation.get_chrom_lengths(grr)`` answers from
``statistics/chrom_lengths.json`` whenever the freshness gate calls it
``CURRENT`` -- no table opened, no genome resolved -- and runs the live
ladder, as before, in every other state.
"""

import pathlib

import pytest_mock
from gain.genomic_resources.genomic_position_table import ChromLengthSource
from gain.genomic_resources.genomic_scores import GenomicScore
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    ChromLength,
)

from .test_genomic_scores_impl_chrom_lengths import (
    CHR1_GENOME_LENGTH,
    CHR1_PROBE_BOUND,
    CHRM_PROBE_BOUND,
    patch_tabix_probe,
)
from .test_genomic_scores_impl_derived_files import (
    a_repaired_labelled_score,
)


def test_a_current_file_answers_without_the_table(
    tmp_path: pathlib.Path, mocker: pytest_mock.MockerFixture,
) -> None:
    """The records the repair stored ARE the answer: the genome's length
    beside the probe's bound for chr1, the bound alone for chrM."""
    impl, repo = a_repaired_labelled_score(tmp_path)
    opened = mocker.spy(GenomicScore, "open")
    probe = patch_tabix_probe(mocker)

    lengths = impl.get_chrom_lengths(repo)

    assert lengths == {
        "chr1": ChromLength(
            answers={
                ChromLengthSource.REFERENCE_GENOME: CHR1_GENOME_LENGTH,
                ChromLengthSource.TABIX_ESTIMATE: CHR1_PROBE_BOUND,
            },
            extent=None),
        "chrM": ChromLength(
            answers={ChromLengthSource.TABIX_ESTIMATE: CHRM_PROBE_BOUND},
            extent=None),
    }
    opened.assert_not_called()
    probe.assert_not_called()
