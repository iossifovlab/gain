"""The fragment kind's logical read plane -- the six ``get_*`` reads.

Read against the in-memory table backend, which is the oracle here: it holds
no per-table cursor, so it answers a read the same way whatever else the score
has been asked.  That makes it the right place to pin WHAT the plane answers,
and deliberately the wrong place to claim anything about holding two reads
open at once -- see :meth:`FragmentScore.get_fragment_scores_overlapping_region`
on the one-live-read limit a tabix-backed score has and this backend does not.
"""
# pylint: disable=W0621,C0114,C0116,W0212,W0613
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
)

#: The three singular reads and the three plural ones, each with a locus its
#: signature accepts.  Named because several tests below are ABOUT the family
#: being complete -- the keyword-only test is exactly their concatenation, so
#: a seventh read joins it by being declared here rather than by being
#: remembered.
SINGULAR_READS = [
    ("get_fragment_score_at_position", ("1", 155)),
    ("get_fragment_score_overlapping_region", ("1", 100, 199)),
    ("get_fragment_score_starting_in_region", ("1", 100, 199)),
]

PLURAL_READS = [
    ("get_fragment_scores_at_position", ("1", 155)),
    ("get_fragment_scores_overlapping_region", ("1", 100, 199)),
    ("get_fragment_scores_starting_in_region", ("1", 100, 199)),
]


@pytest.fixture
def fragments(tmp_path: pathlib.Path) -> FragmentScore:
    """Four fragments over one contig, chosen for clean overlap fractions.

    Read against the region ``[100, 199]`` -- 100 bases wide -- they cover
    every combination the two fraction predicates separate:

    ======================  =======  ===============  =================
    fragment                overlap  ``/ region`` (100)  ``/ fragment``
    ======================  =======  ===============  =================
    ``(50, 149)``  ``v=3``       50             0.5              0.5
    ``(100, 199)`` ``v=1``      100             1.0              1.0
    ``(150, 159)`` ``v=2``       10             0.1              1.0
    ``(190, 389)`` ``v=4``       10             0.1              0.05
    ======================  =======  ===============  =================
    """
    return FragmentScore(
        a_fragment_score()
        .with_score("v", "float")
        .with_data("""
            chrom  pos_begin  pos_end  v
            1      50         149      3.0
            1      100        199      1.0
            1      150        159      2.0
            1      190        389      4.0
        """)
        .build_resource(tmp_path),
    )


def test_overlapping_region_answers_every_fragment_at_its_own_extent(
    fragments: FragmentScore,
) -> None:
    """One entry per overlapping fragment, each at its own unclipped span."""
    with fragments.open() as score:
        assert list(score.get_fragment_scores_overlapping_region(
            "1", 100, 199)) == [
            (50, 149, (3.0,)),
            (100, 199, (1.0,)),
            (150, 159, (2.0,)),
            (190, 389, (4.0,)),
        ]


def test_the_singular_overlapping_read_answers_the_bare_value(
    fragments: FragmentScore,
) -> None:
    """The singular form unwraps the one-tuple its plural would answer."""
    with fragments.open() as score:
        assert list(score.get_fragment_score_overlapping_region(
            "1", 150, 155)) == [
            (100, 199, 1.0),
            (150, 159, 2.0),
        ]


@pytest.fixture
def two_score_fragments(tmp_path: pathlib.Path) -> FragmentScore:
    """One fragment carrying two scores, for the plural/singular contrast."""
    return FragmentScore(
        a_fragment_score()
        .with_score("freq", "float")
        .with_score("collection", "str")
        .with_data("""
            chrom  pos_begin  pos_end  freq  collection
            1      100        199      0.25  SSC
        """)
        .build_resource(tmp_path),
    )


@pytest.mark.parametrize("method,locus", SINGULAR_READS)
def test_a_singular_read_refuses_score_none_when_there_are_two(
    two_score_fragments: FragmentScore,
    method: str,
    locus: tuple[object, ...],
) -> None:
    """``score=None`` means "all of them", which a singular read cannot do.

    Pinned on all three singular reads, and pinned on the message: a read
    that passed ``[score]`` straight through instead of resolving it would
    still raise, but from ``_resolve_score_defs`` complaining that the
    resource does not define ``None``.  Not iterated, so the refusal has to
    come from the call rather than from a generator body.
    """
    with two_score_fragments.open() as score, \
            pytest.raises(ValueError, match="exactly one"):
        getattr(score, method)(*locus)


def test_values_follow_the_requested_score_order(
    two_score_fragments: FragmentScore,
) -> None:
    """The plural tuple is parallel to ``scores`` as asked for."""
    with two_score_fragments.open() as score:
        assert list(score.get_fragment_scores_overlapping_region(
            "1", 100, 199, scores=["collection", "freq"])) == [
            (100, 199, ("SSC", 0.25)),
        ]


def test_min_region_overlap_fraction_keeps_what_covers_enough_of_the_region(
    fragments: FragmentScore,
) -> None:
    """``overlap / region_length >= threshold`` -- "covers this much of MINE".

    Over ``[100, 199]`` the four fragments score 0.5, 1.0, 0.1 and 0.1, so
    ``0.5`` admits the first two and rejects the two 10-base overlaps.
    """
    with fragments.open() as score:
        assert list(score.get_fragment_score_overlapping_region(
            "1", 100, 199, min_region_overlap_fraction=0.5)) == [
            (50, 149, 3.0),
            (100, 199, 1.0),
        ]


def test_min_fragment_overlap_fraction_keeps_what_falls_inside_enough(
    fragments: FragmentScore,
) -> None:
    """``overlap / fragment_length >= threshold`` -- "this much of MINE is in".

    A different question from the region fraction: the 10-base fragment
    scores 0.1 against the region and 1.0 against itself.  ``1.0`` is full
    containment, because the comparison is ``>=``.
    """
    with fragments.open() as score:
        assert list(score.get_fragment_score_overlapping_region(
            "1", 100, 199, min_fragment_overlap_fraction=1.0)) == [
            (100, 199, 1.0),
            (150, 159, 2.0),
        ]


def test_the_two_fractions_combine_with_and(
    fragments: FragmentScore,
) -> None:
    """Every threshold supplied must hold, so AND is stricter than either.

    Alone, ``min_region=0.5`` admits ``(50, 149)`` and ``(100, 199)`` and
    ``min_fragment=1.0`` admits ``(100, 199)`` and ``(150, 159)``.  Together
    only the fragment in both survives.
    """
    with fragments.open() as score:
        assert list(score.get_fragment_score_overlapping_region(
            "1", 100, 199,
            min_region_overlap_fraction=0.5,
            min_fragment_overlap_fraction=1.0)) == [
            (100, 199, 1.0),
        ]


@pytest.mark.parametrize("kwargs", [
    {"min_region_overlap_fraction": -0.1},
    {"min_region_overlap_fraction": 1.5},
    {"min_fragment_overlap_fraction": -0.1},
    {"min_fragment_overlap_fraction": 1.5},
])
@pytest.mark.parametrize("method", [
    "get_fragment_score_overlapping_region",
    "get_fragment_scores_overlapping_region",
])
def test_a_fraction_outside_zero_to_one_is_refused_on_the_call(
    fragments: FragmentScore,
    kwargs: dict[str, float],
    method: str,
) -> None:
    """And refused BEFORE the first ``next()``, as the request guards are.

    Not merely inside the generator body: a threshold no fraction can reach
    is a caller error, and a read that reported it only once consumed would
    hand a plausible empty result to a caller that never iterated.
    """
    with fragments.open() as score, \
            pytest.raises(ValueError, match="between 0 and 1"):
        getattr(score, method)("1", 100, 199, **kwargs)


@pytest.mark.parametrize("method", [
    "get_fragment_score_overlapping_region",
    "get_fragment_scores_overlapping_region",
    "get_fragment_score_starting_in_region",
    "get_fragment_scores_starting_in_region",
])
@pytest.mark.parametrize("start,end", [(0, 199), (100, 99), (100, 50)])
def test_a_region_no_genomic_span_can_mean_is_refused_on_the_call(
    fragments: FragmentScore,
    method: str,
    start: int,
    end: int,
) -> None:
    """A 1-based lower bound and an end that precedes its start.

    The same rule
    :meth:`~.position.PositionScore.get_scores_in_region` refuses by, and
    the fragment plane owes it for the same reason: an inverted region has
    a NEGATIVE width, which the overlap fractions then divide by.
    """
    with fragments.open() as score, \
            pytest.raises(ValueError, match=r"1-based|precedes its start"):
        getattr(score, method)("1", start, end)


def test_an_inverted_region_is_refused_whether_or_not_a_fraction_is_given(
    fragments: FragmentScore,
) -> None:
    """The refusal cannot depend on which optional argument was supplied.

    ``end == start - 1`` makes the region width zero, so a fraction divides
    by it; every other width makes it negative, so every fragment is
    silently rejected.  Both are the same caller error, and answering one
    of them with data and the other with a ``ZeroDivisionError`` would make
    the region's validity look like a property of the fraction.
    """
    with fragments.open() as score:
        with pytest.raises(ValueError, match="precedes its start"):
            score.get_fragment_scores_overlapping_region("1", 100, 99)
        with pytest.raises(ValueError, match="precedes its start"):
            score.get_fragment_scores_overlapping_region(
                "1", 100, 99, min_region_overlap_fraction=0.5)


@pytest.mark.parametrize("method", [
    "get_fragment_score_at_position",
    "get_fragment_scores_at_position",
])
def test_a_position_below_one_is_refused_rather_than_read_as_unbounded(
    fragments: FragmentScore,
    method: str,
) -> None:
    """Position 0 is a caller error, not "the whole contig".

    The in-memory table tests its bounds for truthiness, so a ``0`` reaches
    it as "unbounded" and every fragment on the contig comes back -- a
    plausible answer to a question nobody asked.  The plane refuses it
    before the backend is reached, as
    :meth:`~.position.PositionScore.get_scores_at_position` does.
    """
    with fragments.open() as score, \
            pytest.raises(ValueError, match="1-based"):
        getattr(score, method)("1", 0)


def test_at_position_answers_every_fragment_covering_the_position(
    fragments: FragmentScore,
) -> None:
    """Position 155 lies inside two of the four fragments."""
    with fragments.open() as score:
        assert list(score.get_fragment_scores_at_position("1", 155)) == [
            (100, 199, (1.0,)),
            (150, 159, (2.0,)),
        ]
        assert list(score.get_fragment_score_at_position("1", 155)) == [
            (100, 199, 1.0),
            (150, 159, 2.0),
        ]


def test_at_position_materialises_so_it_can_be_measured_and_reread(
    fragments: FragmentScore,
) -> None:
    """A ``Sequence``, not a generator: ``len()`` works and it re-iterates.

    The caller convenience the method exists for.  A generator answers
    ``len()`` with a ``TypeError`` and the second walk with nothing.
    """
    with fragments.open() as score:
        covering = score.get_fragment_scores_at_position("1", 155)

    assert len(covering) == 2
    assert list(covering) == list(covering)


def test_starting_in_region_ignores_a_fragment_that_merely_overlaps(
    fragments: FragmentScore,
) -> None:
    """The contrast with the overlapping read, on one window.

    ``(50, 149)`` runs into ``[100, 149]`` and the overlapping read answers
    it; this read does not, because the fragment BEGINS outside.
    """
    with fragments.open() as score:
        assert list(score.get_fragment_score_starting_in_region(
            "1", 100, 149)) == [
            (100, 199, 1.0),
        ]


def test_adjacent_windows_partition_the_fragments(
    fragments: FragmentScore,
) -> None:
    """Every fragment answered by exactly one window -- no gaps, no repeats.

    The property chunked and parallel work depends on, and the reason this
    read is kept although nothing calls it yet.  Asserted as the partition
    itself rather than window by window: concatenating the windows in order
    must reproduce the whole contig exactly.
    """
    windows = [(1, 99), (100, 149), (150, 199), (200, 400)]
    with fragments.open() as score:
        per_window = [
            list(score.get_fragment_scores_starting_in_region(
                "1", start, end))
            for start, end in windows
        ]
        whole_contig = list(score.get_fragment_scores_overlapping_region(
            "1", 1, 400))

    assert per_window == [
        [(50, 149, (3.0,))],
        [(100, 199, (1.0,))],
        [(150, 159, (2.0,)), (190, 389, (4.0,))],
        [],
    ]
    assert [row for window in per_window for row in window] == whole_contig


@pytest.mark.parametrize("method,locus", SINGULAR_READS + PLURAL_READS)
def test_everything_after_the_locus_is_keyword_only(
    fragments: FragmentScore,
    method: str,
    locus: tuple[object, ...],
) -> None:
    """The positional part is exactly the locus, and refusing this matters.

    ``fetch_fragment_scores(chrom, start, stop, scores)`` takes its score
    list positionally, so a caller migrating from it would otherwise bind
    that list to whatever this plane happens to put fourth -- no error, just
    a plausible-looking wrong answer.
    """
    with fragments.open() as score, \
            pytest.raises(TypeError, match="positional argument"):
        getattr(score, method)(*locus, ["v"])


@pytest.mark.parametrize("method", [
    "get_fragment_scores_overlapping_region",
    "get_fragment_scores_starting_in_region",
])
def test_a_region_read_pulls_records_only_as_it_is_consumed(
    fragments: FragmentScore,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Taking one fragment reads one record, not the whole region.

    The laziness the primitive under these reads was reshaped for (#1122);
    an implementation that materialised internally and handed back
    ``iter(...)`` would satisfy every other assertion here just as well.
    """
    with fragments.open() as score:
        pulled: list[Record] = []
        real_fetch_records = score.fetch_records

        def counting_fetch_records(
            *args: Any, **kwargs: Any,
        ) -> Iterator[Record]:
            for record in real_fetch_records(*args, **kwargs):
                pulled.append(record)
                yield record

        monkeypatch.setattr(score, "fetch_records", counting_fetch_records)

        rows = getattr(score, method)("1", 1, 400)

        assert not pulled
        next(rows)
        assert len(pulled) == 1


@pytest.mark.parametrize("method,locus", PLURAL_READS)
def test_a_rejected_fragment_is_simply_not_among_the_answers(
    fragments: FragmentScore,
    method: str,
    locus: tuple[object, ...],
) -> None:
    """``score_filter`` reaches every read on the plane, unchanged."""
    with fragments.open() as score:
        kept = list(getattr(score, method)(
            *locus, score_filter=score.compile_filter("v > 1.5")))

    assert all(values[0] > 1.5 for _beg, _end, values in kept)
    assert kept


@pytest.mark.parametrize("method,locus", [
    ("get_fragment_scores_overlapping_region", ("1", 100, 199)),
    ("get_fragment_scores_starting_in_region", ("1", 100, 199)),
])
def test_an_unknown_score_id_is_refused_before_the_first_record(
    fragments: FragmentScore,
    method: str,
    locus: tuple[object, ...],
) -> None:
    """The REQUEST is checked on the call; only the READING is lazy.

    A typo must not answer differently on a populated contig than on an
    empty one -- the reason the primitive's guards are eager, kept by
    composing it rather than by re-deferring it.
    """
    with fragments.open() as score, \
            pytest.raises(ValueError, match="does not define"):
        getattr(score, method)(*locus, scores=["nope"])
