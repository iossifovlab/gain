# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.draw_score_histograms import main
from gain.genomic_resources.histogram import NullHistogram
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)


def build_statistics_without_images(
    repo_path: pathlib.Path, resource_id: str,
) -> None:
    """Build a resource's statistics, then drop the plotted images.

    The tool draws histograms from statistics that already exist, so a test
    has to build them first.  Removing the images ``resource-repair`` plots
    along the way leaves any image found afterwards provably the tool's own.
    """
    cli_manage([
        "resource-repair", "-R", str(repo_path), "-r", resource_id, "-j", "1",
    ])
    for image in (repo_path / resource_id / "statistics").glob("*.png"):
        image.unlink()


def test_draws_position_score_histogram(tmp_path: pathlib.Path) -> None:
    a_grr().with_resource(
        "scores/pos",
        a_position_score()
        .with_score("phastCons100way", "float")
        .with_histogram({"type": "number", "number_of_bins": 100})
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  phastCons100way
            1      10         15       0.02
            1      17         19       0.03
            1      22         25       0.04
            2      5          80       0.01
            2      81         90       0.02
        """),
    ).build_repo(tmp_path)
    image = tmp_path / "scores/pos/statistics/histogram_phastCons100way.png"

    build_statistics_without_images(tmp_path, "scores/pos")
    assert not image.exists()

    main(["-R", str(tmp_path), "-r", "scores/pos"])

    assert image.exists()


def test_draws_gene_score_histogram(tmp_path: pathlib.Path) -> None:
    a_grr().with_resource(
        "genes/impact",
        a_gene_score()
        .with_score("gene_impact", "float")
        .with_data("""
            gene   gene_impact
            G1     0.1
            G2     0.2
            G3     0.3
            G4     0.4
        """),
    ).build_repo(tmp_path)
    image = tmp_path / "genes/impact/statistics/histogram_gene_impact.png"

    build_statistics_without_images(tmp_path, "genes/impact")
    assert not image.exists()

    main(["-R", str(tmp_path), "-r", "genes/impact"])

    assert image.exists()


def test_draws_categorical_histogram(tmp_path: pathlib.Path) -> None:
    a_grr().with_resource(
        "scores/effect",
        a_position_score()
        .with_score("effect", "str")
        .with_histogram({"type": "categorical"})
        .with_data("""
            chrom  pos_begin  pos_end  effect
            1      10         15       benign
            1      17         19       pathogenic
            1      22         25       benign
        """),
    ).build_repo(tmp_path)
    image = tmp_path / "scores/effect/statistics/histogram_effect.png"

    build_statistics_without_images(tmp_path, "scores/effect")
    assert not image.exists()

    main(["-R", str(tmp_path), "-r", "scores/effect"])

    assert image.exists()


def test_skips_score_with_null_histogram(tmp_path: pathlib.Path) -> None:
    repo = a_grr().with_resource(
        "scores/two",
        a_position_score()
        .with_score("phastCons", "float")
        .with_score("raw", "float")
        .with_histogram(
            {"type": "null", "reason": "not interesting"}, score_id="raw")
        .with_data("""
            chrom  pos_begin  pos_end  phastCons  raw
            1      10         15       0.02       1.02
            1      17         19       0.03       1.03
            1      22         25       0.04       1.04
        """),
    ).build_repo(tmp_path)
    statistics = tmp_path / "scores/two/statistics"

    # both scores are on the loop the tool walks; only "raw" is null
    score = build_resource_implementation(
        repo.get_resource("scores/two")).score
    assert score.get_all_scores() == ["phastCons", "raw"]
    assert isinstance(score.get_score_histogram("raw"), NullHistogram)

    build_statistics_without_images(tmp_path, "scores/two")

    main(["-R", str(tmp_path), "-r", "scores/two"])

    assert (statistics / "histogram_phastCons.png").exists()
    assert not (statistics / "histogram_raw.png").exists()


def test_draws_every_resource_when_none_selected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a_grr().with_resource(
        "scores/pos",
        a_position_score()
        .with_score("phastCons", "float")
        .with_data("""
            chrom  pos_begin  pos_end  phastCons
            1      10         15       0.02
            1      17         19       0.03
        """),
    ).with_resource(
        "genes/impact",
        a_gene_score()
        .with_score("gene_impact", "float")
        .with_data("""
            gene   gene_impact
            G1     0.1
            G2     0.2
            G3     0.3
        """),
    ).build_repo(tmp_path)
    images = [
        tmp_path / "scores/pos/statistics/histogram_phastCons.png",
        tmp_path / "genes/impact/statistics/histogram_gene_impact.png",
    ]

    for resource_id in ("scores/pos", "genes/impact"):
        build_statistics_without_images(tmp_path, resource_id)
    assert not any(image.exists() for image in images)

    # with no resource selected the tool enumerates resources from the
    # working directory, so it has to be run from inside the repository
    monkeypatch.chdir(tmp_path)
    main(["-R", str(tmp_path)])

    assert all(image.exists() for image in images)


def test_reports_a_resource_that_carries_no_scores(
    tmp_path: pathlib.Path,
) -> None:
    a_grr().with_resource(
        "genome/mock", a_reference_genome(),
    ).build_repo(tmp_path)

    with pytest.raises(TypeError) as excinfo:
        main(["-R", str(tmp_path), "-r", "genome/mock"])

    message = str(excinfo.value)
    assert "genome/mock" in message
    assert "genome" in message
    assert "score" in message


def test_exits_when_selected_resource_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    a_grr().with_resource(
        "scores/pos",
        a_position_score()
        .with_score("phastCons", "float")
        .with_data("""
            chrom  pos_begin  pos_end  phastCons
            1      10         15       0.02
        """),
    ).build_repo(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(["-R", str(tmp_path), "-r", "scores/no-such-resource"])

    assert excinfo.value.code != 0


def test_exits_when_repository_is_missing(tmp_path: pathlib.Path) -> None:
    not_a_repository = tmp_path / "not-a-repository"
    not_a_repository.mkdir()

    with pytest.raises(SystemExit) as excinfo:
        main(["-R", str(not_a_repository), "-r", "scores/pos"])

    assert excinfo.value.code != 0
