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
