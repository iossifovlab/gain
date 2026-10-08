# pylint: disable=redefined-outer-name,C0114,C0116
"""The exact-allele fold: one allele's rows reduced to one value a query.

``get_allele_scores_for_allele_agg`` folds exactly the rows
``get_allele_scores_for_allele_rows`` answers -- the rows at
``(chrom, pos, ref, alt)`` -- through the machinery the region fold
uses, and answers the same :class:`AlleleAggregate`.  ``None`` is an
allele no row holds, judged before the filter; rows the filter rejects
in full fold to an empty selection.

Every value-asserting test runs on both the ``.mem`` and the bgzip+tabix
backend: the two walk a point through different code, and the fold must
not care which it sits on.
"""

import logging
import pathlib

import pytest
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_scores import (
    AlleleAggregate,
    AlleleScore,
    build_allele_score_from_resource,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import (
    a_grr,
    an_allele_score,
)


@pytest.fixture(params=[False, True], ids=["mem", "tabix"])
def tabix(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


def _score(
    tmp_path: pathlib.Path, data: str, *, tabix: bool,
    multiplicity: str | None = None,
    resource_id: str = "scores/alleles",
) -> AlleleScore:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_score("id", "str")
        .with_data(data))
    if multiplicity is not None:
        builder = builder.with_allele_multiplicity(multiplicity)
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource(resource_id, builder).build_repo(tmp_path)
    return build_allele_score_from_resource(repo.get_resource(resource_id))


_REPEATED_ROWS = """
    chrom  pos_begin  reference  alternative  freq  id
    1      10         A          C            0.2   ac
    1      10         A          C            0.5   ac2
    1      10         A          G            0.9   ag
    1      16         C          T            0.3   ct
"""


@pytest.fixture
def repeated_alleles(tmp_path: pathlib.Path, tabix: bool) -> AlleleScore:
    """``1:10:A:C`` held by two rows; ``1:10:A:G`` beside it at one row."""
    return _score(
        tmp_path, _REPEATED_ROWS, tabix=tabix, multiplicity="many")


@pytest.mark.parametrize(("aggregator", "expected"), [
    ("max", 0.5),
    ("mean", 0.35),
    ("count", 2),
    ("value_count", {0.2: 1, 0.5: 1}),
])
def test_the_rows_of_a_repeated_allele_fold_together(
    repeated_alleles: AlleleScore, aggregator: str, expected: object,
) -> None:
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C",
            queries=[ScoreAggregationQuery("freq", aggregator)])

    assert aggregate == AlleleAggregate(values=(expected,), allele_keys=None)


@pytest.mark.parametrize(("aggregator", "expected"), [
    ("max", 0.2),
    ("mean", 0.2),
    ("count", 1),
    ("value_count", {0.2: 1}),
])
def test_a_filter_rejecting_one_row_folds_the_other(
    repeated_alleles: AlleleScore, aggregator: str, expected: object,
) -> None:
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C",
            queries=[ScoreAggregationQuery("freq", aggregator)],
            score_filter=score.compile_filter("freq < 0.3"))

    assert aggregate == AlleleAggregate(values=(expected,), allele_keys=None)


@pytest.mark.parametrize(("pos", "ref", "alt"), [
    (10, "A", "T"),
    (12, "A", "C"),
    (200, "A", "C"),
])
def test_an_allele_no_row_holds_answers_none(
    repeated_alleles: AlleleScore, pos: int, ref: str, alt: str,
) -> None:
    """``1:10:A:T`` shares its position with rows differing in alt."""
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", pos, ref, alt, allele_keys=())

    assert aggregate is None


def test_rows_all_filtered_fold_to_an_empty_selection_not_none(
    repeated_alleles: AlleleScore,
) -> None:
    """The same values the region fold gives an all-filtered region.

    Each aggregator's empty-selection answer: ``max`` and ``count`` have
    no row to answer for and give ``None``, ``list`` gives ``[]``.
    """
    queries = [
        ScoreAggregationQuery("freq", "max"),
        ScoreAggregationQuery("id", "list"),
        ScoreAggregationQuery("freq", "count"),
    ]
    with repeated_alleles.open() as score:
        rejecting = score.compile_filter("freq > 2.0")
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C", queries=queries, allele_keys=(),
            score_filter=rejecting)
        region = score.get_allele_scores_in_region_agg(
            "1", 10, 10, queries=queries, allele_keys=(),
            score_filter=rejecting)

    assert aggregate == AlleleAggregate(values=(None, [], None), allele_keys=())
    assert aggregate == region


@pytest.mark.parametrize("aggregator", ["max", "min", "mean"])
def test_a_single_row_allele_folds_to_its_own_values(
    tmp_path: pathlib.Path, tabix: bool, aggregator: str,
) -> None:
    score = _score(tmp_path, _REPEATED_ROWS, tabix=tabix)

    with score.open() as opened:
        aggregate = opened.get_allele_scores_for_allele_agg(
            "1", 16, "C", "T",
            queries=[ScoreAggregationQuery("freq", aggregator)])
        values = opened.get_allele_scores_for_allele(
            "1", 16, "C", "T", scores=["freq"])

    assert aggregate is not None
    assert aggregate.values == values


def test_another_allele_at_the_position_is_not_folded(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    score = _score(tmp_path, """
        chrom  pos_begin  reference  alternative  freq  id
        1      10         G          A            0.1   ga1
        1      10         G          T            0.8   gt1
        1      10         G          A            0.3   ga2
        1      10         G          T            0.9   gt2
    """, tabix=tabix, multiplicity="many")

    with score.open() as opened:
        aggregate = opened.get_allele_scores_for_allele_agg(
            "1", 10, "G", "A", queries=[
                ScoreAggregationQuery("freq", "count"),
                ScoreAggregationQuery("freq", "max"),
                ScoreAggregationQuery("id", "list"),
            ])

    assert aggregate == AlleleAggregate(
        values=(2, 0.3, ["ga1", "ga2"]), allele_keys=None)


def test_a_row_starting_before_pos_and_spanning_it_is_not_folded(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    score = _score(tmp_path, """
        chrom  pos_begin  pos_end  reference  alternative  freq  id
        1      8          12       A          C            0.7   span
        1      10         10       A          C            0.2   ac
    """, tabix=tabix, multiplicity="many")

    with score.open() as opened:
        folded = opened.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C", queries=[
                ScoreAggregationQuery("freq", "count"),
                ScoreAggregationQuery("id", "list"),
            ])
        only_spanned = opened.get_allele_scores_for_allele_agg(
            "1", 11, "A", "C")

    assert folded == AlleleAggregate(values=(1, ["ac"]), allele_keys=None)
    assert only_spanned is None


def test_no_queries_folds_every_score_with_its_own_default(
    repeated_alleles: AlleleScore,
) -> None:
    """``freq`` is a float, folded by ``max``; ``id`` a str, by ``list``."""
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C")

    assert aggregate == AlleleAggregate(
        values=(0.5, ["ac", "ac2"]), allele_keys=None)


def test_a_string_score_configured_with_most_common_folds_to_a_ranked_list(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    """``id`` states ``aggregator: most_common(2)`` in the resource config."""
    builder = (
        an_allele_score()
        .with_score("id", "str")
        .with_aggregator("most_common(2)")
        .with_allele_multiplicity("many")
        .with_data("""
            chrom  pos_begin  reference  alternative  id
            1      10         A          C            rare
            1      10         A          C            often
            1      10         A          C            twice
            1      10         A          C            often
            1      10         A          C            twice
            1      10         A          C            often
            1      10         A          G            other
        """))
    if tabix:
        builder = builder.with_tabix()
    repo = a_grr().with_resource("ids", builder).build_repo(tmp_path)
    allele_score = build_allele_score_from_resource(repo.get_resource("ids"))

    with allele_score.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C")

    assert aggregate == AlleleAggregate(
        values=(["often", "twice"],), allele_keys=None)


@pytest.mark.parametrize(("allele_keys", "expected"), [
    ((), ("1:10:A:C",)),
    (("freq",), ("1:10:A:C:0.2", "1:10:A:C:0.5")),
    (None, None),
])
def test_allele_keys_are_the_alleles_own(
    repeated_alleles: AlleleScore,
    allele_keys: tuple[str, ...] | None,
    expected: tuple[str, ...] | None,
) -> None:
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C", allele_keys=allele_keys)

    assert aggregate is not None
    assert aggregate.allele_keys == expected


def _multiplicity_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records
        if record.levelno >= logging.WARNING
        and "allele_multiplicity" in record.getMessage()
    ]


def test_a_one_resource_folds_its_single_row_without_a_warning(
    tmp_path: pathlib.Path, tabix: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    score = _score(tmp_path, _REPEATED_ROWS, tabix=tabix, multiplicity="one")

    with caplog.at_level("WARNING"), score.open() as opened:
        aggregate = opened.get_allele_scores_for_allele_agg(
            "1", 10, "A", "G")

    assert aggregate == AlleleAggregate(values=(0.9, ["ag"]), allele_keys=None)
    assert not _multiplicity_warnings(caplog)


def test_a_one_resource_with_repeated_rows_folds_them_without_a_warning(
    tmp_path: pathlib.Path, tabix: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mode-blind and multiplicity-blind: the fold answers every row."""
    score = _score(tmp_path, _REPEATED_ROWS, tabix=tabix, multiplicity="one")

    with caplog.at_level("WARNING"), score.open() as opened:
        aggregate = opened.get_allele_scores_for_allele_agg(
            "1", 10, "A", "C",
            queries=[ScoreAggregationQuery("freq", "count")])

    assert aggregate == AlleleAggregate(values=(2,), allele_keys=None)
    assert not _multiplicity_warnings(caplog)


@pytest.mark.parametrize(("aggregator", "expected"), [
    (None, 0.5),
    ("min", 0.2),
])
def test_the_singular_answers_an_aggregate_of_one_value(
    repeated_alleles: AlleleScore, aggregator: str | None, expected: float,
) -> None:
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_score_for_allele_agg(
            "1", 10, "A", "C", score="freq", aggregator=aggregator)

    assert aggregate == AlleleAggregate(values=(expected,), allele_keys=None)


def test_the_singular_answers_none_for_an_allele_no_row_holds(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_score_for_allele_agg(
            "1", 10, "A", "T", score="freq")

    assert aggregate is None


def test_the_singular_needs_no_score_on_a_single_score_resource(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          C            0.2
            1      10         A          C            0.5
        """)
        .with_allele_multiplicity("many"))
    if tabix:
        builder = builder.with_tabix()
    score = build_allele_score_from_resource(builder.build_resource(tmp_path))

    with score.open() as opened:
        aggregate = opened.get_allele_score_for_allele_agg("1", 10, "A", "C")

    assert aggregate == AlleleAggregate(values=(0.5,), allele_keys=None)


def test_the_singular_refuses_no_score_on_a_multi_score_resource(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError, match=r"defines \['freq', 'id'\]"):
        score.get_allele_score_for_allele_agg("1", 10, "A", "C")


# The request checks, each made on an allele no row carries, so a refusal
# cannot hide behind data: both forms of the read refuse alike.
_READS = {
    "plural": lambda opened, chrom, *, score_id, aggregator=None, **kw: (
        opened.get_allele_scores_for_allele_agg(
            chrom, 200, "A", "C",
            queries=[ScoreAggregationQuery(score_id, aggregator)], **kw)),
    "singular": lambda opened, chrom, *, score_id, aggregator=None, **kw: (
        opened.get_allele_score_for_allele_agg(
            chrom, 200, "A", "C", score=score_id, aggregator=aggregator,
            **kw)),
}


@pytest.mark.parametrize("read", sorted(_READS))
def test_an_unknown_score_is_refused_with_the_valid_names(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError,
            match=r"score 'nope' is not defined by resource '[^']*'; "
                  r"it has \['freq', 'id'\]"):
        _READS[read](score, "1", score_id="nope")


@pytest.mark.parametrize("read", sorted(_READS))
def test_a_bad_aggregator_is_refused(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError, match="nope"):
        _READS[read](score, "1", score_id="freq", aggregator="nope")


@pytest.mark.parametrize("read", sorted(_READS))
def test_an_unknown_contig_is_refused(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError, match="not among the available chromosomes"):
        _READS[read](score, "2", score_id="freq")


@pytest.mark.parametrize("read", sorted(_READS))
def test_a_foreign_filter_is_refused(
    repeated_alleles: AlleleScore, tmp_path: pathlib.Path, tabix: bool,
    read: str,
) -> None:
    other = _score(tmp_path / "other", _REPEATED_ROWS, tabix=tabix)

    with repeated_alleles.open() as score, other.open() as other_score:
        foreign = other_score.compile_filter("freq > 0.15")

        with pytest.raises(ScoreFilterError, match="compiled against"):
            _READS[read](score, "1", score_id="freq", score_filter=foreign)


def test_an_unknown_allele_key_score_is_refused(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError,
            match=r"score 'nope' is not defined by resource '[^']*'; "
                  r"it has \['freq', 'id'\]"):
        score.get_allele_scores_for_allele_agg(
            "1", 200, "A", "C", allele_keys=("nope",))


def test_a_query_with_no_aggregator_to_resolve_to_is_refused(
    tmp_path: pathlib.Path, tabix: bool,
) -> None:
    builder = (
        an_allele_score()
        .with_score("flag", "bool")
        .with_data("""
            chrom  pos_begin  reference  alternative  flag
            1      10         A          C            True
        """))
    if tabix:
        builder = builder.with_tabix()
    score = build_allele_score_from_resource(builder.build_resource(tmp_path))

    with score.open() as opened, pytest.raises(
            ValueError,
            match=r"no default aggregator .*; name one on the query"):
        opened.get_allele_scores_for_allele_agg(
            "1", 200, "A", "C", queries=[ScoreAggregationQuery("flag")])
