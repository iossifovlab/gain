# pylint: disable=C0116
# ruff: file-ignore[suspicious-non-cryptographic-random-usage]
# Seeded `random` builds test data here, not secrets.
"""How a reference genome's statistics build is cut into tasks (gain#1788).

Contigs that fit whole into one region are packed together, so a
scaffold-heavy assembly costs a handful of tasks rather than three per
contig, while every contig still gets its own statistics file.
"""
import pathlib
import random
import time

import pytest
from gain.genomic_resources.implementations.reference_genome_impl import (
    ReferenceGenomeImplementation,
    ReferenceGenomeStatistics,
    _pack_contigs,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import a_reference_genome
from gain.task_graph.cli_tools import task_graph_run
from gain.task_graph.graph import TaskGraph
from gain.task_graph.sequential_executor import SequentialExecutor

SCAFFOLDS = [f"scaf{i}" for i in range(10)]


def _scaffold_heavy_genome(
    tmp_path: pathlib.Path,
) -> ReferenceGenomeImplementation:
    builder = a_reference_genome().with_chromosome("chrA", "ACGTN" * 8)
    for scaffold in SCAFFOLDS:
        builder = builder.with_chromosome(scaffold, "ACGT")
    impl = build_resource_implementation(builder.build_resource(tmp_path))
    assert isinstance(impl, ReferenceGenomeImplementation)
    return impl


def _run(impl: ReferenceGenomeImplementation, region_size: int) -> None:
    graph = TaskGraph()
    graph.add_tasks(impl.create_statistics_build_tasks(region_size=region_size))
    task_graph_run(graph, SequentialExecutor())


def test_whole_contigs_are_packed_up_to_the_longest_contig(
    tmp_path: pathlib.Path,
) -> None:
    impl = _scaffold_heavy_genome(tmp_path)

    tasks = impl.create_statistics_build_tasks(region_size=0)

    # chrA (40 bp) alone; the ten 4 bp scaffolds fill one 40 bp batch;
    # then the global statistic.
    assert len(tasks) == 3


def test_a_split_contig_merges_and_saves_in_one_task(
    tmp_path: pathlib.Path,
) -> None:
    impl = _scaffold_heavy_genome(tmp_path)

    task_ids = [
        desc.task.task_id
        for desc in impl.create_statistics_build_tasks(region_size=20)
    ]

    # chrA in two 20 bp regions plus one merge-and-save; the scaffolds in
    # two 20 bp batches (five each); the global statistic.
    assert task_ids == [
        "_count_nucleotides_chrA:1-20",
        "_count_nucleotides_chrA:21",
        "_merge_and_save_chrom_statistics_chrA",
        "_chrom_statistics_batch_scaf0",
        "_chrom_statistics_batch_scaf5",
        "_global_statistics",
    ]


@pytest.mark.parametrize("region_size", [0, 20, 3])
def test_packed_build_writes_every_contig_statistic(
    tmp_path: pathlib.Path, region_size: int,
) -> None:
    impl = _scaffold_heavy_genome(tmp_path)

    _run(impl, region_size)

    stats = ReferenceGenomeStatistics.build_statistics(impl.resource)
    assert stats is not None
    assert list(stats.chrom_statistics) == ["chrA", *SCAFFOLDS]
    chr_a = stats.chrom_statistics["chrA"]
    assert chr_a.length == 40
    assert chr_a.nucleotide_counts == {"A": 8, "C": 8, "G": 8, "T": 8, "N": 8}
    # The pair across a region boundary is counted once, as unsplit.
    assert sum(chr_a.nucleotide_pair_counts.values()) == 39
    for scaffold in SCAFFOLDS:
        scaf = stats.chrom_statistics[scaffold]
        assert scaf.length == 4
        assert scaf.nucleotide_counts == {
            "A": 1, "C": 1, "G": 1, "T": 1, "N": 0}
    assert stats.global_statistic.length == 80


def test_packing_keeps_every_contig_once_within_capacity() -> None:
    rng = random.Random(7)
    lengths = {f"c{i}": rng.randint(1, 100) for i in range(2_000)}

    batches = _pack_contigs(lengths, 100)

    packed = [chrom for batch in batches for chrom in batch]
    assert sorted(packed) == sorted(lengths)
    assert all(sum(lengths[c] for c in batch) <= 100 for batch in batches)
    # Near the lower bound of total length over capacity.
    assert len(batches) <= sum(lengths.values()) // 100 + 20


def test_packing_many_contigs_into_many_batches_is_fast() -> None:
    # Each contig just over half the capacity: one batch per contig, the
    # shape that makes a scan over every open batch quadratic.
    rng = random.Random(7)
    lengths = {
        f"c{i}": 500_001 + rng.randint(0, 1_000) for i in range(30_000)}

    start = time.perf_counter()
    batches = _pack_contigs(lengths, 1_000_000)

    assert len(batches) == 30_000
    assert time.perf_counter() - start < 5
