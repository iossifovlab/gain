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


def test_an_unknown_allele_key_score_is_refused_from_the_call(
    alleles: AlleleScore,
) -> None:
    """Resolved up front, with the valid names -- not a per-record KeyError.

    Earlier than the annotator's old path refused it, and on an EMPTY
    region too, so the typo cannot hide behind absent data.
    """
    with alleles.open() as score, pytest.raises(
            ValueError,
            match=r"does not define \['nope'\]; it has \['freq', 'id'\]"):
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
