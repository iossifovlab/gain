# pylint: disable=C0116
"""The ``region_size`` contract of a statistics build (gain#1656).

``0`` means "do not split" for every kind that reads it, and a negative
size is refused rather than rewritten.
"""
import pathlib
from typing import Any

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.genomic_scores.chrom_lengths import (
    CHROM_LENGTHS_FILE,
)
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    a_position_score,
    a_reference_genome,
)


def _task_shape(impl: Any, **build_kwargs: Any) -> list[tuple[str, list]]:
    return [
        (desc.task.task_id, desc.args)
        for desc in impl.create_statistics_build_tasks(**build_kwargs)
    ]


def test_genome_region_size_zero_is_one_region_per_chromosome(
    tmp_path: pathlib.Path,
) -> None:
    genome = a_reference_genome() \
        .with_chromosome("chrA", "ACGT" * 8) \
        .with_chromosome("chrB", "ACGT" * 8) \
        .build_resource(tmp_path)
    impl = build_resource_implementation(genome)

    zero = _task_shape(impl, region_size=0)

    assert zero == _task_shape(impl)


def test_genome_refuses_a_negative_region_size(
    tmp_path: pathlib.Path,
) -> None:
    genome = a_reference_genome() \
        .with_chromosome("chrA", "ACGT" * 8) \
        .build_resource(tmp_path)
    impl = build_resource_implementation(genome)

    with pytest.raises(ValueError, match="-1"):
        impl.create_statistics_build_tasks(region_size=-1)


def test_genomic_score_refuses_a_negative_region_size_before_writing(
    tmp_path: pathlib.Path,
) -> None:
    score = a_position_score() \
        .with_score("score", "float") \
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """) \
        .with_tabix() \
        .build_resource(tmp_path)
    impl = build_resource_implementation(score)

    with pytest.raises(ValueError, match="-1"):
        impl.create_statistics_build_tasks(region_size=-1)

    # Refused before the build stores anything in the resource.
    assert not score.file_exists(CHROM_LENGTHS_FILE)


def test_grr_manage_refuses_a_negative_region_size(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli_manage([
            "repo-stats", "-R", str(tmp_path), "--region-size", "-1"])

    assert exit_info.value.code == 2
    assert "--region-size" in capsys.readouterr().err


def test_score_default_region_size_is_one_region_per_contig(
    tmp_path: pathlib.Path,
) -> None:
    score = a_position_score() \
        .with_score("score", "float") \
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
            chr1   20         0.2
            chr2   5          0.3
            chr3   7          0.4
        """) \
        .with_tabix() \
        .build_resource(tmp_path)
    impl = build_resource_implementation(score)

    task_ids = [
        desc.task.task_id
        for desc in impl.create_statistics_build_tasks()
    ]

    # Pinned literally (gain#357): the named default must keep the
    # graph it always built -- one region per contig.
    assert task_ids == [
        "_calculate_min_max_chr1_1_24",
        "_calculate_min_max_chr2_1_6",
        "_calculate_min_max_chr3_1_12",
        "_merge_min_max",
        "_calculate_histogram_chr1_1_24",
        "_calculate_histogram_chr2_1_6",
        "_calculate_histogram_chr3_1_12",
        "_merge_and_save_histograms",
    ]


def test_genome_default_region_size_is_one_region_per_contig(
    tmp_path: pathlib.Path,
) -> None:
    genome = a_reference_genome() \
        .with_chromosome("chrA", "ACGT" * 8) \
        .with_chromosome("chrB", "ACGT" * 5) \
        .build_resource(tmp_path)
    impl = build_resource_implementation(genome)

    task_ids = [
        desc.task.task_id
        for desc in impl.create_statistics_build_tasks()
    ]

    # Each contig whole: chrB (20 bp) does not fit beside chrA (32 bp)
    # in a batch no longer than the longest contig (gain#1788).
    assert task_ids == [
        "_chrom_statistics_batch_chrA",
        "_chrom_statistics_batch_chrB",
        "_global_statistics",
    ]


@pytest.mark.parametrize("subcommand", [
    "repo-stats", "resource-stats",
    "repo-repair", "resource-repair",
    "repo-info", "resource-info",
])
def test_region_size_help_names_the_per_contig_default(
    subcommand: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli_manage([subcommand, "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "one region per contig" in help_text
    assert "0 does not split" in help_text
    assert "--region-size 20000000" in help_text
