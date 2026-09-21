"""The statistics scan's vectorized way in to a region (gain#591, ADR 0008).

The bulk counterpart of ``test_scan_read_door.py``.  The scan reads a region's
column arrays through
``validate_record_arrays(score, fetch_region_value_arrays(...))``
-- the same shape as the per-record door, one link over the stream the scan is
already pulling -- and every plain array read leaves that link out.

What these tests are for is the equivalence: a malformed resource must be
refused the SAME way whichever path it was eligible for.  Before gain#591 the
two paths read one class attribute and enforced different subsets of one rule,
which is the drift gain#585 is unwinding.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
from typing import ClassVar

import numpy as np
import pyBigWig
import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    GenomicScore,
    PositionScore,
    RecordArrays,
    build_allele_score_from_resource,
    build_fragment_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    scan,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.statistics.record_validation import (
    validate_record_arrays,
    validate_records,
)
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


def _batch(begins: list[int], ends: list[int]) -> RecordArrays:
    """One batch of raw column arrays, as the backends produce them."""
    return (
        np.array(begins, dtype=np.int64),
        np.array(ends, dtype=np.int64),
        {"s": np.array([0.5] * len(begins), dtype=np.float64)},
    )


def _records_of(batches: list[RecordArrays]) -> list[Record]:
    """The per-record stream carrying exactly the rows of ``batches``.

    Both doors then read the same rows, so a comparison of their refusals
    is of the two rules and not of two fixtures.
    """
    return [
        ("chr1", int(begin), int(end), None, None,
         ("chr1", str(begin), str(end), "0.5"))
        for pos_begin, pos_end, _cells in batches
        for begin, end in zip(pos_begin, pos_end, strict=True)
    ]


TOUCHING_RECORDS = """
    chrom  pos_begin  pos_end  s
    chr1   1          5        0.1
    chr1   5          9        0.2
"""


def _position_score_resource(
    tmp_path: pathlib.Path, resource_id: str, data: str,
) -> GenomicResource:
    return (
        a_grr()
        .with_resource(
            resource_id,
            a_position_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data(data))
        .build_repo(tmp_path)
        .get_resource(resource_id)
    )


def _position_score(
    tmp_path: pathlib.Path, resource_id: str, data: str = TOUCHING_RECORDS,
) -> PositionScore:
    score = build_position_score_from_resource(
        _position_score_resource(tmp_path, resource_id, data))
    score.open()
    return score


def test_the_array_scan_refuses_a_position_score_whose_records_touch(
    tmp_path: pathlib.Path,
) -> None:
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(validate_record_arrays(score,
            score.fetch_region_value_arrays("chr1", 1, 10, ["s"]), "chr1"))

    message = str(excinfo.value)
    assert "<touching>" in message
    assert "chr1:5" in message
    assert "at most one record per position" in message


def test_a_score_kind_must_state_its_own_record_rules(
    tmp_path: pathlib.Path,
) -> None:
    # gain#592 deleted the base defaults, so a kind that states neither rule
    # fails loudly instead of being validated by a rule nobody chose for it.
    #
    # The two record rules and the weight rule now say that in two different
    # ways, and the difference is the point of ADR 0027.  ``record_weight`` is
    # still ``@abstractmethod`` on ``GenomicScore``, so mypy ([abstract]) and
    # pylint (W0223) refuse this kind before it runs -- which is why the class
    # below still has to silence them both.  The record rules have left the
    # class: a kind absent from ``record_validation``'s registry is refused by
    # the base registration when the scan reads it, and by nothing earlier.
    # The refusal names the class, because a registry has no other way to say
    # WHICH kind was never registered.
    class _KindStatingNothing(GenomicScore):  # pylint: disable=W0223
        """A new score kind whose author stated no record rule."""

        DEFAULT_AGGREGATORS: ClassVar[dict[str, str | None]] = {
            "float": "max", "int": "max", "str": "join(,)", "bool": None,
        }

    kind = _KindStatingNothing(  # type: ignore[abstract]
        _position_score_resource(tmp_path, "any_kind", TOUCHING_RECORDS))

    with pytest.raises(NotImplementedError, match="_KindStatingNothing"):
        list(validate_records(kind, iter([])))
    with pytest.raises(NotImplementedError, match="_KindStatingNothing"):
        list(validate_record_arrays(kind, iter([]), "chr1"))
    # The weight rule joined them in gain#1095, and for the same reason: an
    # inherited "counts once" reads exactly like a kind whose author
    # considered the question and answered it.  Asked of the CLASS, since
    # that is how both scan paths reach it.
    with pytest.raises(NotImplementedError):
        _KindStatingNothing.record_weight(10, 19)


ONE_ALLELE = """
    chrom  pos_begin  reference  alternative  s
    chr1   10         A          G            0.1
"""


def _allele_score(
    tmp_path: pathlib.Path, resource_id: str, data: str = ONE_ALLELE,
) -> AlleleScore:
    # The default resource is well formed -- a table whose positions decrease
    # cannot be tabix-indexed, so the backwards stream is supplied directly,
    # as the per-record tests of this rule also do.
    score = build_allele_score_from_resource(
        a_grr()
        .with_resource(
            resource_id,
            an_allele_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data(data))
        .build_repo(tmp_path)
        .get_resource(resource_id))
    score.open()
    return score


def test_the_array_scan_refuses_an_allele_record_that_moves_backwards(
    tmp_path: pathlib.Path,
) -> None:
    score = _allele_score(tmp_path, "backwards_allele")

    with pytest.raises(MalformedResourceError) as excinfo:
        list(validate_record_arrays(score,
            iter([_batch([10, 5], [10, 5])]), "chr1"))

    message = str(excinfo.value)
    assert "<backwards_allele>" in message
    assert "chr1:5" in message
    assert "an allele score's records must not move backwards" in message


def test_the_array_scan_accepts_allele_records_sharing_a_position(
    tmp_path: pathlib.Path,
) -> None:
    # Several records at one position is what an allele score IS -- one per
    # ref/alt pair -- so the rule compares strictly, and this must pass
    # through untouched.
    score = _allele_score(tmp_path, "shared_site")
    batch = _batch([10, 10, 10], [10, 10, 10])

    assert list(validate_record_arrays(score, iter([batch]), "chr1")) == [batch]


def test_the_allele_rule_reads_the_begins_and_not_the_ends(
    tmp_path: pathlib.Path,
) -> None:
    # An allele record may carry a ``pos_end`` column, and the rule must not
    # look at it: the record means the point it sits at, however wide that
    # column reaches.  Every other allele fixture here sets end == begin,
    # which cannot tell a begins-to-begins comparison from a begins-to-ends
    # one -- this one can, because its ends decrease while its begins rise.
    score = _allele_score(tmp_path, "allele_with_ends")
    batch = _batch([10, 20], [50, 20])

    assert list(validate_record_arrays(score, iter([batch]), "chr1")) == [batch]


def test_an_empty_batch_passes_through_every_kind(
    tmp_path: pathlib.Path,
) -> None:
    # A real backend does yield one: a tabix region query whose rows all
    # begin past the region's end cuts to an empty batch and yields it.  The
    # size guard is what keeps that from indexing off the end of an empty
    # carry column, which would raise IndexError -- an internal error, not a
    # resource refusal, failing a task on a perfectly healthy resource.
    empty = _batch([], [])
    scores = [
        _position_score(tmp_path, "empty_position", TOUCHING_RECORDS),
        _allele_score(tmp_path, "empty_allele"),
        _fragment_score(tmp_path, "empty_fragment"),
    ]

    for score in scores:
        assert list(validate_record_arrays(score, iter([empty]), "chr1")) == [
            empty]
        # An empty batch also must not disturb the carry: the pair below
        # straddles it and is still refused.
        with pytest.raises(MalformedResourceError):
            list(validate_record_arrays(score,
                iter([_batch([10], [10]), empty, _batch([5], [5])]), "chr1"))


def _backwards_arrays(*_args: object, **_kwargs: object) -> object:
    """Stand in for a backend handing the scan a backwards batch."""
    return iter([_batch([10, 5], [10, 5])])


def test_the_bulk_histogram_scan_applies_the_allele_rule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Net-new enforcement: before gain#591 the vectorized guard was gated on
    # DISJOINT, so an allele score's records could run backwards through a
    # completed bulk scan and it still certified the resource.
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    monkeypatch.setattr(
        AlleleScore, "fetch_region_value_arrays", _backwards_arrays)

    with pytest.raises(MalformedResourceError, match="move backwards"):
        scan.do_histogram_bulk(
            resource, {"s": _hist_conf()}, "chr1", 1, 20)


def test_the_bulk_min_max_scan_applies_the_allele_rule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both scan passes read through the door, so neither can be the one that
    # forgets -- a resource refused by the histogram pass is refused by the
    # min/max pass that runs before it.
    resource = (
        an_allele_score()
        .with_score("s", "float")
        .with_data("""
            chrom  pos_begin  reference  alternative  s
            chr1   10         A          G            0.1
        """)
        .with_tabix()
        .build_resource(tmp_path)
    )
    monkeypatch.setattr(
        AlleleScore, "fetch_region_value_arrays", _backwards_arrays)

    with pytest.raises(MalformedResourceError, match="move backwards"):
        scan.do_min_max_bulk(
            resource, ["s"], "chr1", 1, 20)


def _fragment_score(tmp_path: pathlib.Path, resource_id: str) -> FragmentScore:
    score = build_fragment_score_from_resource(
        a_grr()
        .with_resource(
            resource_id,
            a_fragment_score()
            .with_score("s", "float")
            .with_tabix()
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         20       0.1
            """))
        .build_repo(tmp_path)
        .get_resource(resource_id))
    score.open()
    return score


def test_the_array_scan_refuses_a_fragment_that_moves_backwards(
    tmp_path: pathlib.Path,
) -> None:
    score = _fragment_score(tmp_path, "backwards_fragment")

    with pytest.raises(MalformedResourceError) as excinfo:
        list(validate_record_arrays(score,
            iter([_batch([10, 5], [40, 50])]), "chr1"))

    message = str(excinfo.value)
    assert "<backwards_fragment>" in message
    assert "chr1:5" in message
    assert "a fragment score's records must not move backwards" in message


def test_the_array_scan_accepts_fragments_that_overlap(
    tmp_path: pathlib.Path,
) -> None:
    # Fragments overlap freely and several may share a start; a fragment's
    # own end takes no part in the rule, so an interval reaching back over
    # its predecessor is the normal case.
    score = _fragment_score(tmp_path, "overlapping_fragments")
    batch = _batch([10, 10, 12], [100, 20, 15])

    assert list(validate_record_arrays(score, iter([batch]), "chr1")) == [batch]


def test_the_array_rules_carry_across_a_batch_boundary(
    tmp_path: pathlib.Path,
) -> None:
    # Batches are a read-granularity artefact -- the backend's window, or a
    # batch_size hint -- so no verdict may depend on where one happens to
    # break.  Each pair below is legal within its own batch and illegal
    # across the two.
    allele = _allele_score(tmp_path, "allele_carry")
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(validate_record_arrays(allele,
            iter([_batch([10], [10]), _batch([5], [5])]), "chr1"))

    fragment = _fragment_score(tmp_path, "fragment_carry")
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(validate_record_arrays(fragment,
            iter([_batch([10], [40]), _batch([5], [50])]), "chr1"))

    position = _position_score(tmp_path, "position_carry", TOUCHING_RECORDS)
    with pytest.raises(MalformedResourceError, match="chr1:5"):
        list(validate_record_arrays(position,
            iter([_batch([1], [5]), _batch([5], [9])]), "chr1"))


def test_a_malformed_resource_is_refused_the_same_way_down_both_paths(
    tmp_path: pathlib.Path,
) -> None:
    # The point of the slice.  One resource, both scan paths, one message:
    # before gain#591 the vectorized path enforced a different subset of the
    # rule than the per-record one while both read a single class attribute,
    # so "the scan passed" meant different things depending on which path the
    # resource happened to be eligible for.
    resource = (
        a_position_score()
        .with_score("s", "float")
        .with_data(TOUCHING_RECORDS)
        .with_tabix()
        .build_resource(tmp_path)
    )
    confs: dict = {"s": _hist_conf()}

    with pytest.raises(MalformedResourceError) as per_record:
        scan.do_histogram(
            resource, confs, "chr1", 1, 9)
    with pytest.raises(MalformedResourceError) as bulk:
        scan.do_histogram_bulk(
            resource, confs, "chr1", 1, 9)

    assert str(bulk.value) == str(per_record.value)


@pytest.mark.parametrize("kind,builder,possessive", [
    ("fragment", _fragment_score, "a fragment score's"),
    ("allele", _allele_score, "an allele score's"),
])
def test_the_backwards_rule_is_refused_the_same_way_down_both_paths(
    tmp_path: pathlib.Path,
    kind: str,
    builder: object,
    possessive: str,
) -> None:
    # ``test_a_malformed_resource_is_refused_the_same_way_down_both_paths``
    # makes this claim for the POSITION kind, where both validators have
    # always built the refusal from ``overlapping_records_error`` and so
    # cannot drift.  The backwards rule is the one that could: it is stated
    # twice per kind, for two kinds, and was written out longhand at all four
    # sites until ``backwards_records_error`` collapsed them.  Six copies of
    # one sentence agree only for as long as nobody edits one of them, and
    # nothing compared them.
    #
    # Both refusals are driven off the SAME violation -- one record at 10
    # following one at 20 -- so the comparison is of the two code paths and
    # not of two fixtures.
    score = builder(tmp_path, f"backwards_{kind}")  # type: ignore[operator]
    batches = [_batch([20, 10], [29, 19])]

    with pytest.raises(MalformedResourceError) as per_record:
        list(validate_records(score, iter(_records_of(batches))))
    with pytest.raises(MalformedResourceError) as arrays:
        list(validate_record_arrays(score, iter(batches), "chr1"))

    assert str(arrays.value) == str(per_record.value)
    assert f"{possessive} records must not move backwards" in str(arrays.value)


# The backends the array door reads keep almost every inverted span out, and
# the two tests below pin how much: tabix, checking zero-based, admits a
# 1-based row ending exactly one below its begin, and a bigWig admits none.
# The tests after them are what the door says to the row tabix lets through.


def _a_second_row_ending_at(pos_end: int) -> str:
    return f"""
        chrom  pos_begin  pos_end  s
        chr1   10         20       0.1
        chr1   30         {pos_end}       0.2
        chr1   35         40       0.3
    """


def test_tabix_refuses_to_index_a_row_ending_two_below_its_begin(
    tmp_path: pathlib.Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    # ``pysam`` raises the same "building of index" for an unsorted table,
    # so the reason is read off fd 2, where htslib alone writes it.
    with pytest.raises(OSError, match="building of index"):
        _position_score_resource(
            tmp_path, "inverted", _a_second_row_ending_at(28))

    assert "Invalid record on sequence #1: end 28 < begin 30" \
        in capfd.readouterr().err


def test_a_bigwig_cannot_be_written_with_an_inverted_interval(
    tmp_path: pathlib.Path,
) -> None:
    # Asked of ``pyBigWig`` itself rather than of ``a_bigwig_score()``: the
    # bigWig test helper asserts ``start < end`` before the writer sees the
    # row.  Both refused intervals begin past the first one's end, so their
    # own two ends are all that is wrong with them; the second shows the
    # bound is ``end <= start``.
    bigwig = pyBigWig.open(  # pylint: disable=I1101
        str(tmp_path / "inverted.bw"), "w")
    bigwig.addHeader([("chr1", 100)])
    bigwig.addEntries(["chr1"], [9], ends=[20], values=[0.1])

    with pytest.raises(RuntimeError, match="illegal values"):
        bigwig.addEntries(["chr1"], [40], ends=[30], values=[0.2])
    with pytest.raises(RuntimeError, match="illegal values"):
        bigwig.addEntries(["chr1"], [30], ends=[30], values=[0.2])

    bigwig.close()


def test_a_tabix_row_ending_one_below_its_begin_is_refused_down_both_doors(
    tmp_path: pathlib.Path,
) -> None:
    # htslib checks zero-based, where the 1-based ``30 29`` is the empty
    # interval ``[29, 29)`` and not inverted, so this is the one inverted
    # row a backend hands the array door.  It must be refused there exactly
    # as the per-record door refuses it (gain#1526).
    score = _position_score(tmp_path, "empty", _a_second_row_ending_at(29))

    with pytest.raises(OSError) as per_record:
        list(validate_records(score, score.fetch_records("chr1", 1, 100)))
    with pytest.raises(OSError) as arrays:
        list(validate_record_arrays(
            score, score.fetch_region_value_arrays("chr1", 1, 100, ["s"]),
            "chr1"))

    assert str(arrays.value) == str(per_record.value)
    assert "chr1:30-29" in str(arrays.value)
    assert "end 29 smaller than the beginning 30" in str(arrays.value)


def test_the_histogram_scan_refuses_the_inverted_row_down_both_paths(
    tmp_path: pathlib.Path,
) -> None:
    # The same row through the scan's own entry points, so the claim is
    # about what ``repo-stats`` says and not only about the two validators.
    # The histogram pass stands for both bulk passes: the min/max pass reads
    # through the same door, as the allele-rule tests above show.
    resource = _position_score_resource(
        tmp_path, "empty", _a_second_row_ending_at(29))
    confs: dict = {"s": _hist_conf()}

    with pytest.raises(OSError) as per_record:
        scan.do_histogram(resource, confs, "chr1", 1, 100)
    with pytest.raises(OSError) as bulk:
        scan.do_histogram_bulk(resource, confs, "chr1", 1, 100)

    assert str(bulk.value) == str(per_record.value)
    assert "chr1:30-29" in str(bulk.value)


@pytest.mark.parametrize("kind,builder", [
    ("position", _position_score),
    ("allele", _allele_score),
    ("fragment", _fragment_score),
])
def test_every_kind_refuses_an_inverted_span_at_the_array_door(
    tmp_path: pathlib.Path,
    kind: str,
    builder: object,
) -> None:
    # A record's own two ends are a claim about that record, not about the
    # kind's ordering, so every kind's rule refuses it -- as every kind's
    # per-record rule does, through one shared reader.  The stream is
    # supplied directly: no allele fixture carries a ``pos_end`` column.
    score = builder(tmp_path, f"inverted_{kind}")  # type: ignore[operator]

    with pytest.raises(OSError, match="chr1:30-29") as excinfo:
        list(validate_record_arrays(score,
            iter([_batch([10, 30, 35], [20, 29, 40])]), "chr1"))

    assert "end 29 smaller than the beginning 30" in str(excinfo.value)


def test_an_allele_inverted_span_loses_its_ref_alt_at_the_array_door(
    tmp_path: pathlib.Path,
) -> None:
    # The one place the two doors' refusals differ in wording: an allele
    # record carries its ref and alt, and the per-record door names them;
    # the column batches the array door reads carry neither, so its message
    # stops at the position.  Same type, same record, one suffix apart --
    # pinned so the narrowing is a stated fact rather than a surprise.
    score = _allele_score(tmp_path, "inverted_allele_with_ends", """
        chrom  pos_begin  pos_end  reference  alternative  s
        chr1   10         10       A          G            0.1
        chr1   30         29       C          T            0.2
    """)

    with pytest.raises(OSError) as per_record:
        list(validate_records(score, score.fetch_records("chr1", 1, 100)))
    with pytest.raises(OSError) as arrays:
        list(validate_record_arrays(
            score, score.fetch_region_value_arrays("chr1", 1, 100, ["s"]),
            "chr1"))

    assert "chr1:30-29 C->T has" in str(per_record.value)
    assert str(arrays.value) == str(per_record.value).replace(" C->T", "")


@pytest.mark.parametrize("data,batch_size,first_fault", [
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   10         20       0.1
        chr1   15         25       0.2
        chr1   30         29       0.3
    """, 10, "at most one record per position", id="overlap-first"),
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   10         20       0.1
        chr1   30         29       0.2
        chr1   32         35       0.3
        chr1   33         40       0.4
    """, 10, "end 29 smaller than the beginning 30", id="inverted-first"),
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   10         20       0.1
        chr1   15         14       0.2
    """, 10, "end 14 smaller than the beginning 15", id="same-record"),
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   5          8        0.1
        chr1   10         20       0.2
        chr1   15         25       0.3
        chr1   30         29       0.4
    """, 2, "at most one record per position", id="overlap-across-a-batch"),
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   10         20       0.1
        chr1   15         25       0.2
        chr1   30         29       0.3
        chr1   35         40       0.4
    """, 2, "at most one record per position",
        id="overlap-in-an-earlier-batch"),
    pytest.param("""
        chrom  pos_begin  pos_end  s
        chr1   5          8        0.1
        chr1   30         29       0.2
        chr1   32         40       0.3
        chr1   33         45       0.4
    """, 2, "end 29 smaller than the beginning 30",
        id="inverted-in-an-earlier-batch"),
])
def test_both_doors_name_the_same_first_fault_of_a_two_fault_resource(
    tmp_path: pathlib.Path,
    data: str,
    batch_size: int,
    first_fault: str,
) -> None:
    # A resource may break two rules at once, and "refused the same way"
    # then means the same FIRST fault in record order.  The per-record door
    # reads a record's own span before comparing it with its predecessor,
    # so a span fault wins a tie on one record (``same-record``); the array
    # door must reach the same answer whichever batch either fault lands
    # in.  The three ``batch_size=2`` cases read two records per batch:
    # the overlap caught on the carried end with the inverted row after it
    # in the same batch, and each fault alone in the batch before the other.
    score = _position_score(tmp_path, "two_faults", data)

    with pytest.raises((OSError, MalformedResourceError)) as per_record:
        list(validate_records(score, score.fetch_records("chr1", 1, 100)))
    with pytest.raises((OSError, MalformedResourceError)) as arrays:
        list(validate_record_arrays(
            score,
            score.fetch_region_value_arrays(
                "chr1", 1, 100, ["s"], batch_size=batch_size),
            "chr1"))

    assert type(arrays.value) is type(per_record.value)
    assert str(arrays.value) == str(per_record.value)
    assert first_fault in str(arrays.value)


@pytest.mark.parametrize("batches,first_fault", [
    pytest.param(
        [_batch([20, 10, 30], [29, 19, 29])],
        "must not move backwards", id="backwards-first"),
    pytest.param(
        [_batch([20], [29]), _batch([10, 30], [19, 29])],
        "must not move backwards", id="backwards-first-across-a-batch"),
    pytest.param(
        [_batch([10, 30, 5], [20, 29, 6])],
        "end 29 smaller than the beginning 30", id="inverted-first"),
    pytest.param(
        [_batch([10, 30], [20, 29]), _batch([5], [6])],
        "end 29 smaller than the beginning 30",
        id="inverted-first-in-an-earlier-batch"),
    pytest.param(
        [_batch([20, 10], [29, 9])],
        "end 9 smaller than the beginning 10", id="same-record"),
    pytest.param(
        [_batch([20], [29]), _batch([10], [9])],
        "end 9 smaller than the beginning 10",
        id="same-record-across-a-batch"),
])
def test_both_doors_name_the_same_first_fault_of_a_two_fault_stream(
    tmp_path: pathlib.Path,
    batches: list[RecordArrays],
    first_fault: str,
) -> None:
    # The backwards rule's half of the claim above, over the same
    # arrangements: the faults in either order, on one record, and split
    # across a batch boundary.  Supplied directly, since a table whose
    # positions decrease cannot be tabix-indexed; both doors read the same
    # rows, so the comparison is of the two rules and not of two fixtures.
    score = _fragment_score(tmp_path, "two_fault_fragment")

    with pytest.raises((OSError, MalformedResourceError)) as per_record:
        list(validate_records(score, iter(_records_of(batches))))
    with pytest.raises((OSError, MalformedResourceError)) as arrays:
        list(validate_record_arrays(score, iter(batches), "chr1"))

    assert type(arrays.value) is type(per_record.value)
    assert str(arrays.value) == str(per_record.value)
    assert first_fault in str(arrays.value)
