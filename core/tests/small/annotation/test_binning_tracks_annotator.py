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

import html
import pathlib
import re
import textwrap
from collections.abc import Hashable
from typing import Any

import pysam
import pytest
import yaml
from gain.annotation import binning_tracks_annotator
from gain.annotation.annotatable import (
    Annotatable,
    CNVAllele,
    Position,
    Region,
)
from gain.annotation.annotate_doc import cli as annotate_doc
from gain.annotation.annotate_vcf import cli as annotate_vcf
from gain.annotation.annotation_config import AnnotationConfigurationError
from gain.annotation.annotation_factory import load_pipeline_from_yaml
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.binning_tracks_annotator import LocalFilesError
from gain.binning.cli import cli as binning_tool
from gain.binning.fragment_binner import FragmentScoreBinding
from gain.binning.h5_reader import read_values
from gain.binning.run_definition import (
    RunDefinitionError,
    parse_run_definition,
    resolve_binners,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource_id,
)
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import setup_vcf
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.reference_genome_builder import (
    a_reference_genome,
)
from gain.genomic_resources.testing.table_score_builder import (
    a_fragment_score,
    a_position_score,
)
from gain.templates.markdown_support import render_markdown

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


def test_a_track_attribute_is_a_float_described_by_its_track_label(
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
    ) == ("track_0_0", "float", "pair/*:all:sum", False)


PAIR_FRAGMENTS = a_fragment_score().with_data("""
    chrom  pos_begin  pos_end  score
    chr1   10         19       1
""")

UNPOOLED_PAIR = {"fragment_score_binner": {
    "resource_query": "pair/*", "pool": False, "group": {"group": "all"},
    "aggregate": {"mode": "fragment_start", "aggregator": "mean"}}}


@pytest.fixture
def pair_grr_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A GRR of ``pair/a`` and ``pair/b``: one fragment on chr1:10-19."""
    grr_dir = tmp_path / "pair"
    (
        a_grr()
        .with_resource("pair/a", PAIR_FRAGMENTS)
        .with_resource("pair/b", PAIR_FRAGMENTS)
        .build_repo(grr_dir)
    )
    return grr_dir


@pytest.mark.usefixtures("clean_genomic_context")
def test_annotate_vcf_writes_each_track_with_its_label(
    pair_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    # chr1:10 is the start of the fragment; no fragment starts at
    # chr1:15, so the mean over no fragments is missing.
    input_vcf = tmp_path / "in.vcf"
    setup_vcf(input_vcf, textwrap.dedent("""
        ##fileformat=VCFv4.2
        ##contig=<ID=chr1>
        #CHROM POS ID REF ALT QUAL FILTER INFO
        chr1   10  .  A   T   .    .      .
        chr1   15  .  A   T   .    .      .
    """))
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump([{"binning_tracks_annotator": {
        "prefix": "atac", "binners": [UNPOOLED_PAIR]}}]))
    output = tmp_path / "out.vcf"

    annotate_vcf([
        str(input_vcf), str(pipeline), "-o", str(output),
        "-w", str(tmp_path / "work"), "-j", "1",
        "--grr-directory", str(pair_grr_dir)])

    # pylint: disable=no-member
    with pysam.VariantFile(str(output)) as vcf:
        descriptions = {
            key: vcf.header.info[key].description
            for key in ("atac_0_0", "atac_0_1")}
    info_columns = [
        line.split("\t")[7]
        for line in output.read_text().splitlines()
        if not line.startswith("#")]
    assert descriptions == {
        "atac_0_0": "pair/a:all:mean", "atac_0_1": "pair/b:all:mean"}
    assert info_columns == ["atac_0_0=1;atac_0_1=1", "."]


def attribute_documentation(page: str, name: str) -> str:
    """The rendered documentation of the attribute ``name`` of ``page``."""
    match = re.search(
        r'<div class="attribute-name">\s*' + re.escape(name)
        + r'\s*</div>.*?<div class="attribute-description">(.*?)'
        r'<div class="attribute-source">',
        page, re.DOTALL)
    assert match is not None
    return html.unescape(match.group(1))


@pytest.mark.usefixtures("clean_genomic_context")
def test_annotate_doc_shows_the_label_and_the_six_fields_of_a_track(
    pair_grr_dir: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump([{"binning_tracks_annotator": {
        "prefix": "atac", "binners": [UNPOOLED_PAIR]}}]))
    output = tmp_path / "doc.html"

    annotate_doc([
        str(pipeline), "-o", str(output),
        "--grr-directory", str(pair_grr_dir)])

    page = output.read_text()
    documentation = attribute_documentation(page, "atac_0_0")
    assert "<p><code>pair/a:all:mean</code></p>" in documentation
    for field, value in [
            ("binner_type", "fragment_score"),
            ("binner_index", "0"),
            ("track_index", "0"),
            ("resource_ids", "pair/a"),
            ("selector", "all")]:
        assert f"<li><code>{field}</code>: <code>{value}</code></li>" \
            in documentation
    assert "<code>binner_def</code>:" in documentation
    assert textwrap.dedent("""\
        {
          "aggregator": "mean",
          "mode": "fragment_start",
          "name": null,
          "pool": false,
          "resource_query": "pair/*",
          "score_id": null,
          "uncovered_value": null
        }""") in documentation
    second = attribute_documentation(page, "atac_0_1")
    assert "<p><code>pair/b:all:mean</code></p>" in second
    for field, value in [
            ("track_index", "1"),
            ("resource_ids", "pair/b")]:
        assert f"<li><code>{field}</code>: <code>{value}</code></li>" \
            in second
    assert (
        "annotation_infrastructure.html#binning-tracks-annotator" in page)


@pytest.mark.parametrize("label", [
    "atac/sample_a_b:cell_type_x:mean",
    "scores/*/a/*:all:mean",
    "<img src=x onerror=alert(1)>/atac/pbmc:all:mean",
    "pair/a`b:all:mean",
])
def test_the_rendered_documentation_shows_the_label_and_fields_verbatim(
    label: str,
) -> None:
    row: dict[Hashable, Any] = {
        "binner_type": "fragment_score", "binner_index": 0,
        "track_index": 0, "resource_ids": label, "selector": label,
        "binner_def": "{}"}

    rendered = render_markdown(
        binning_tracks_annotator.track_documentation(label, row))

    assert "<img" not in rendered
    assert "<em>" not in rendered
    assert re.findall(r"<code>(.*?)</code>", rendered).count(
        html.escape(label, quote=False)) == 3


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


@pytest.mark.parametrize("prefix", ["1kg", "a-b", "", "x y"])
def test_a_prefix_that_is_not_a_vcf_key_is_refused(
    repo: GenomicResourceRepo, prefix: str,
) -> None:
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, [fragment_entry()], prefix=prefix)

    message = str(excinfo.value)
    assert repr(prefix) in message
    assert "[A-Za-z_][0-9A-Za-z_.]*" in message


@pytest.mark.parametrize("attributes", [
    [{"source": "x"}],
    [{"source": "track_0_0"}],
])
def test_a_non_empty_attributes_list_is_refused(
    repo: GenomicResourceRepo, attributes: list[dict[str, str]],
) -> None:
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, [fragment_entry()], attributes=attributes)

    assert "the binners entries define the attributes" in str(excinfo.value)


def test_an_empty_attributes_list_gives_every_track_attribute(
    repo: GenomicResourceRepo,
) -> None:
    pipeline = tracks_pipeline(repo, [
        {"position_score_binner": {
            "resource_query": "pos", "aggregator": "mean"}},
        fragment_entry(),
    ], attributes=[])

    assert [attribute.name for attribute in pipeline.get_attributes()] == [
        "track_0_0", "track_1_0"]


def test_an_entry_error_has_the_words_of_parse_run_definition(
    repo: GenomicResourceRepo,
) -> None:
    binners = [fragment_entry(), {"no_such_binner": {}}]
    with pytest.raises(RunDefinitionError) as run_error:
        parse_run_definition(
            {"bins": {"bin_size": 10}, "binners": binners}, repo,
            build_reference_genome_from_resource_id("genome", repo))

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, binners)

    assert "binners[1]" in str(run_error.value)
    assert str(run_error.value) in str(excinfo.value)


LOCAL_CELL_GROUPING = {"fragment_score_binner": {
    "resource_query": "frags",
    "group": {
        "cell_score_id": "cell", "cell_meta_column": "barcode",
        "group_meta_column": "cell_type"},
    "meta": {"file_name": "cells.csv"}}}


@pytest.fixture
def readable_cells_csv(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readable ``cells.csv`` in the current directory.

    With it, ``LOCAL_CELL_GROUPING`` resolves into a job whose
    ``local_files`` names the table.
    """
    (tmp_path / "cells.csv").write_text("barcode,cell_type\nAAA,T\nBBB,B\n")
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("readable_cells_csv")
def test_a_job_that_reads_a_local_file_is_refused(
    repo: GenomicResourceRepo,
) -> None:
    # The parser gives a job a local file only for a meta file_name, so
    # the refusal before resolution raises this error first.  The tests
    # below pin the refusal after resolution.
    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, [LOCAL_CELL_GROUPING])

    message = str(excinfo.value)
    assert "binners[0]" in message
    assert "a pipeline has no directory of a run definition" in message
    assert not isinstance(excinfo.value.other_error, RunDefinitionError)


@pytest.mark.usefixtures("readable_cells_csv")
def test_the_refusal_after_resolution_holds_without_the_one_before(
    repo: GenomicResourceRepo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The refusal before resolution covers meta.file_name.  The refusal
    # after resolution covers local_files of every job, whatever key of
    # the entry names the file.  No entry of the parser reaches a local
    # file without meta.file_name, so this test removes the first
    # refusal.  If the first refusal moves, move the patch with it.
    monkeypatch.setattr(
        binning_tracks_annotator, "refuse_local_file_names",
        lambda _binners: None)

    with pytest.raises(AnnotationConfigurationError) as excinfo:
        tracks_pipeline(repo, [LOCAL_CELL_GROUPING])

    assert isinstance(excinfo.value.other_error, LocalFilesError)
    assert str(LocalFilesError("binners[0]")) in str(excinfo.value)


@pytest.mark.usefixtures("readable_cells_csv")
def test_a_resolved_job_with_local_files_is_refused(
    repo: GenomicResourceRepo,
) -> None:
    jobs = resolve_binners([LOCAL_CELL_GROUPING], repo)

    with pytest.raises(LocalFilesError) as excinfo:
        binning_tracks_annotator.refuse_local_files(jobs)

    assert jobs[0].local_files
    assert str(excinfo.value) == str(LocalFilesError("binners[0]"))
