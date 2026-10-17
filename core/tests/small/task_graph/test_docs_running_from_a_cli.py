"""The running example, part 3, of the docs page "Running from a CLI".

The page ``docs/source/development/task_graph/running_from_a_cli.rst``
pulls the example code from this module with ``literalinclude``. The
example runs the graph of the page "Graphs", which ``test_docs_graphs``
builds, from a command line.
"""
import argparse
import pathlib
import sys

import pytest
from gain.genomic_resources.genomic_context import (
    build_cli_genomic_context,
    context_providers_add_argparser_arguments,
    register_context_provider,
)
from gain.genomic_resources.genomic_context_cli import (
    CLIGenomicContextProvider,
)
from gain.genomic_resources.repository_factory import (
    build_genomic_resource_repository,
)
from gain.task_graph import TaskGraphCli
from gain.task_graph.cache import CacheRecordType, FileTaskCache

from .test_docs_graphs import build_n_count_graph

# The fixture genome has 5, 0 and 2 ``N`` bases in its three chromosomes.
N_COUNT = 7

# Keep the task log directory of each task executor out of the tree.
pytestmark = pytest.mark.usefixtures("work_in_tmp_path")


# The docs example of a command line script starts here.
def main(argv: list[str] | None = None) -> bool:
    """Count the ``N`` bases of a reference genome from a command line."""
    parser = argparse.ArgumentParser(
        description="Count the N bases of a reference genome.")
    context_providers_add_argparser_arguments(parser)
    TaskGraphCli.add_arguments(parser)
    args = parser.parse_args(argv)

    context = build_cli_genomic_context(vars(args))
    genome = context.get_reference_genome()
    if genome is None:
        parser.error("name the reference genome with -R")

    graph, _ = build_n_count_graph(genome.resource)
    return TaskGraphCli.process_graph(graph, **vars(args))
# The docs example of a command line script ends here.


# The docs example of a console script starts here.
def count_n() -> None:
    """Run ``main`` and exit with status 0 only when every task succeeds."""
    sys.exit(0 if main(sys.argv[1:]) else 1)
# The docs example of a console script ends here.


@pytest.fixture(name="cli_context_provider")
def fixture_cli_context_provider(
    clean_genomic_context_providers: None,
) -> None:
    """Register only the CLI context provider, for the test alone.

    ``clean_genomic_context_providers`` empties the registered providers,
    so the providers of one test do not reach the next test.
    """
    register_context_provider(CLIGenomicContextProvider())


def grr_args(grr_dir: pathlib.Path) -> list[str]:
    """Return the options that name the directory GRR and the genome."""
    return ["--grr-directory", str(grr_dir), "-R", "genome"]


def read_records(
    grr_dir: pathlib.Path, status_dir: pathlib.Path,
) -> dict[str, tuple[CacheRecordType, object]]:
    """Read the record type and result of each task of a fresh graph."""
    repo = build_genomic_resource_repository({
        "id": "local", "type": "directory", "directory": str(grr_dir)})
    graph, _ = build_n_count_graph(repo.get_resource("genome"))
    task_cache = FileTaskCache(cache_dir=str(status_dir))
    records = {}
    for task in graph.tasks:
        record = task_cache.get_record(graph.get_task_desc(task))
        records[task.task_id] = (record.type, record.result)
    return records


@pytest.mark.usefixtures("cli_context_provider")
def test_sequential_run_writes_a_computed_record_for_each_task(
    genome_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """A run with ``-j 1`` computes each task, and the summary holds the sum."""
    status_dir = tmp_path / "task-status"

    assert main([*grr_args(genome_grr_dir), "-j", "1", "-d", str(status_dir)])

    records = read_records(genome_grr_dir, status_dir)
    assert {task_id: rt for task_id, (rt, _) in records.items()} == {
        "count_n_chr1": CacheRecordType.COMPUTED,
        "count_n_chr2": CacheRecordType.COMPUTED,
        "count_n_chr3": CacheRecordType.COMPUTED,
        "sum_n": CacheRecordType.COMPUTED,
    }
    assert records["sum_n"][1] == N_COUNT


@pytest.mark.usefixtures("cli_context_provider")
def test_status_prints_computed_for_each_task_after_a_run(
    genome_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``status`` command prints the record type of each task."""
    status_dir = str(tmp_path / "task-status")
    assert main([*grr_args(genome_grr_dir), "-j", "1", "-d", status_dir])
    capsys.readouterr()

    assert main(
        [*grr_args(genome_grr_dir), "status", "-j", "1", "-d", status_dir])

    rows = capsys.readouterr().out.splitlines()
    assert rows[0].split() == ["TaskID", "Status"]
    assert {tuple(row.split()) for row in rows[1:]} == {
        ("count_n_chr1", "COMPUTED"),
        ("count_n_chr2", "COMPUTED"),
        ("count_n_chr3", "COMPUTED"),
        ("sum_n", "COMPUTED"),
    }


@pytest.mark.usefixtures("cli_context_provider", "process_pool_enabled")
def test_process_pool_run_writes_the_sum_into_the_summary_record(
    genome_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """A run in a pool of two processes computes the same sum."""
    status_dir = tmp_path / "task-status"

    assert main([
        *grr_args(genome_grr_dir),
        "-j", "2", "--process-pool", "-d", str(status_dir)])

    records = read_records(genome_grr_dir, status_dir)
    assert {rt for rt, _ in records.values()} == {CacheRecordType.COMPUTED}
    assert records["sum_n"][1] == N_COUNT


@pytest.mark.usefixtures("cli_context_provider")
def test_console_script_exits_with_status_zero_after_a_successful_run(
    genome_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console script maps the true value of ``main`` to status 0."""
    monkeypatch.setattr(sys, "argv", [
        "count_n", *grr_args(genome_grr_dir),
        "-j", "1", "-d", str(tmp_path / "task-status")])

    with pytest.raises(SystemExit) as exit_info:
        count_n()

    assert exit_info.value.code == 0
