# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``bind(job, grr, quiet=...)`` of the fragment binner kind.

The grouped job is the conftest's two samples, ``frags/s1`` and
``frags/s2``, grouped by the ``class`` of ``meta/cells``.  On chr1 two
fragments of ``frags/s1`` reach no track: CCC's class is empty and DDD
has no row.  ``frags/s2`` drops none, and neither sample drops a
fragment on chr2.
"""
import logging

import numpy as np
import pytest
from gain.binning.binners import BinningJob, discover_binner_kinds
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.run_definition import parse_run_definition
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

BIN_SIZE = 10
CHR1 = BedRegion("chr1", 1, 40)
CHR2 = BedRegion("chr2", 1, 40)
BY_CLASS = {
    "resource_query": "frags/*",
    "group": {"cell_score_id": "cell", "cell_meta_column": "barcode",
              "group_meta_column": "class"},
    "meta": {"resource_id": "meta/cells",
             "filter": [{"column": "sample_id", "label": "sample_id"}]},
}


@pytest.fixture
def grouped_job(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> BinningJob:
    (job,) = parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{"fragment_score_binner": BY_CLASS}],
    }, repo, genome).jobs
    return job


def bin_two_regions(
    job: BinningJob, repo: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture, **bind_args: bool,
) -> tuple[list[np.ndarray], dict[tuple[str, str], int], list[str]]:
    """The blocks of chr1 and chr2, the dropped record and the log lines."""
    with caplog.at_level(logging.INFO, logger="gain.binning"), \
            discover_binner_kinds()[job.binner].bind(
                job, repo, **bind_args) as bound:
        blocks = [bound.bin_region(region, BIN_SIZE)
                  for region in (CHR1, CHR2)]
    assert isinstance(bound, FragmentScoreBinding)
    logged = [r.getMessage() for r in caplog.records
              if "dropped" in r.getMessage()]
    return blocks, bound.dropped, logged


def test_a_quiet_binding_keeps_no_dropped_record_and_logs_no_line(
    grouped_job: BinningJob, repo: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, dropped, logged = bin_two_regions(
        grouped_job, repo, caplog, quiet=True)

    assert dropped == {}
    assert logged == []


@pytest.mark.parametrize("bind_args", [{}, {"quiet": False}])
def test_a_binding_that_is_not_quiet_records_and_logs_each_region(
    grouped_job: BinningJob, repo: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture, bind_args: dict[str, bool],
) -> None:
    _, dropped, logged = bin_two_regions(
        grouped_job, repo, caplog, **bind_args)

    assert dropped == {
        ("frags/s1", "chr1:1-40"): 2, ("frags/s2", "chr1:1-40"): 0,
        ("frags/s1", "chr2:1-40"): 0, ("frags/s2", "chr2:1-40"): 0}
    assert len(logged) == 4
    assert "frags/s1 chr1:1-40: 2 fragments dropped" in logged[0]


def test_a_quiet_binding_bins_the_same_values(
    grouped_job: BinningJob, repo: GenomicResourceRepo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    loud, _, _ = bin_two_regions(grouped_job, repo, caplog)

    quiet, _, _ = bin_two_regions(grouped_job, repo, caplog, quiet=True)

    for expected, block in zip(loud, quiet, strict=True):
        np.testing.assert_array_equal(block, expected)
