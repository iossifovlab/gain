# pylint: disable=C0114,C0116
"""Explicit position-column mappings and ``default_annotation`` (gain#318).

The table-score builders normally derive every column fact from the
authored header.  ``with_position_column`` maps ``chrom``, ``pos_begin``
or ``pos_end`` explicitly -- by name or by index -- and that mapping then
feeds the rendered config, the tabix index columns and the header
validation alike.
"""
import pathlib

import pytest
import yaml
from gain.genomic_resources.genomic_scores import AlleleScore, PositionScore
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    ResourceValidationError,
    a_fragment_score,
    a_position_score,
    an_allele_score,
)


def _config(resource: GenomicResource) -> dict:
    config: dict = yaml.safe_load(
        resource.get_file_content("genomic_resource.yaml"))
    return config


@pytest.mark.parametrize("tabix", [False, True])
def test_position_columns_mapped_by_name_read_back(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("chrom", column_name="chromosome")
        .with_position_column("pos_begin", column_name="pos")
        .with_position_column("pos_end", column_name="pos2")
        .with_data("""
            chromosome  pos  pos2  v
            1           10   12    0.5
            1           20   22    0.6
        """)
    )
    if tabix:
        builder = builder.with_tabix()

    resource = builder.build_resource(tmp_path)

    table = _config(resource)["table"]
    assert table["chrom"] == {"column_name": "chromosome"}
    assert table["pos_begin"] == {"column_name": "pos"}
    assert table["pos_end"] == {"column_name": "pos2"}
    score = PositionScore(resource).open()
    assert score.get_scores_at_position("1", 11) == (0.5,)
    assert score.get_scores_at_position("1", 21) == (0.6,)
    assert score.get_scores_at_position("1", 15) == (None,)


@pytest.mark.parametrize("tabix", [False, True])
def test_position_columns_mapped_by_index_in_a_shuffled_order_read_back(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("chrom", column_index=3)
        .with_position_column("pos_begin", column_index=2)
        .with_position_column("pos_end", column_index=1)
        .with_data("""
            v    end  begin  contig
            0.5  12   10     1
            0.6  22   20     1
        """)
    )
    if tabix:
        builder = builder.with_tabix()

    resource = builder.build_resource(tmp_path)

    table = _config(resource)["table"]
    assert table["chrom"] == {"column_index": 3}
    assert table["pos_begin"] == {"column_index": 2}
    assert table["pos_end"] == {"column_index": 1}
    score = PositionScore(resource).open()
    assert score.get_scores_at_position("1", 11) == (0.5,)
    assert score.get_scores_at_position("1", 21) == (0.6,)
    assert score.get_scores_at_position("1", 15) == (None,)


def test_tabix_index_columns_follow_the_explicit_mapping(
    tmp_path: pathlib.Path,
) -> None:
    # Two position-shaped columns, both declared as scores so either can be
    # the one mapped to pos_begin; the two builds differ ONLY in the mapping.
    # A tabix index built on any column but the mapped one would answer the
    # mapped positions with nothing.
    base = (
        a_position_score()
        .with_score("v", "float")
        .with_score("a", "int")
        .with_score("b", "int")
        .with_tabix()
        .with_data("""
            chrom  a   b    v
            1      10  100  0.5
            1      20  200  0.6
        """)
    )

    by_a = PositionScore(
        base.with_position_column("pos_begin", column_name="a")
        .build_resource(tmp_path / "a")).open()
    by_b = PositionScore(
        base.with_position_column("pos_begin", column_name="b")
        .build_resource(tmp_path / "b")).open()

    assert by_a.get_scores_at_position("1", 20, ["v"]) == (0.6,)
    assert by_a.get_scores_at_position("1", 200, ["v"]) == (None,)
    assert by_b.get_scores_at_position("1", 200, ["v"]) == (0.6,)
    assert by_b.get_scores_at_position("1", 20, ["v"]) == (None,)


def test_header_mode_none_refuses_a_name_mapped_position_column() -> None:
    builder = (
        a_position_score()
        .with_score("v", "float", column_index=2)
        .with_position_column("pos_begin", column_name="pos")
        .with_header_mode("none")
        .with_data("""
            chrom  pos  v
            1      10   0.5
        """)
    )

    with pytest.raises(ResourceValidationError, match="'pos_begin'"):
        builder.realize_into(pathlib.Path("/nonexistent"))


@pytest.mark.parametrize("tabix", [False, True])
def test_header_mode_none_renders_the_explicit_index(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        a_position_score()
        .with_score("v", "float", column_index=0)
        .with_position_column("chrom", column_index=1)
        .with_position_column("pos_begin", column_index=2)
        .with_header_mode("none")
        .with_data("""
            v    contig  start
            0.5  1       10
            0.6  1       20
        """)
    )
    if tabix:
        builder = builder.with_tabix()

    resource = builder.build_resource(tmp_path)

    table = _config(resource)["table"]
    assert table["chrom"] == {"column_index": 1}
    assert table["pos_begin"] == {"column_index": 2}
    score = PositionScore(resource).open()
    assert score.get_scores_at_position("1", 10) == (0.5,)
    assert score.get_scores_at_position("1", 20) == (0.6,)


def test_with_position_column_refuses_both_address_modes() -> None:
    with pytest.raises(ResourceValidationError, match="mutually exclusive"):
        a_position_score().with_position_column(
            "chrom", column_name="contig", column_index=0)


def test_with_position_column_refuses_neither_address_mode() -> None:
    with pytest.raises(
            ResourceValidationError, match="pass column_name or column_index"):
        a_position_score().with_position_column("chrom")


def test_with_position_column_refuses_a_non_position_column() -> None:
    # reference/alternative are out of scope: only the position columns map.
    with pytest.raises(ResourceValidationError, match="not a position column"):
        an_allele_score().with_position_column(
            "reference", column_name="ref")


def test_with_position_column_refuses_a_negative_index() -> None:
    with pytest.raises(ResourceValidationError, match="non-negative"):
        a_position_score().with_position_column("chrom", column_index=-1)


def test_a_name_mapping_absent_from_the_header_is_refused() -> None:
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("chrom", column_name="contig")
        .with_data("""
            chromosome  pos_begin  v
            1           10         0.5
        """)
    )

    with pytest.raises(ResourceValidationError, match="'contig'"):
        builder.realize_into(pathlib.Path("/nonexistent"))


def test_an_index_mapping_past_the_header_is_refused() -> None:
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("pos_end", column_index=3)
        .with_data("""
            chrom  pos_begin  v
            1      10         0.5
        """)
    )

    with pytest.raises(ResourceValidationError, match="out of range"):
        builder.realize_into(pathlib.Path("/nonexistent"))


def test_a_name_mapping_retires_the_base_name_from_the_header() -> None:
    # The mapped name REPLACES the base one: a header still carrying the
    # base name next to the mapped one states the column twice.
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("chrom", column_name="contig")
        .with_data("""
            contig  chrom  pos_begin  v
            1       1      10         0.5
        """)
    )

    with pytest.raises(ResourceValidationError, match="undeclared"):
        builder.realize_into(pathlib.Path("/nonexistent"))


def test_score_lines_key_a_name_mapped_column_by_its_mapped_name(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("chrom", column_name="contig")
        .with_position_column("pos_end", column_name="stop")
        .with_score_line(contig="1", pos_begin=10, stop=12, v=0.5)
        .with_score_line(contig="1", pos_begin=20, stop=22, v=0.6)
        .with_tabix()
        .build_resource(tmp_path)
    )

    score = PositionScore(resource).open()
    assert score.get_scores_at_position("1", 11) == (0.5,)
    assert score.get_scores_at_position("1", 21) == (0.6,)


@pytest.mark.parametrize("column, column_index", [
    # the synthesized header is chrom, pos_begin, v: index 0 is chrom
    ("pos_begin", 0),
    # the rows carry no pos_end, so no index can agree with the mapping
    ("pos_end", 2),
])
def test_score_lines_refuse_an_index_mapping_their_header_disagrees_with(
    column: str, column_index: int,
) -> None:
    builder = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column(column, column_index=column_index)
        .with_score_line(chrom="1", pos_begin=10, v=0.5)
    )

    with pytest.raises(
            ResourceValidationError,
            match=f"with_score_line cannot synthesize .*'{column}'"):
        builder.realize_into(pathlib.Path("/nonexistent"))


def test_score_lines_accept_an_index_mapping_their_header_agrees_with(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        a_position_score()
        .with_score("v", "float")
        .with_position_column("pos_begin", column_index=1)
        .with_score_line(chrom="1", pos_begin=10, v=0.5)
        .with_tabix()
        .build_resource(tmp_path)
    )

    assert _config(resource)["table"]["pos_begin"] == {"column_index": 1}
    score = PositionScore(resource).open()
    assert score.get_scores_at_position("1", 10) == (0.5,)


@pytest.mark.parametrize("attributes", [
    [{"source": "v", "name": "v_attr"}, {"source": "w"}],
    [],
])
def test_default_annotation_reads_back_through_the_score(
    tmp_path: pathlib.Path, attributes: list[dict],
) -> None:
    resource = (
        a_position_score()
        .with_score("v", "float")
        .with_score("w", "float")
        .with_default_annotation(attributes)
        .with_data("""
            chrom  pos_begin  v    w
            1      10         0.5  0.6
        """)
        .build_resource(tmp_path)
    )

    assert _config(resource)["default_annotation"] == attributes
    score = PositionScore(resource).open()
    assert score.get_default_annotation_attributes() == attributes


# The configs the builders rendered before the explicit mappings and
# default_annotation existed, captured verbatim: a fixture using neither
# knob must keep rendering exactly these bytes.
_UNCHANGED_CONFIGS = {
    "position_plain": (
        a_position_score(),
        (
            "type: position_score\ntable:\n    filename: data.txt\n"
            "scores:\n- id: score\n  type: float\n  column_name: score\n"
        ),
    ),
    "allele_tabix": (
        an_allele_score().with_tabix(),
        (
            "type: allele_score\ntable:\n    filename: data.txt.gz\n"
            "    format: tabix\n    reference:\n      name: reference\n"
            "    alternative:\n      name: alternative\n"
            "scores:\n- id: score\n  type: float\n  column_name: score\n"
        ),
    ),
    "fragment_header_none": (
        a_fragment_score()
        .with_score("score", "float", column_index=3)
        .with_header_mode("none"),
        (
            "type: fragment_score\ntable:\n    filename: data.txt\n"
            "    header_mode: none\n"
            "    chrom:\n        column_index: 0\n"
            "    pos_begin:\n        column_index: 1\n"
            "    pos_end:\n        column_index: 2\n"
            "scores:\n- id: score\n  type: float\n  column_index: 3\n"
        ),
    ),
    "position_header_list_tabix": (
        a_position_score().with_header_mode("list").with_tabix(),
        (
            "type: position_score\ntable:\n    filename: data.txt.gz\n"
            "    format: tabix\n    header_mode: list\n    header:\n"
            "    - chrom\n    - pos_begin\n    - score\n"
            "scores:\n- id: score\n  type: float\n  column_name: score\n"
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(_UNCHANGED_CONFIGS))
def test_a_builder_without_the_new_knobs_renders_the_same_config(
    tmp_path: pathlib.Path, case: str,
) -> None:
    builder, expected = _UNCHANGED_CONFIGS[case]

    resource = builder.build_resource(tmp_path)

    assert resource.get_file_content("genomic_resource.yaml") == expected


def test_every_table_builder_carries_the_position_mapping(
    tmp_path: pathlib.Path,
) -> None:
    resource = (
        an_allele_score()
        .with_score("freq", "float")
        .with_position_column("chrom", column_name="contig")
        .with_position_column("pos_begin", column_index=1)
        .with_tabix()
        .with_data("""
            contig  at  reference  alternative  freq
            1       10  A          G            0.1
        """)
        .build_resource(tmp_path)
    )

    score = AlleleScore(resource).open()
    assert score.get_allele_scores_for_allele(
        "1", 10, "A", "G", scores=["freq"]) == (0.1,)
