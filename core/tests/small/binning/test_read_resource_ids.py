# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``read_resource_ids`` of a job: every resource that the job reads.

The jobs come from the conftest's GRR.  ``frags/s1`` and ``frags/s2``
are two samples, and ``meta/cells`` is their cell metadata table.
"""
from typing import Any

import numpy as np
from gain.binning.binners import BinningJob, discover_binner_kinds
from gain.binning.fragment_binner import FragmentBinningJob
from gain.binning.run_definition import parse_run_definition
from gain.genomic_resources.reference_genome import ReferenceGenome
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.utils.regions import BedRegion

BIN_SIZE = 10
CHR1 = BedRegion("chr1", 1, 40)
BY_CLASS = {
    "resource_query": "frags/*",
    "group": {"cell_score_id": "cell", "cell_meta_column": "barcode",
              "group_meta_column": "class"},
    "meta": {"resource_id": "meta/cells",
             "filter": [{"column": "sample_id", "label": "sample_id"}]},
}


def one_job(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
    kind: str, entry: dict[str, Any],
) -> BinningJob:
    (job,) = parse_run_definition({
        "bins": {"bin_size": BIN_SIZE},
        "binners": [{kind: entry}],
    }, repo, genome).jobs
    return job


def test_a_cell_grouped_job_reads_its_fragments_and_its_metadata(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    job = one_job(repo, genome, "fragment_score_binner", BY_CLASS)

    assert job.read_resource_ids == ("frags/s1", "frags/s2", "meta/cells")


def test_a_value_grouped_job_reads_only_its_fragments(
    stats_repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    job = one_job(stats_repo, genome, "fragment_score_binner", {
        "resource_query": "frags/s1", "group": {"group_score_id": "cell"}})

    assert isinstance(job, FragmentBinningJob)
    assert job.read_resource_ids == job.resource_ids == ("frags/s1",)


def test_a_position_job_reads_the_resources_of_its_tracks(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    job = one_job(repo, genome, "position_score_binner",
                  {"resource_query": "scores/one"})

    assert job.read_resource_ids == ("scores/one",)


def test_a_cell_grouped_job_bins_its_classes_over_a_region(
    repo: GenomicResourceRepo, genome: ReferenceGenome,
) -> None:
    # The starts of the fragments, by bin.  1-10: AAA of S1 (T), BBB of
    # S1 (B) and AAA of S2 (B).  11-20: AAA of S1 and EEE twice (T), and
    # CCC, which has no class.  31-40: EEE (T), and DDD, which has no row.
    job = one_job(repo, genome, "fragment_score_binner", BY_CLASS)

    with discover_binner_kinds()[job.binner].bind(job, repo) as bound:
        block = bound.bin_region(CHR1, BIN_SIZE)

    assert [track.selector for track in job.tracks] == ["B", "T"]
    np.testing.assert_array_equal(
        block, [[2.0, 1.0], [0.0, 3.0], [0.0, 0.0], [0.0, 1.0]])
