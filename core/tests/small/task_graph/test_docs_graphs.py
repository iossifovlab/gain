"""The running example of the docs page "Graphs" of the task graph chapter.

The page ``docs/source/development/task_graph/graphs.rst`` pulls the
example code from this module with ``literalinclude``. The task functions
and the graph builder are module-level, so a task executor can pickle them
and other tests can import the builder and run its graph.
"""
import pathlib
import textwrap

import pytest
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_genome,
)
from gain.task_graph.graph import Task, TaskGraph


# The docs example of the task functions starts here.
def count_n(
    resource: GenomicResource, chrom: str, start: int, end: int,
) -> int:
    """Count the ``N`` bases of a region of the genome in ``resource``."""
    genome = build_reference_genome_from_resource(resource)
    with genome.open():
        return genome.get_sequence(chrom, start, end).count("N")


def sum_counts(*counts: int) -> int:
    """Return the sum of the counts of the chromosome tasks."""
    return sum(counts)
# The docs example of the task functions ends here.


# The docs example of the graph builder starts here.
def build_n_count_graph(
    resource: GenomicResource,
) -> tuple[TaskGraph, Task]:
    """Build a task graph that counts the ``N`` bases of a genome."""
    genome = build_reference_genome_from_resource(resource)
    with genome.open():
        lengths = genome.get_all_chrom_lengths()

    graph = TaskGraph()
    chrom_tasks = [
        graph.create_task(
            f"count_n_{chrom}", count_n,
            args=[resource, chrom, 1, length])
        for chrom, length in lengths.items()
    ]
    summary = graph.create_task("sum_n", sum_counts, args=chrom_tasks)
    return graph, summary
# The docs example of the graph builder ends here.


@pytest.fixture(name="genome_resource")
def fixture_genome_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """Return a genome resource with three chromosomes."""
    setup_genome(tmp_path / "genome.fa", textwrap.dedent("""
        >chr1
        NNACGTACGT
        ACGTNNNACG
        >chr2
        ACGTACGTAC
        >chr3
        NACGTACGTN
    """))
    return build_filesystem_test_resource(tmp_path)


def test_graph_has_one_task_for_each_chromosome_and_a_summary(
    genome_resource: GenomicResource,
) -> None:
    """The builder adds one task for each chromosome and a summary task."""
    graph, summary = build_n_count_graph(genome_resource)

    assert [task.task_id for task in graph.tasks] == [
        "count_n_chr1", "count_n_chr2", "count_n_chr3", "sum_n"]
    assert summary.task_id == "sum_n"


def test_summary_task_depends_on_the_chromosome_tasks(
    genome_resource: GenomicResource,
) -> None:
    """The summary task takes the chromosome tasks as args and deps."""
    graph, summary = build_n_count_graph(genome_resource)

    summary_desc = graph.get_task_desc(summary)

    assert sorted(dep.task_id for dep in summary_desc.deps) == [
        "count_n_chr1", "count_n_chr2", "count_n_chr3"]
    assert [arg.task_id for arg in summary_desc.args] == [
        "count_n_chr1", "count_n_chr2", "count_n_chr3"]


def test_chromosome_task_passes_the_resource_and_the_whole_chromosome(
    genome_resource: GenomicResource,
) -> None:
    """A chromosome task passes the resource, not an open genome."""
    graph, _ = build_n_count_graph(genome_resource)

    chr1 = graph.get_task_desc(Task("count_n_chr1"))

    assert chr1.func is count_n
    assert chr1.args == [genome_resource, "chr1", 1, 20]
    assert not chr1.deps
    assert count_n(*chr1.args) == 5
