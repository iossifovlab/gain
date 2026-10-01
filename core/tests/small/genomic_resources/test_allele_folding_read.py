# pylint: disable=redefined-outer-name,C0114,C0116
"""The allele folding read: ``AlleleScore`` reduces its own region (#1163).

``get_allele_scores_in_region_agg`` walks a region's allele records ONCE,
feeds each record's values to a fresh aggregator per query and drops the
record -- and, on request, builds the allele keys off that same walk.
Nothing is held per record, which is the memory win gain#834 measured
against the annotator's materialised list.

Two things this read answers that the fragment kind's folding read does
not, both pinned here:

- ``None`` for a region no record overlaps -- absent data -- kept apart
  from the aggregate an empty SELECTION answers, where records were there
  and the filter rejected them all (D3 of the design);
- the allele keys, ``chrom:pos[:ref:alt][:v1,v2]``, de-duplicated in
  first-seen order (D1, D2).
"""

import pathlib
import tracemalloc
from collections.abc import Iterator
from typing import Any

import pytest
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    AlleleAggregate,
    AlleleEntry,
    AlleleScore,
    build_allele_score_from_resource,
)
from gain.genomic_resources.score_filter import ScoreFilterError
from gain.genomic_resources.testing.builders import an_allele_score


@pytest.fixture
def alleles(tmp_path: pathlib.Path) -> AlleleScore:
    """Two alleles at position 10 and a third at 16; nothing past 16.

    ``freq`` is a float, whose default aggregator is ``max``; ``id`` is a
    string, whose default is ``list``.  The rows are in the order every
    backend serves them -- by position, then ref/alt -- so what a test
    here pins about order is the READ's, not one table's.
    """
    return build_allele_score_from_resource(
        an_allele_score()
        .with_score("freq", "float")
        .with_score("id", "str")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq  id
            1      10         A          C            0.2   ac
            1      10         A          G            0.1   ag
            1      16         C          T            0.3   ct
        """)
        .build_resource(tmp_path))


def test_no_queries_means_every_score_with_its_own_default(
    alleles: AlleleScore,
) -> None:
    """``queries=None`` reduces every score the resource defines, in order.

    Each by its own default aggregator, which differ by value type: the
    float takes ``max`` and the string takes ``list``.  No keys were
    asked for, so none are built.
    """
    with alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg("1", 10, 16)

    assert aggregate == AlleleAggregate(
        values=(0.3, ["ac", "ag", "ct"]), allele_keys=None)


def test_one_source_asked_twice_answers_twice(alleles: AlleleScore) -> None:
    """``values`` is parallel to the QUERIES, not keyed by score id.

    A source exposed as both a min and a max is the case a mapping keyed
    by score id would silently drop; one fetch serves both, and each query
    keeps its own accumulator over the same column.
    """
    with alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16,
            queries=[
                ScoreAggregationQuery("freq", "min"),
                ScoreAggregationQuery("freq", "max"),
            ])

    assert aggregate == AlleleAggregate(
        values=(0.1, 0.3), allele_keys=None)


def test_a_region_no_allele_overlaps_reads_as_absent(
    alleles: AlleleScore,
) -> None:
    """``None`` is absent data, as :meth:`fetch_allele_records` answers it.

    A contig the resource HAS, so what is pinned is the empty region and
    not an unknown chromosome.  Keys were asked for and are not built
    either: there is no walk to build them off.
    """
    with alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 200, 300, allele_keys=())

    assert aggregate is None


def test_a_filter_rejecting_every_allele_answers_an_empty_selection(
    alleles: AlleleScore,
) -> None:
    """Records were there, so this is an aggregate, not ``None``.

    Each aggregator answers for an empty selection -- ``max`` has nothing
    to answer and gives ``None``, ``list`` gives ``[]`` -- and the keys,
    asked for, are ``()``.  The distinction gain#820 drew for
    :meth:`fetch_allele_records` survives the move onto a fold.
    """
    with alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16,
            allele_keys=(),
            score_filter=score.compile_filter("freq > 2.0"))

    assert aggregate == AlleleAggregate(
        values=(None, []), allele_keys=())


def test_a_foreign_filter_is_refused_on_an_empty_region_too(
    alleles: AlleleScore, tmp_path: pathlib.Path,
) -> None:
    """Ownership is checked BEFORE the absence peek.

    A foreign filter is a programming error, so it must not be refused for
    a region holding records and accepted for one holding none.  The
    region here holds none, which is what makes the ordering observable.
    """
    other = build_allele_score_from_resource(
        an_allele_score()
        .with_score("padding", "str")
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  padding  freq
            1      10         A          G            x        0.9
        """)
        .build_resource(tmp_path / "other"))

    with alleles.open() as score, other.open() as other_score:
        foreign = score.compile_filter("freq > 0.15")

        with pytest.raises(ScoreFilterError, match="compiled against"):
            other_score.get_allele_scores_in_region_agg(
                "1", 200, 300, score_filter=foreign)


def test_an_unknown_contig_is_refused_from_the_call(
    alleles: AlleleScore,
) -> None:
    """A contig that does not exist is not a region holding nothing.

    Answering ``None`` would make a caller's typo indistinguishable from
    real absent data, which is the failure every eager guard on the allele
    reads exists to prevent.
    """
    with alleles.open() as score, pytest.raises(
            ValueError, match="not among the available chromosomes"):
        score.get_allele_scores_in_region_agg("2", 10, 16)


def test_bare_allele_keys_come_in_first_seen_order(
    alleles: AlleleScore,
) -> None:
    """``allele_keys=()`` asks for ``chrom:pos:ref:alt`` with no suffix.

    Compared as a TUPLE, deliberately: the order is the walk's -- the
    file's own genomic order -- and is part of what the read promises
    (D2), where the annotator's set used to promise nothing.
    """
    with alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16, allele_keys=())

    assert aggregate is not None
    assert aggregate.allele_keys == ("1:10:A:C", "1:10:A:G", "1:16:C:T")


@pytest.fixture
def repeated_alleles(tmp_path: pathlib.Path) -> AlleleScore:
    """One ``(chrom, pos, ref, alt)`` published twice, differing in ``freq``.

    Normal data in several GRR resources (see
    ``.out-of-scope/duplicate-allele-keys.md``), which is why the keys
    de-duplicate rather than refuse.
    """
    return build_allele_score_from_resource(
        an_allele_score()
        .with_score("freq", "float")
        .with_score("id", "str")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq  id
            1      10         A          C            0.2   ac
            1      10         A          C            0.5   ac2
            1      16         C          T            0.3   ct
        """)
        .build_resource(tmp_path))


def test_repeated_alleles_collapse_to_one_key(
    repeated_alleles: AlleleScore,
) -> None:
    """De-duplicated in first-seen order: ``dict.fromkeys`` over the walk.

    The values are still folded from every record -- the collapse is of
    the KEYS alone, so ``max`` sees both ``0.2`` and ``0.5``.
    """
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16,
            queries=[ScoreAggregationQuery("freq")], allele_keys=())

    assert aggregate == AlleleAggregate(
        values=(0.5,), allele_keys=("1:10:A:C", "1:16:C:T"))


def test_a_differing_suffix_keeps_both_keys(
    repeated_alleles: AlleleScore,
) -> None:
    """The suffix is part of the key's identity, exactly as today.

    Two records sharing ``(chrom, pos, ref, alt)`` but differing in a
    suffixed score remain two keys; the ids are suffixed in the order
    asked, joined with ``,``, and formatted as the annotation output
    formats a value -- a float to three significant digits.
    """
    with repeated_alleles.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16, allele_keys=("freq", "id"))

    assert aggregate is not None
    assert aggregate.allele_keys == (
        "1:10:A:C:0.2,ac", "1:10:A:C:0.5,ac2", "1:16:C:T:0.3,ct")


def test_a_missing_ref_or_alt_leaves_the_key_at_chrom_pos(
    tmp_path: pathlib.Path,
) -> None:
    """``chrom:pos`` when either nucleotide is absent -- today's rule, kept.

    A table declaring only ``alternative`` is a legal allele score; its
    records carry ``None`` for the reference, and a key of
    ``1:10:None:C`` would name an allele that does not exist.
    """
    only_alt = build_allele_score_from_resource(
        an_allele_score()
        .without_key_columns("reference")
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  alternative  freq
            1      10         C            0.2
            1      16         T            0.3
        """)
        .build_resource(tmp_path))

    with only_alt.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16, allele_keys=("freq",))

    assert aggregate is not None
    assert aggregate.allele_keys == ("1:10:0.2", "1:16:0.3")


def test_a_false_bool_suffix_is_not_a_missing_one(
    tmp_path: pathlib.Path,
) -> None:
    """A suffixed flag spells ``yes``, ``no``, or nothing (gain#1222).

    The suffix is part of the key's identity, so a false flag and a
    missing flag must be two keys.  They were not: ``False`` rendered as
    the empty string, so ``1:12:A:C:`` named both the false record and
    the one with no value.
    """
    flagged = build_allele_score_from_resource(
        an_allele_score()
        .with_score("flag", "bool")
        .with_na_values(".")
        .with_data("""
            chrom  pos_begin  reference  alternative  flag
            1      10         A          C            True
            1      12         A          C            False
            1      14         A          C            .
        """)
        .build_resource(tmp_path))

    with flagged.open() as score:
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 14,
            queries=[ScoreAggregationQuery("flag", "mode")],
            allele_keys=("flag",))

    assert aggregate is not None
    assert aggregate.allele_keys == (
        "1:10:A:C:yes", "1:12:A:C:no", "1:14:A:C:")


def test_an_unknown_allele_key_score_is_refused_from_the_call(
    alleles: AlleleScore,
) -> None:
    """Resolved up front, with the valid names -- not a per-record KeyError.

    Earlier than the annotator's old path refused it, and on an EMPTY
    region too, so the typo cannot hide behind absent data.
    """
    with alleles.open() as score, pytest.raises(
            ValueError,
            match=r"score 'nope' is not defined by resource '[^']*'; "
                  r"it has \['freq', 'id'\]"):
        score.get_allele_scores_in_region_agg(
            "1", 200, 300, allele_keys=("nope",))


def test_an_unknown_score_is_refused_from_the_call(
    alleles: AlleleScore,
) -> None:
    """The REQUEST is checked when the read is called, with the valid names."""
    with alleles.open() as score, pytest.raises(
            ValueError, match=r"not defined by resource .*\['freq', 'id'\]"):
        score.get_allele_scores_in_region_agg(
            "1", 10, 16, queries=[ScoreAggregationQuery("nope")])


# ---------------------------------------------------------------------------
# The in-memory backend as oracle: the same rows served as ``.mem`` and as
# bgzip+tabix must answer the same aggregate, keys included.  The two
# backends walk a region through different code (a sorted list against an
# indexed seek plus a line buffer), and the fold is blind to which it sits
# on -- which is exactly what this pins.
# ---------------------------------------------------------------------------

_ORACLE_ROWS = """
    chrom  pos_begin  reference  alternative  freq  id   flag
    1      10         A          C            0.2   ac   True
    1      10         A          C            0.5   ac2  False
    1      10         A          G            0.1   ag   True
    1      16         C          T            0.3   ct   False
    1      16         C          T            0.3   ct   False
    1      40         G          A            0.9   ga   True
"""


def _oracle_score(tmp_path: pathlib.Path, *, tabix: bool) -> AlleleScore:
    builder = (
        an_allele_score()
        .with_score("freq", "float")
        .with_score("id", "str")
        .with_score("flag", "bool")
        .with_data(_ORACLE_ROWS))
    if tabix:
        builder = builder.with_tabix()
    return build_allele_score_from_resource(builder.build_resource(tmp_path))


@pytest.mark.parametrize("aggregator", [
    "max", "min", "mean", "median", "count", "mode", "list",
    "value_count", "concatenate", "join(,)", "bool",
])
def test_mem_and_tabix_answer_the_same_aggregate(
    tmp_path: pathlib.Path, aggregator: str,
) -> None:
    """Every registered aggregator, the keys asked for beside it.

    ``freq`` is asked once with the aggregator under test and once with
    its default, so the second column also pins that one score asked
    twice folds to two values on both backends.  ``value_count`` and
    ``mode`` see the repeated ``0.3``; ``mean`` and ``median`` see all
    five records over ``[10, 16]``.
    """
    queries = [
        ScoreAggregationQuery("freq", aggregator),
        ScoreAggregationQuery("freq"),
    ]
    answers = []
    for tabix in (False, True):
        score = _oracle_score(tmp_path / f"tabix-{tabix}", tabix=tabix)
        with score.open() as opened:
            answers.append(opened.get_allele_scores_in_region_agg(
                "1", 10, 16, queries=queries, allele_keys=("id", "freq")))

    mem, indexed = answers
    assert mem == indexed
    assert mem is not None
    assert mem.values[1] == 0.5
    assert mem.allele_keys == (
        "1:10:A:C:ac,0.2", "1:10:A:C:ac2,0.5", "1:10:A:G:ag,0.1",
        "1:16:C:T:ct,0.3")


def test_mem_and_tabix_answer_the_same_defaults_and_absence(
    tmp_path: pathlib.Path,
) -> None:
    """The string and float defaults on both backends, and ``None`` on both.

    Asked by explicit query rather than ``queries=None``: ``flag`` is a
    ``bool``, whose default aggregator is ``None``, so ``None`` -- every
    score with its own default -- is refused on this resource, which
    ``test_a_none_request_list_is_refused_when_a_score_has_no_default``
    pins.
    """
    queries = [ScoreAggregationQuery("freq"), ScoreAggregationQuery("id")]
    answers = []
    for tabix in (False, True):
        score = _oracle_score(tmp_path / f"tabix-{tabix}", tabix=tabix)
        with score.open() as opened:
            answers.append((
                opened.get_allele_scores_in_region_agg(
                    "1", 10, 16, queries=queries, allele_keys=()),
                opened.get_allele_scores_in_region_agg(
                    "1", 17, 39, queries=queries, allele_keys=()),
            ))

    assert answers[0] == answers[1]
    assert answers[0] == (
        AlleleAggregate(
            values=(0.5, ["ac", "ac2", "ag", "ct", "ct"]),
            allele_keys=("1:10:A:C", "1:10:A:G", "1:16:C:T")),
        None,
    )


def test_a_query_with_no_aggregator_to_resolve_to_is_refused(
    tmp_path: pathlib.Path,
) -> None:
    """``bool`` has no default; asking for it bare is refused from the call.

    With the query surface's own remedy, which is what the annotator
    surfaces at pipeline load (D6).
    """
    score = _oracle_score(tmp_path, tabix=False)
    with score.open() as opened, pytest.raises(
            ValueError,
            match=r"no default aggregator .*; name one on the query"):
        opened.get_allele_scores_in_region_agg(
            "1", 10, 16, queries=[ScoreAggregationQuery("flag")])


def test_a_none_request_list_is_refused_when_a_score_has_no_default(
    tmp_path: pathlib.Path,
) -> None:
    """``queries=None`` means every score with its own default -- and one has
    none, so the whole request is refused, naming that score.

    Refused from the call, on an EMPTY region too, so a resource carrying
    a ``bool`` score cannot be reduced by default anywhere.
    """
    score = _oracle_score(tmp_path, tabix=False)
    with score.open() as opened, pytest.raises(
            ValueError, match=r"score 'flag' .* no default aggregator"):
        opened.get_allele_scores_in_region_agg("1", 200, 300)


# ---------------------------------------------------------------------------
# Streaming: the read pulls records as the fold consumes them and holds
# none.  The whole reason this read exists (gain#834).
# ---------------------------------------------------------------------------


def _score_of_many_alleles(
    tmp_path: pathlib.Path, count: int,
) -> AlleleScore:
    """``count`` alleles, one per position from 1, all inside ``[1, count]``."""
    rows = "\n".join(
        f"1 {i + 1} A C {float(i % 7)}" for i in range(count))
    return build_allele_score_from_resource(
        an_allele_score()
        .with_score("v", "float")
        .with_data(f"chrom pos_begin reference alternative v\n{rows}")
        .build_resource(tmp_path))


def test_the_read_consumes_every_record_through_the_fold(
    alleles: AlleleScore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A counting generator under the read: every record is pulled ONCE.

    One walk serves the values and the keys alike -- a second pass for the
    keys, or a peek that re-read the first record, would pull more than
    the region holds.
    """
    with alleles.open() as score:
        pulled: list[Record] = []
        real_fetch_records = score.fetch_records

        def counting_fetch_records(
            *args: Any, **kwargs: Any,
        ) -> Iterator[Record]:
            for record in real_fetch_records(*args, **kwargs):
                pulled.append(record)
                yield record

        monkeypatch.setattr(score, "fetch_records", counting_fetch_records)
        aggregate = score.get_allele_scores_in_region_agg(
            "1", 10, 16, allele_keys=("id",))

    assert aggregate == AlleleAggregate(
        values=(0.3, ["ac", "ag", "ct"]),
        allele_keys=("1:10:A:C:ac", "1:10:A:G:ag", "1:16:C:T:ct"))
    assert len(pulled) == 3


def _peak_bytes_reading(score: AlleleScore, end: int) -> int:
    """Peak bytes allocated by ONE folding read over ``[1, end]``.

    The score is opened and read once before measuring, so the peak is a
    steady-state read's rather than the table's one-off load.
    """
    with score.open() as opened:
        opened.get_allele_scores_in_region_agg(
            "1", 1, end, queries=[ScoreAggregationQuery("v", "max")])
        tracemalloc.start()
        try:
            opened.get_allele_scores_in_region_agg(
                "1", 1, end, queries=[ScoreAggregationQuery("v", "max")])
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()


def test_peak_memory_does_not_grow_with_the_number_of_alleles(
    tmp_path: pathlib.Path,
) -> None:
    """Records are released as they are folded, so ten times the alleles
    costs about the same peak.

    ``max`` is deliberate: an aggregator that KEEPS what it is given --
    ``list``, the ``str`` default -- still grows, and that is the
    aggregator's property rather than the read's.  What the read removes
    is the materialised record list ``fetch_allele_records`` hands back,
    which is linear in the region's alleles whatever the aggregator.

    The bound is loose on purpose: linear growth is ~10x here, and the
    assertion fails anything above 3x.
    """
    small = _peak_bytes_reading(
        _score_of_many_alleles(tmp_path / "small", 200), 200)
    large = _peak_bytes_reading(
        _score_of_many_alleles(tmp_path / "large", 2000), 2000)

    assert large < 3 * small, (
        f"peak grew from {small} to {large} bytes for 10x the alleles")


# ---------------------------------------------------------------------------
# Interface B of the allele plane (gain#1751): the UNREDUCED reads.  Every
# row of an allele, or of a region, as values -- mode-blind and
# multiplicity-blind, so the repeated allele answers both of its rows.
# ---------------------------------------------------------------------------


def test_an_allele_entry_unpacks_by_position_and_by_name() -> None:
    entry = AlleleEntry(10, "A", "C", (0.2, "ac"))

    pos, ref, alt, values = entry

    assert (pos, ref, alt, values) == (10, "A", "C", (0.2, "ac"))
    assert (entry.pos, entry.ref, entry.alt, entry.values) == (
        10, "A", "C", (0.2, "ac"))


def test_for_allele_rows_answers_every_row_of_a_repeated_allele(
    repeated_alleles: AlleleScore,
) -> None:
    """Both rows of ``1:10:A:C``, in file order, every score by default."""
    with repeated_alleles.open() as score:
        rows = score.get_allele_scores_for_allele_rows("1", 10, "A", "C")

    assert rows == [(0.2, "ac"), (0.5, "ac2")]


@pytest.mark.parametrize(("condition", "expected"), [
    ("freq > 0.3", [(0.5, "ac2")]),
    ("freq > 2.0", []),
])
def test_for_allele_rows_answers_only_the_rows_the_filter_keeps(
    repeated_alleles: AlleleScore,
    condition: str, expected: list[tuple[float, str]],
) -> None:
    """Rejecting one row answers the other; rejecting both answers ``[]``."""
    with repeated_alleles.open() as score:
        rows = score.get_allele_scores_for_allele_rows(
            "1", 10, "A", "C",
            score_filter=score.compile_filter(condition))

    assert rows == expected


@pytest.mark.parametrize(("pos", "ref", "alt"), [
    (10, "A", "G"),
    (12, "A", "C"),
    (16, "C", "A"),
])
def test_for_allele_rows_answers_an_empty_list_for_an_allele_with_no_row(
    repeated_alleles: AlleleScore, pos: int, ref: str, alt: str,
) -> None:
    """Never ``None``: absent and filtered read the same on this read.

    ``1:10:A:G`` shares a position with two rows and differs in alt only.
    """
    with repeated_alleles.open() as score:
        rows = score.get_allele_scores_for_allele_rows("1", pos, ref, alt)

    assert rows == []


def test_for_allele_rows_answers_the_scores_asked_in_the_order_asked(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        rows = score.get_allele_scores_for_allele_rows(
            "1", 10, "A", "C", scores=["id", "freq"])

    assert rows == [("ac", 0.2), ("ac2", 0.5)]


def test_for_allele_rows_does_not_answer_a_row_starting_elsewhere(
    tmp_path: pathlib.Path,
) -> None:
    """Exact on the position: a row at 8 whose span reaches 10 is not
    the allele at 10, even with the same nucleotides."""
    spanning = build_allele_score_from_resource(
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  pos_end  reference  alternative  freq
            1      8          12       A          C            0.7
            1      10         10       A          C            0.2
        """)
        .build_resource(tmp_path))

    with spanning.open() as score:
        rows = score.get_allele_scores_for_allele_rows("1", 10, "A", "C")

    assert rows == [(0.2,)]


def test_in_region_rows_yields_one_entry_per_row_in_position_order(
    repeated_alleles: AlleleScore,
) -> None:
    """Both repeated rows, each with its nucleotides, every score."""
    with repeated_alleles.open() as score:
        entries = list(score.get_allele_scores_in_region_rows("1", 10, 16))

    assert entries == [
        AlleleEntry(10, "A", "C", (0.2, "ac")),
        AlleleEntry(10, "A", "C", (0.5, "ac2")),
        AlleleEntry(16, "C", "T", (0.3, "ct")),
    ]


@pytest.mark.parametrize(("start", "end", "condition"), [
    (200, 300, None),
    (10, 16, "freq > 2.0"),
])
def test_in_region_rows_is_exhausted_for_absent_and_all_filtered(
    repeated_alleles: AlleleScore,
    start: int, end: int, condition: str | None,
) -> None:
    """A generator that yields nothing -- not ``None`` -- for a region no
    row overlaps and for one whose every row the filter rejected."""
    with repeated_alleles.open() as score:
        score_filter = (
            None if condition is None else score.compile_filter(condition))
        entries = score.get_allele_scores_in_region_rows(
            "1", start, end, score_filter=score_filter)

        assert entries is not None
        assert list(entries) == []


def test_in_region_rows_yields_only_the_rows_the_filter_keeps(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        entries = list(score.get_allele_scores_in_region_rows(
            "1", 10, 16, scores=["freq"],
            score_filter=score.compile_filter("freq > 0.25")))

    assert entries == [
        AlleleEntry(10, "A", "C", (0.5,)),
        AlleleEntry(16, "C", "T", (0.3,)),
    ]


# The four unreduced reads, each called on an EMPTY region (or an allele
# no row carries) of contig ``1``, so a refusal cannot hide behind data.
# Calling is all the test does: a generator read must refuse before it is
# iterated.
_ROWS_READS = {
    "for_allele_rows": lambda opened, chrom, **kw: (
        opened.get_allele_scores_for_allele_rows(chrom, 200, "A", "C", **kw)),
    "for_allele_row_singular": lambda opened, chrom, **kw: (
        opened.get_allele_score_for_allele_rows(chrom, 200, "A", "C", **kw)),
    "in_region_rows": lambda opened, chrom, **kw: (
        opened.get_allele_scores_in_region_rows(chrom, 200, 300, **kw)),
    "in_region_row_singular": lambda opened, chrom, **kw: (
        opened.get_allele_score_in_region_rows(chrom, 200, 300, **kw)),
}


@pytest.mark.parametrize("read", sorted(_ROWS_READS))
def test_an_unknown_contig_is_refused_by_every_rows_read_on_the_call(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError, match="not among the available chromosomes"):
        _ROWS_READS[read](score, "2", **_score_kwarg(read, "freq"))


@pytest.mark.parametrize("read", sorted(_ROWS_READS))
def test_a_foreign_filter_is_refused_by_every_rows_read_on_the_call(
    repeated_alleles: AlleleScore, tmp_path: pathlib.Path, read: str,
) -> None:
    other = build_allele_score_from_resource(
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          G            0.9
        """)
        .build_resource(tmp_path / "other"))

    with repeated_alleles.open() as score, other.open() as other_score:
        foreign = other_score.compile_filter("freq > 0.15")

        with pytest.raises(ScoreFilterError, match="compiled against"):
            _ROWS_READS[read](
                score, "1", score_filter=foreign,
                **_score_kwarg(read, "freq"))


@pytest.mark.parametrize("read", sorted(_ROWS_READS))
def test_an_unknown_score_is_refused_by_every_rows_read_on_the_call(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    """With the valid ids listed -- the singulars through
    ``_resolve_single_score``, which passes a named id on to the same
    check."""
    with repeated_alleles.open() as score, pytest.raises(
            ValueError,
            match=r"score 'nope' is not defined by resource '[^']*'; "
                  r"it has \['freq', 'id'\]"):
        _ROWS_READS[read](score, "1", **_score_kwarg(read, "nope"))


def _score_kwarg(read: str, score_id: str) -> dict[str, Any]:
    if read.endswith("_singular"):
        return {"score": score_id}
    return {"scores": [score_id]}


def test_the_singular_for_allele_read_answers_bare_values(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        values = score.get_allele_score_for_allele_rows(
            "1", 10, "A", "C", score="id")

    assert values == ["ac", "ac2"]


def test_the_singular_region_read_yields_pos_ref_alt_value(
    repeated_alleles: AlleleScore,
) -> None:
    with repeated_alleles.open() as score:
        rows = list(score.get_allele_score_in_region_rows(
            "1", 10, 16, score="freq"))

    assert rows == [(10, "A", "C", 0.2), (10, "A", "C", 0.5),
                    (16, "C", "T", 0.3)]


@pytest.mark.parametrize("read", [
    "for_allele_row_singular", "in_region_row_singular"])
def test_a_singular_rows_read_refuses_no_score_on_a_multi_score_resource(
    repeated_alleles: AlleleScore, read: str,
) -> None:
    with repeated_alleles.open() as score, pytest.raises(
            ValueError,
            match=r"defines \['freq', 'id'\]; a singular read can resolve "
                  r"score=None only when there is exactly one"):
        _ROWS_READS[read](score, "1")


def test_a_singular_rows_read_needs_no_score_on_a_single_score_resource(
    tmp_path: pathlib.Path,
) -> None:
    single = build_allele_score_from_resource(
        an_allele_score()
        .with_score("freq", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  freq
            1      10         A          C            0.2
        """)
        .build_resource(tmp_path))

    with single.open() as score:
        assert score.get_allele_score_for_allele_rows(
            "1", 10, "A", "C") == [0.2]
        assert list(score.get_allele_score_in_region_rows("1", 1, 20)) == [
            (10, "A", "C", 0.2)]


_ROWS_ORACLE_FILTERS = [None, "freq > 0.3", "freq > 2.0"]


@pytest.mark.parametrize("condition", _ROWS_ORACLE_FILTERS)
def test_mem_and_tabix_answer_the_same_for_allele_rows(
    tmp_path: pathlib.Path, condition: str | None,
) -> None:
    """The repeated allele, a shared position with another alt, and an
    allele no row carries -- unfiltered, filtered to one row, filtered to
    none -- through both the plural and the singular."""
    alleles = [
        (10, "A", "C"), (10, "A", "G"), (10, "A", "T"), (16, "C", "T"),
        (30, "G", "A"),
    ]
    answers = []
    for tabix in (False, True):
        score = _oracle_score(tmp_path / f"tabix-{tabix}", tabix=tabix)
        with score.open() as opened:
            score_filter = (
                None if condition is None
                else opened.compile_filter(condition))
            answers.append([
                (
                    opened.get_allele_scores_for_allele_rows(
                        "1", pos, ref, alt, score_filter=score_filter),
                    opened.get_allele_score_for_allele_rows(
                        "1", pos, ref, alt, score="id",
                        score_filter=score_filter),
                )
                for pos, ref, alt in alleles
            ])

    mem, indexed = answers
    assert mem == indexed
    if condition is None:
        assert mem[0] == (
            [(0.2, "ac", True), (0.5, "ac2", False)], ["ac", "ac2"])


@pytest.mark.parametrize("condition", _ROWS_ORACLE_FILTERS)
@pytest.mark.parametrize(("start", "end"), [(10, 16), (17, 39), (1, 100)])
def test_mem_and_tabix_answer_the_same_in_region_rows(
    tmp_path: pathlib.Path, condition: str | None, start: int, end: int,
) -> None:
    answers = []
    for tabix in (False, True):
        score = _oracle_score(tmp_path / f"tabix-{tabix}", tabix=tabix)
        with score.open() as opened:
            score_filter = (
                None if condition is None
                else opened.compile_filter(condition))
            plural = list(opened.get_allele_scores_in_region_rows(
                "1", start, end, score_filter=score_filter))
            singular = list(opened.get_allele_score_in_region_rows(
                "1", start, end, score="freq", score_filter=score_filter))
            answers.append((plural, singular))

    mem, indexed = answers
    assert mem == indexed
    if (start, end, condition) == (10, 16, None):
        assert [entry[:3] for entry in mem[0]] == [
            (10, "A", "C"), (10, "A", "C"), (10, "A", "G"),
            (16, "C", "T"), (16, "C", "T")]
