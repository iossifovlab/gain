# pylint: disable=redefined-outer-name,C0114,C0116
"""A score annotator's ``region_length_cutoff`` (gain#264).

Every region fold of a ``position_score`` or an ``allele_score`` is
subject to one base-pair limit.  An annotatable longer than the limit
answers ``None`` for every attribute, and the annotator logs one WARNING
that names the length and the cutoff.  The output does not say "too
large": only the log does.  An annotatable of exactly the cutoff length
is folded.  The exact match of a ``VCFAllele`` is not a fold, so the
cutoff never applies to it.
"""

import logging
import pathlib
import textwrap
from typing import Any

import pytest
from gain.annotation.annotatable import Annotatable, Region, VCFAllele
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    an_allele_score,
)

# The logger the score annotators warn through.  Loading a pipeline logs
# warnings of its own (the default ``work_dir``, a table that omits
# ``zero_based``), so the count is taken on this logger only.
_CUTOFF_LOGGER = "gain.annotation.genomic_score_annotator_base"


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource(
            "positions",
            a_position_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  pos_end    s
                chr1   1          2000000    1.5
            """),
        )
        .with_resource(
            "alleles",
            an_allele_score()
            .with_score("freq", "float")
            .with_data("""
                chrom  pos_begin  reference  alternative  freq
                chr1   10         ACG        A            0.5
            """),
        )
        .build_repo(tmp_path)
    )


def _annotate(
    repo: GenomicResourceRepo,
    annotator: str,
    annotatable: Annotatable,
    **parameters: Any,
) -> dict[str, Any]:
    resource_id, source = {
        "position_score": ("positions", "s"),
        "allele_score": ("alleles", "freq"),
    }[annotator]
    extra = "".join(
        f"\n            {key}: {value}" for key, value in parameters.items())
    pipeline = load_pipeline_from_yaml(textwrap.dedent(f"""
        - {annotator}:
            resource_id: {resource_id}{extra}
            attributes:
            - source: {source}
              name: value
              aggregator: max
        """), repo)
    with pipeline.open() as work_pipeline:
        return work_pipeline.annotate(annotatable)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == _CUTOFF_LOGGER and r.levelno == logging.WARNING]


def test_a_region_longer_than_the_cutoff_answers_none_and_warns(
    repo: GenomicResourceRepo, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CUTOFF_LOGGER):
        result = _annotate(
            repo, "position_score", Region("chr1", 1, 11),
            region_length_cutoff=10)

    assert result == {"value": None}
    [record] = _warnings(caplog)
    assert "region length 11 is longer than cutoff 10" \
        in record.getMessage()


def test_a_region_of_exactly_the_cutoff_length_is_folded_silently(
    repo: GenomicResourceRepo, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CUTOFF_LOGGER):
        result = _annotate(
            repo, "position_score", Region("chr1", 1, 10),
            region_length_cutoff=10)

    assert result == {"value": 1.5}
    assert _warnings(caplog) == []


def test_the_default_cutoff_folds_a_one_megabase_region(
    repo: GenomicResourceRepo,
) -> None:
    result = _annotate(
        repo, "position_score", Region("chr1", 1, 1_000_000))

    assert result == {"value": 1.5}


def test_region_mode_folds_a_vcf_allele_subject_to_the_cutoff(
    repo: GenomicResourceRepo, caplog: pytest.LogCaptureFixture,
) -> None:
    allele = VCFAllele("chr1", 10, "ACG", "A")

    with caplog.at_level(logging.WARNING, logger=_CUTOFF_LOGGER):
        result = _annotate(
            repo, "allele_score", allele,
            mode="region", region_length_cutoff=2)

    assert result == {"value": None}
    [record] = _warnings(caplog)
    assert f"region length {len(allele)} is longer than cutoff 2" \
        in record.getMessage()


def test_an_exact_match_is_not_subject_to_the_cutoff(
    repo: GenomicResourceRepo, caplog: pytest.LogCaptureFixture,
) -> None:
    allele = VCFAllele("chr1", 10, "ACG", "A")

    with caplog.at_level(logging.WARNING, logger=_CUTOFF_LOGGER):
        result = _annotate(
            repo, "allele_score", allele, region_length_cutoff=1)

    assert result == {"value": 0.5}
    assert _warnings(caplog) == []
