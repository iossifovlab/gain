# pylint: disable=redefined-outer-name,C0114,C0116
"""``binning_tracks_annotator``: one float attribute per track (ADR 0041).

The annotator resolves a ``binners:`` list the way ``binning_tool`` does
and bins each annotatable as one bin of the grid whose bin size is the
end of the annotatable.

The fragments of ``frags`` on ``chr1``, all of value 1:

- ``(10, 19)``, ``(15, 24)`` and ``(30, 39)``.

Over the three annotatables of the tests:

- ``chr1:15``: one fragment starts there and two cover it.
- ``chr1:12-32``: two fragments start in it; the clipped overlaps are 8,
  10 and 3 bp, 21 bp in all; the coverage peaks at 2 on ``15-19``.
- ``chr1:25-35``: one fragment starts in it; the clipped overlap is 6 bp;
  the coverage peaks at 1.

The position score ``pos`` on ``chr1`` is 1.0 on ``1-14``, 3.0 on
``15-20`` and 5.0 on ``21-40``.  Its mean over ``chr1:12-32`` is
``(3 * 1.0 + 6 * 3.0 + 12 * 5.0) / 21``.

The GRR also holds ``genome``, 60 bases of ``chr1``, which only the
``binning_tool`` run of the cross-check reads.
"""

import pathlib
from typing import Any

import pytest
import yaml
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
)
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.binning.cli import cli as binning_tool
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.h5_reader import read_values
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    a_reference_genome,
)

POSITION = Position("chr1", 15)
REGION = Region("chr1", 12, 32)
CNV = CNVAllele("chr1", 25, 35, Annotatable.Type.LARGE_DUPLICATION)


@pytest.fixture
def grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "grr"


@pytest.fixture
def repo(grr_dir: pathlib.Path) -> GenomicResourceRepo:
    return (
        a_grr()
        .with_resource(
            "genome", a_reference_genome().with_chromosome("chr1", "A" * 60))
        .with_resource(
            "frags",
            a_fragment_score()
            .with_score("cell", "str")
            .with_data("""
                chrom  pos_begin  pos_end  cell
                chr1   10         19       AAA
                chr1   15         24       BBB
                chr1   30         39       AAA
            """),
        )
        .with_resource(
            "pos",
            a_position_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   1          14       1.0
                chr1   15         20       3.0
                chr1   21         40       5.0
            """),
        )
        .build_repo(grr_dir)
    )


def fragment_entry(**entry: Any) -> dict[str, Any]:
    """A constant-group entry over ``frags``."""
    return {"fragment_score_binner": {
        "resource_query": "frags", "group": {"group": "all"}, **entry}}


def tracks_pipeline(
    repo: GenomicResourceRepo, binners: list[dict[str, Any]],
    **parameters: Any,
) -> AnnotationPipeline:
    config = yaml.safe_dump([{"binning_tracks_annotator": {
        **parameters, "binners": binners}}])
    return load_pipeline_from_yaml(config, repo)


def annotate(
    pipeline: AnnotationPipeline, annotatable: Annotatable,
) -> dict[str, Any]:
    with pipeline.open() as opened:
        result: dict[str, Any] = opened.annotate(annotatable)
    return result


@pytest.mark.parametrize("aggregate, expected", [
    ({"mode": "fragment_start"}, [1.0, 2.0, 1.0]),
    ({"mode": "fragment_length", "aggregator": "sum"}, [2.0, 21.0, 6.0]),
    ({"mode": "coverage_profile", "aggregator": "max"}, [2.0, 2.0, 1.0]),
])
def test_a_constant_group_answers_the_hand_counted_value(
    repo: GenomicResourceRepo, aggregate: dict[str, Any],
    expected: list[float],
) -> None:
    pipeline = tracks_pipeline(repo, [fragment_entry(aggregate=aggregate)])

    values = [
        annotate(pipeline, annotatable)["track_0_0"]
        for annotatable in (POSITION, REGION, CNV)]

    assert values == expected


@pytest.mark.parametrize("parameters, expected", [
    ({}, ["track_0_0"]),
    ({"prefix": "atac"}, ["atac_0_0"]),
])
def test_an_attribute_is_named_by_its_prefix_and_track(
    repo: GenomicResourceRepo, parameters: dict[str, Any],
    expected: list[str],
) -> None:
    pipeline = tracks_pipeline(repo, [fragment_entry()], **parameters)

    assert list(annotate(pipeline, REGION)) == expected


def test_a_track_attribute_is_a_float_described_by_its_resources(
    tmp_path: pathlib.Path,
) -> None:
    fragments = a_fragment_score().with_data("""
        chrom  pos_begin  pos_end  score
        chr1   10         19       1
    """)
    repo = (
        a_grr()
        .with_resource("pair/a", fragments)
        .with_resource("pair/b", fragments)
        .build_repo(tmp_path / "pair")
    )
    pipeline = tracks_pipeline(repo, [{"fragment_score_binner": {
        "resource_query": "pair/*", "group": {"group": "all"}}}])

    (attribute,) = pipeline.get_attributes()

    assert attribute.spec is not None
    assert (
        attribute.name, attribute.spec.value_type,
        attribute.spec.description, attribute.spec.supports_aggregation,
    ) == ("track_0_0", "float", "pair/a, pair/b", False)


def test_close_exits_each_binding_that_open_entered(
    repo: GenomicResourceRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    exits: list[FragmentScoreBinding] = []
    original_exit = FragmentScoreBinding.__exit__

    def counting_exit(self: FragmentScoreBinding, *args: Any) -> None:
        exits.append(self)
        original_exit(self, *args)

    monkeypatch.setattr(FragmentScoreBinding, "__exit__", counting_exit)
    pipeline = tracks_pipeline(
        repo, [fragment_entry(), fragment_entry(name="second")])

    annotate(pipeline, REGION)
    annotate(pipeline, REGION)

    assert len(exits) == 4


@pytest.mark.parametrize("aggregator, annotatable, expected", [
    ("mean", Region("chr1", 50, 60), None),
    ("count", Region("chr1", 50, 60), 0.0),
    ("mean", Region("chr2", 10, 20), None),
    ("count", Region("chr2", 10, 20), 0.0),
])
def test_a_region_of_no_fragments_answers_the_empty_value_of_the_fold(
    repo: GenomicResourceRepo, aggregator: str, annotatable: Annotatable,
    expected: float | None,
) -> None:
    pipeline = tracks_pipeline(repo, [fragment_entry(
        aggregate={"mode": "fragment_start", "aggregator": aggregator})])

    assert annotate(pipeline, annotatable)["track_0_0"] == expected


def test_a_position_entry_before_a_fragment_entry(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(repo, [
        {"position_score_binner": {
            "resource_query": "pos", "aggregator": "mean"}},
        fragment_entry(),
    ])

    result = annotate(pipeline, REGION)

    assert result == {
        "track_0_0": pytest.approx((3 * 1.0 + 6 * 3.0 + 12 * 5.0) / 21),
        "track_1_0": 2.0,
    }


def test_a_value_equals_the_binning_tool_value_of_the_same_window(
    repo: GenomicResourceRepo, grr_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    # chr1:13-24 is the second bin of a grid of 12; the annotator bins it
    # as a part of the first bin of a grid of 24.
    binners = [
        {"position_score_binner": {
            "resource_query": "pos", "aggregator": "mean"}},
        fragment_entry(aggregate={
            "mode": "fragment_length", "aggregator": "sum"}),
    ]
    run_definition = tmp_path / "run.yaml"
    run_definition.write_text(yaml.safe_dump({
        "reference_genome": "genome",
        "bins": {"bin_size": 12, "regions": ["chr1:13-24"]},
        "binners": binners,
    }))
    output = tmp_path / "bins.h5"
    binning_tool([
        str(run_definition), "-o", str(output),
        "--grr-directory", str(grr_dir), "-j", "1"])

    result = annotate(
        tracks_pipeline(repo, binners), Region("chr1", 13, 24))

    assert read_values(output).tolist() == [
        [result["track_0_0"], result["track_1_0"]]]


def test_a_local_meta_file_is_refused_before_it_is_read(
    repo: GenomicResourceRepo, tmp_path: pathlib.Path,
) -> None:
    server_file = tmp_path / "server.csv"
    server_file.write_text("first line of a server file\nAAA\n")

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, [
            {"position_score_binner": {
                "resource_query": "pos", "aggregator": "mean"}},
            {"fragment_score_binner": {
                "resource_query": "frags", "group": {},
                "meta": {"file_name": str(server_file)}}},
        ])

    message = str(excinfo.value)
    assert "binners[1]" in message
    assert "file_name" in message
    assert "first line of a server file" not in message
