# pylint: disable=W0621,C0114,C0116,W0212,W0613
import contextlib
import logging
import pathlib

import pytest
from gain.genomic_resources.cli import cli_manage
from gain.genomic_resources.draw_score_histograms import main
from gain.genomic_resources.histogram import NullHistogram
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.testing.builders import (
    a_gene_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)


class UnsupportedResourceBuilder:
    """Realizes a resource whose ``type:`` no implementation is built for.

    Stands in for what a client actually meets in the wild: a GRR written
    by a newer GAIn, or one still declaring a type this client has since
    dropped.  Nothing is mocked -- the type is simply absent from the
    implementation registry, which is exactly the deployed condition.
    """

    def __init__(self, resource_type: str = "some_future_type") -> None:
        self.resource_type = resource_type

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        resource_dir.mkdir(parents=True, exist_ok=True)
        (resource_dir / GR_CONF_FILE_NAME).write_text(
            f"type: {self.resource_type}\n")


def a_position_score_with_one_histogram(score_id: str = "phastCons"):
    """A minimal drawable position score -- one score, one histogram."""
    return (
        a_position_score()
        .with_score(score_id, "float")
        .with_data(f"""
            chrom  pos_begin  pos_end  {score_id}
            1      10         15       0.02
            1      17         19       0.03
        """)
    )


def build_statistics_without_images(
    repo_path: pathlib.Path, resource_id: str,
) -> None:
    """Build a resource's statistics, then drop the plotted images.

    The tool draws histograms from statistics that already exist, so a test
    has to build them first.  Removing the images ``resource-repair`` plots
    along the way leaves any image found afterwards provably the tool's own.

    The images are dropped whether or not the run reported a failure: a
    repository holding an unsupported resource makes ``resource-repair``
    exit non-zero after it has already built this resource's statistics,
    and the caller that tolerates that exit still needs a clean slate.
    """
    try:
        cli_manage([
            "resource-repair", "-R", str(repo_path),
            "-r", resource_id, "-j", "1",
        ])
    finally:
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


def test_draws_the_other_resources_when_one_type_is_unsupported(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a_grr().with_resource(
        "future/thing", UnsupportedResourceBuilder(),
    ).with_resource(
        "scores/pos", a_position_score_with_one_histogram(),
    ).build_repo(tmp_path)
    image = tmp_path / "scores/pos/statistics/histogram_phastCons.png"

    # `resource-repair` builds the repository-wide FTS index too, and
    # already does the right thing with the unsupported resource: reports
    # it, skips it, and exits non-zero because a resource failed.  That
    # exit is not what this test is about -- the statistics of the good
    # resource are built by then, as the drawn image below proves.
    with contextlib.suppress(SystemExit):
        build_statistics_without_images(tmp_path, "scores/pos")
    assert not image.exists()

    monkeypatch.chdir(tmp_path)
    # the skipped resource makes the run exit non-zero, which is asserted
    # separately; what matters here is that the good resource was drawn
    with contextlib.suppress(SystemExit):
        main(["-R", str(tmp_path)])

    assert image.exists()


def test_exits_non_zero_naming_the_resource_it_could_not_draw(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    a_grr().with_resource(
        "future/thing", UnsupportedResourceBuilder(),
    ).with_resource(
        "scores/pos", a_position_score_with_one_histogram(),
    ).build_repo(tmp_path)

    with contextlib.suppress(SystemExit):
        build_statistics_without_images(tmp_path, "scores/pos")

    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        main(["-R", str(tmp_path)])

    assert excinfo.value.code != 0
    assert "future/thing" in caplog.text
    assert "some_future_type" in caplog.text


def test_draws_the_other_resources_when_one_carries_no_scores(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genome sits in every real GRR; it must not stop the sweep."""
    a_grr().with_resource(
        "genome/mock", a_reference_genome(),
    ).with_resource(
        "scores/pos", a_position_score_with_one_histogram(),
    ).build_repo(tmp_path)
    image = tmp_path / "scores/pos/statistics/histogram_phastCons.png"

    build_statistics_without_images(tmp_path, "scores/pos")
    assert not image.exists()

    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.DEBUG):
        main(["-R", str(tmp_path)])

    assert image.exists()
    # having no scores is not a failure -- it is the normal state of a
    # genome, so it must not be reported at failure level or blame the run
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.WARNING
        and "genome/mock" in record.getMessage()
    ]
    assert not [
        record for record in caplog.records if record.exc_info is not None
    ]
    assert "genome/mock" in caplog.text


def test_draws_the_other_resources_when_one_fails_while_drawing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Isolation covers drawing, not only building the implementation."""
    a_grr().with_resource(
        "scores/good", a_position_score_with_one_histogram("good_score"),
    ).with_resource(
        "scores/broken", a_position_score_with_one_histogram("broken_score"),
    ).build_repo(tmp_path)
    good_image = tmp_path / "scores/good/statistics/histogram_good_score.png"

    for resource_id in ("scores/good", "scores/broken"):
        build_statistics_without_images(tmp_path, resource_id)

    # the statistics survived the build but cannot be read back
    (tmp_path / "scores/broken/statistics/histogram_broken_score.json"
     ).write_text("{ this is not json")

    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as excinfo:
        main(["-R", str(tmp_path)])

    assert excinfo.value.code != 0
    assert good_image.exists()
    assert "scores/broken" in caplog.text
