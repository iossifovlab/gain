# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
import textwrap
from typing import Any

import pytest
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    PositionScore,
    build_allele_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.score_def import (
    ScoreValue,
)
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_tabix,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    an_allele_score,
)


@pytest.fixture(scope="module")
def position_score(tmp_path_factory: pytest.TempPathFactory) -> PositionScore:
    root_path = tmp_path_factory.mktemp("position_score")
    setup_directories(
        root_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: position_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  header_mode: none
                  chrom:
                    index: 0
                  pos_begin:
                    index: 1
                  pos_end:
                    index: 2
                scores:
                - id: s1
                  index: 3
                  type: float
                - id: s2
                  index: 4
                  type: float
            """),
        })
    setup_tabix(
        root_path / "data.txt.gz",
        textwrap.dedent("""
        chr1     11        13       1.0    10.0
        chr1     21        23       2.0    na
        chr1     31        33       3.0    30.0
        chr1     41        43       na     40.0
        chr1     51        53       na     na

        chr2     61        73       6.0    60.0
        chr2     71        73       7.0    70.0

        chr3     61        73       6.0    60.0
        chr3     73        73       7.0    70.0
        """).strip(),
        seq_col=0, start_col=1, end_col=2)
    res = build_filesystem_test_resource(root_path)
    score = build_position_score_from_resource(res)
    score.open()
    assert len(score.score_definitions) == 2
    assert "s1" in score.score_definitions
    assert "s2" in score.score_definitions
    return score


@pytest.mark.parametrize("begin,end,scores,expected", [
    (1, 10, None, []),
    (10, 20, ["s1"], [(11, 13, [1.0])]),
    (20, 30, ["s1"], [(21, 23, [2.0])]),
    (30, 40, ["s1"], [(31, 33, [3.0])]),
    (10, 20, ["s2"], [(11, 13, [10.0])]),
    (20, 30, ["s2"], [(21, 23, [None])]),
    (30, 40, ["s2"], [(31, 33, [30.0])]),
    (40, 60, ["s1"], [(41, 43, [None]), (51, 53, [None])]),
    (40, 60, ["s1", "s2"],
     [(41, 43, [None, 40.0]), (51, 53, [None, None])]),
    (40, 60, None,
     [(41, 43, [None, 40.0]), (51, 53, [None, None])]),
    (40, 60, ["s2", "s1"],
     [(41, 43, [40.0, None]), (51, 53, [None, None])]),
    (30, 40, None, [(31, 33, [3.0, 30.0])]),
    (10, 30, None, [(11, 13, [1.0, 10.0]), (21, 23, [2.0, None])]),
    (20, 40, None, [(21, 23, [2.0, None]), (31, 33, [3.0, 30.0])]),
])
def test_position_score_fetch_region(
    position_score: PositionScore,
    begin: int | None,
    end: int | None,
    scores: list[str] | None,
    expected: list[tuple[int, int, list[ScoreValue]]],
) -> None:

    score_lines = list(
        position_score.fetch_region_values("chr1", begin, end, scores=scores))

    assert len(score_lines) == len(expected)
    assert score_lines == expected


@pytest.mark.parametrize("chrom,begin,end", [
    ("chr2", 60, 120),
    ("chr3", 60, 120),
])
def test_position_score_fetch_region_does_not_check_consistency(
    position_score: PositionScore,
    chrom: str,
    begin: int | None,
    end: int | None,
) -> None:
    # chr2 holds two overlapping records and chr3 two touching ones.  The
    # read yields both without a word: since gain#588 the consistency of a
    # position score's records is the statistics scan's question, and the
    # test below asks it of the same two regions.
    assert len(list(position_score.fetch_region_values(chrom, begin, end))) \
        == 2


@pytest.mark.parametrize("chrom,begin,end", [
    ("chr2", 60, 120),
    ("chr3", 60, 120),
])
def test_position_score_scan_consistency(
    position_score: PositionScore,
    chrom: str,
    begin: int | None,
    end: int | None,
) -> None:
    # ... and the scan's door refuses what the read above handed back, which
    # is where that check went rather than what happened to it.
    with pytest.raises(MalformedResourceError,
                       match="multiple values for positions"):
        list(position_score.scan_records(chrom, begin, end))


@pytest.fixture(scope="module")
def np_score(tmp_path_factory: pytest.TempPathFactory) -> AlleleScore:
    root_path = tmp_path_factory.mktemp("np_score")
    setup_directories(
        root_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                    filename: data.txt.gz
                    format: tabix
                    pos_begin:
                      name: pos
                    reference:
                      name: ref
                    alternative:
                      name: alt
                scores:
                    - id: s1
                      type: float
                      name: s1

                    - id: s2
                      type: float
                      name: s2
            """),
        })
    setup_tabix(
        root_path / "data.txt.gz",
        textwrap.dedent("""
            #chrom pos  ref  alt  s1    s2
            chr1   1    A    G    0.1   1.0
            chr1   1    A    C    0.1   1.0
            chr1   1    A    T    0.1   1.0
            chr1   11   A    G    0.2   2.0
            chr1   11   A    C    0.3   na
            chr1   11   A    T    0.4   na
            chr1   21   C    A    na    3.0
            chr1   21   C    G    na    4.0
            chr1   21   C    T    0.5   5.0
            chr1   31   C    A    na    3.0
            chr1   31   C    G    0.4   na
            chr1   31   C    T    na   5.0

            chr1   41   A    G    0.1   1.0
            chr1   41   A    C    0.1   1.0
            chr1   41   A    G    0.1   1.0

            chr3   1    A    G    0.3   3.0
            chr3   1    A    C    0.33  3.3
            chr3   10   A    G    0.3   3.0
            chr3   10   A    C    0.33  3.3
            chr3   10   A    G    0.5   5.0

        """).strip(),
        seq_col=0, start_col=1, end_col=1)
    res = build_filesystem_test_resource(root_path)
    score = build_allele_score_from_resource(res)
    score.open()

    assert len(score.score_definitions) == 2
    assert "s1" in score.score_definitions
    assert "s2" in score.score_definitions

    return score


@pytest.mark.parametrize("begin,end,scores,expected", [
    (5, 10, None, []),
    (1, 10, None, [
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
    ]),
    (11, 20, None, [
        (11, 11, [0.2, 2.0]),
        (11, 11, [0.3, None]),
        (11, 11, [0.4, None]),
    ]),
    (21, 30, None, [
        (21, 21, [None, 3.0]),
        (21, 21, [None, 4.0]),
        (21, 21, [0.5, 5.]),
    ]),
    (31, 40, None, [
        (31, 31, [None, 3.0]),
        (31, 31, [0.4, None]),
        (31, 31, [None, 5.]),
    ]),
    (11, 20, ["s1"], [
        (11, 11, [0.2]),
        (11, 11, [0.3]),
        (11, 11, [0.4]),
    ]),
    (11, 20, ["s2"], [
        (11, 11, [2.0]),
        (11, 11, [None]),
        (11, 11, [None]),
    ]),
    (11, 20, ["s2", "s1"], [
        (11, 11, [2.0, 0.2]),
        (11, 11, [None, 0.3]),
        (11, 11, [None, 0.4]),
    ]),
    (41, 43, ["s1", "s2"], [
        (41, 41, [0.1, 1.0]),
        (41, 41, [0.1, 1.0]),
        (41, 41, [0.1, 1.0]),
    ]),
])
def test_np_score_fetch_regions(
    np_score: AlleleScore,
    begin: int | None,
    end: int | None,
    scores: list[str] | None,
    expected: list[tuple[int, int, list[ScoreValue]]],
) -> None:
    assert np_score is not None

    score_lines = list(
        np_score.fetch_region_values("chr1", begin, end, scores=scores))
    assert len(score_lines) == len(expected)
    assert score_lines == expected


@pytest.fixture(scope="module")
def np_score2(tmp_path_factory: pytest.TempPathFactory) -> AlleleScore:
    root_path = tmp_path_factory.mktemp("np_score")
    setup_directories(
        root_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: np_score
                table:
                    filename: data.txt.gz
                    format: tabix
                    reference:
                      name: ref
                    alternative:
                      name: alt
                scores:
                    - id: s1
                      type: float
                      name: s1

                    - id: s2
                      type: float
                      name: s2
            """),
        })
    setup_tabix(
        root_path / "data.txt.gz",
        textwrap.dedent("""
            #chrom  pos_begin  ref  alt  s1    s2
            chr1    1          A    G    0.1   1.0
            chr1    1          A    C    0.1   1.0
            chr1    1          A    T    0.1   1.0
            chr1    11         A    G    0.2   2.0
            chr1    11         A    C    0.3   na
            chr1    11         A    T    0.4   na
            chr1    21         C    A    na    3.0
            chr1    21         C    G    na    4.0
            chr1    21         C    T    0.5   5.0
            chr1    31         C    A    na    3.0
            chr1    31         C    G    0.4   na
            chr1    31         C    T    na   5.0

            chr1    41         A    G    0.1   1.0
            chr1    41         A    C    0.1   1.0
            chr1    41         A    G    0.1   1.0

            chr1    51         A    G    0.3   3.0
            chr1    51         A    C    0.33  3.3

            chr1    60         A    G    0.3   3.0
            chr1    60         A    C    0.33  3.3
            chr1    60         A    G    0.3   3.0
            chr1    60         A    C    0.33  3.3

        """).strip(),
        seq_col=0, start_col=1, end_col=1, line_skip=1)
    res = build_filesystem_test_resource(root_path)
    score = build_allele_score_from_resource(res)
    score.open()

    assert len(score.score_definitions) == 2
    assert "s1" in score.score_definitions
    assert "s2" in score.score_definitions

    return score


@pytest.mark.parametrize("begin,end,scores,expected", [
    (5, 10, None, []),
    (1, 10, None, [
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
    ]),
    (11, 20, None, [
        (11, 11, [0.2, 2.0]),
        (11, 11, [0.3, None]),
        (11, 11, [0.4, None]),
    ]),
    (21, 30, None, [
        (21, 21, [None, 3.0]),
        (21, 21, [None, 4.0]),
        (21, 21, [0.5, 5.]),
    ]),
    (31, 40, None, [
        (31, 31, [None, 3.0]),
        (31, 31, [0.4, None]),
        (31, 31, [None, 5.]),
    ]),
    (11, 20, ["s1"], [
        (11, 11, [0.2]),
        (11, 11, [0.3]),
        (11, 11, [0.4]),
    ]),
    (11, 20, ["s2"], [
        (11, 11, [2.0]),
        (11, 11, [None]),
        (11, 11, [None]),
    ]),
    (11, 20, ["s2", "s1"], [
        (11, 11, [2.0, 0.2]),
        (11, 11, [None, 0.3]),
        (11, 11, [None, 0.4]),
    ]),
    (41, 41, ["s1", "s2"], [
        (41, 41, [0.1, 1.0]),
        (41, 41, [0.1, 1.0]),
        (41, 41, [0.1, 1.0]),
    ]),
    (51, 51, ["s1", "s2"], [
        (51, 51, [0.3, 3.0]),
        (51, 51, [0.33, 3.3]),
    ]),
    (60, 60, ["s1", "s2"], [
        (60, 60, [0.3, 3.0]),
        (60, 60, [0.33, 3.3]),
        (60, 60, [0.3, 3.0]),
        (60, 60, [0.33, 3.3]),
    ]),
])
def test_np_score2_fetch_regions(
    np_score2: AlleleScore,
    begin: int | None,
    end: int | None,
    scores: list[str] | None,
    expected: list[tuple[int, int, list[ScoreValue]]],
) -> None:
    assert np_score2 is not None

    score_lines = list(
        np_score2.fetch_region_values("chr1", begin, end, scores=scores))
    assert len(score_lines) == len(expected)
    assert score_lines == expected


@pytest.fixture(scope="module")
def allele_score(tmp_path_factory: pytest.TempPathFactory) -> AlleleScore:
    root_path = tmp_path_factory.mktemp("np_score")
    setup_directories(
        root_path, {
            "genomic_resource.yaml": textwrap.dedent("""
                type: allele_score
                table:
                  filename: data.txt.gz
                  format: tabix
                  reference:
                    name: ref
                  alternative:
                    name: alt
                scores:
                - id: s1
                  type: float
                  name: s1

                - id: s2
                  type: float
                  name: s2
            """),
        })
    setup_tabix(
        root_path / "data.txt.gz",
        textwrap.dedent("""
            #chrom  pos_begin  ref  alt  s1    s2
            chr1    1          A    G    0.1   1.0
            chr1    1          A    C    0.1   1.0
            chr1    1          A    AT   0.1   1.0
            chr1    11         A    G    0.2   2.0
            chr1    11         A    C    0.3   na
            chr1    11         A    AT   0.4   na
            chr1    21         C    A    na    3.0
            chr1    21         C    G    na    4.0
            chr1    21         C    T    0.5   5.0
            chr1    31         C    A    na    3.0
            chr1    31         C    G    0.4   na
            chr1    31         C    T    na   5.0

            chr2    1          A    AG   0.1   1.0
            chr2    1          A    G    0.1   1.0
            chr2    1          A    C    0.1   1.0
            chr2    1          A    AG   0.1   1.0

            chr3    1          A    AG   0.3   3.0
            chr3    1          A    C    0.33  3.3
            chr3    10         A    G    0.3   3.0
            chr3    10         A    C    0.33  3.3
            chr3    10         A    AG   0.3   3.0
            chr3    10         A    C    0.33  3.3

        """).strip(),
        seq_col=0, start_col=1, end_col=1, line_skip=1)
    res = build_filesystem_test_resource(root_path)
    score = build_allele_score_from_resource(res)
    score.open()

    assert len(score.score_definitions) == 2
    assert "s1" in score.score_definitions
    assert "s2" in score.score_definitions

    return score


@pytest.mark.parametrize("begin,end,scores,expected", [
    (5, 10, None, []),
    (1, 10, None, [
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
        (1, 1, [0.1, 1.0]),
    ]),
    (11, 20, None, [
        (11, 11, [0.2, 2.0]),
        (11, 11, [0.3, None]),
        (11, 11, [0.4, None]),
    ]),
    (21, 30, None, [
        (21, 21, [None, 3.0]),
        (21, 21, [None, 4.0]),
        (21, 21, [0.5, 5.]),
    ]),
    (31, 40, None, [
        (31, 31, [None, 3.0]),
        (31, 31, [0.4, None]),
        (31, 31, [None, 5.]),
    ]),
    (11, 20, ["s1"], [
        (11, 11, [0.2]),
        (11, 11, [0.3]),
        (11, 11, [0.4]),
    ]),
    (11, 20, ["s2"], [
        (11, 11, [2.0]),
        (11, 11, [None]),
        (11, 11, [None]),
    ]),
    (11, 20, ["s2", "s1"], [
        (11, 11, [2.0, 0.2]),
        (11, 11, [None, 0.3]),
        (11, 11, [None, 0.4]),
    ]),
])
def test_allele_score_fetch_regions(
    allele_score: AlleleScore,
    begin: int | None,
    end: int | None,
    scores: list[str] | None,
    expected: list[tuple[int, int, list[ScoreValue]]],
) -> None:
    assert allele_score is not None

    score_lines = list(
        allele_score.fetch_region_values("chr1", begin, end, scores=scores))
    assert len(score_lines) == len(expected)
    assert score_lines == expected


# ---------------------------------------------------------------------------
# gain#587: a record-ordering refusal is the RESOURCE's fault, and says so
# ---------------------------------------------------------------------------

def _score_repo(
    tmp_path: pathlib.Path, resource_id: str, builder: Any,
) -> GenomicResource:
    repo = a_grr().with_resource(resource_id, builder).build_repo(tmp_path)
    return repo.get_resource(resource_id)


def test_a_position_score_overlap_names_the_resource_locus_and_rule(
    tmp_path: pathlib.Path,
) -> None:
    resource = _score_repo(
        tmp_path, "overlapping",
        a_position_score()
        .with_score("s", "float")
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   1          5        0.1
            chr1   3          8        0.2
        """))
    score = build_position_score_from_resource(resource)
    score.open()

    # Through the scan's door: since gain#588 that is where the refusal
    # lives, and the message it carries is what gain#587 pinned.
    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.scan_records("chr1", 1, 10))

    message = str(excinfo.value)
    assert "<overlapping>" in message
    assert "chr1:3" in message
    assert "at most one record per position" in message


def test_a_position_score_repeat_names_the_resource_locus_and_rule(
    tmp_path: pathlib.Path,
) -> None:
    resource = _score_repo(
        tmp_path, "repeated",
        a_position_score()
        .with_score("s", "float")
        .with_tabix()
        .with_data("""
            chrom  pos_begin  pos_end  s
            chr1   10         10       0.1
            chr1   10         10       0.2
        """))
    score = build_position_score_from_resource(resource)
    score.open()

    # The point read stopped refusing this with gain#588 -- one rule, one
    # path -- so the repeat is named by the scan that now owns the rule.
    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.scan_records("chr1", 1, 20))

    message = str(excinfo.value)
    assert "<repeated>" in message
    assert "chr1:10" in message
    assert "at most one record per position" in message


def test_an_allele_score_going_backwards_names_the_resource_locus_and_rule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The records are handed in rather than read: no backend can deliver a
    # contig's records out of order -- tabix refuses to index an unsorted
    # file and the in-memory backend sorts each contig as it loads it -- so
    # the guard is reachable only from a backend that has yet to exist.
    resource = _score_repo(
        tmp_path, "backwards", an_allele_score())
    score = build_allele_score_from_resource(resource)
    score.open()

    def out_of_order(*_args: Any, **_kwargs: Any) -> Any:
        yield ("chr1", 20, 20, [0.1], ("chr1", 20, 20, "A", "G", ()))
        yield ("chr1", 10, 10, [0.2], ("chr1", 10, 10, "C", "T", ()))

    monkeypatch.setattr(score, "_fetch_region_records", out_of_order)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.fetch_region_values("chr1", 1, 30))

    message = str(excinfo.value)
    assert "<backwards>" in message
    assert "chr1:10" in message
    assert "chr1:20" in message
    assert "must not move backwards" in message
