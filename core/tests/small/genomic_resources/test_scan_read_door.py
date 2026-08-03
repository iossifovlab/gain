"""The statistics scan's own read door (gain#588, ADR 0008).

The scan reads through :meth:`GenomicScore.scan_records`, which validates;
every other read -- ``fetch_records``, ``fetch_region_values``,
``fetch_region_weighted_values``, ``fetch_position_scores`` -- does not.
These tests pin both halves of that split, because either half alone is
quietly undoable: a door that stops validating still serves every read, and
a read path that starts validating again still passes the scan's tests.
"""
# pylint: disable=C0116,W0212,W0621
import pathlib
from collections.abc import Iterator

import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    FragmentScore,
    PositionScore,
    build_allele_score_from_resource,
    build_fragment_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.histogram import NumberHistogramConfig
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing.builders import (
    a_fragment_score,
    a_grr,
    a_position_score,
    an_allele_score,
)

TOUCHING_RECORDS = """
    chrom  pos_begin  pos_end  s
    chr1   1          5        0.1
    chr1   5          9        0.2
"""


def _hist_conf() -> NumberHistogramConfig:
    return NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 1},
        "number_of_bins": 10,
        "x_log_scale": False,
        "y_log_scale": False,
    })


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
    tmp_path: pathlib.Path, resource_id: str, data: str,
) -> PositionScore:
    score = build_position_score_from_resource(
        _position_score_resource(tmp_path, resource_id, data))
    score.open()
    return score


def test_the_scan_door_refuses_a_position_score_whose_records_touch(
    tmp_path: pathlib.Path,
) -> None:
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.scan_records("chr1", 1, 10))

    message = str(excinfo.value)
    assert "<touching>" in message
    assert "chr1:5" in message
    assert "at most one record per position" in message


def test_reading_a_position_score_whose_records_touch_yields_them_all(
    tmp_path: pathlib.Path,
) -> None:
    # The read path's ordering guard is GONE: the region read hands back both
    # records, clipped, in table order.
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    assert list(score.fetch_region_values("chr1", 1, 10)) == [
        (1, 5, [0.1]),
        (5, 9, [0.2]),
    ]


def test_weighting_a_position_score_whose_records_touch_yields_them_all(
    tmp_path: pathlib.Path,
) -> None:
    # The weighted read is built on the region read, so it is the same guard;
    # it gets its own test because a guard restored in either place would be
    # invisible to the other's.
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    assert list(score.fetch_region_weighted_values("chr1", 1, 10)) == [
        ([0.1], 5),
        ([0.2], 5),
    ]


def test_fetching_records_where_a_position_score_repeats_itself_yields_both(
    tmp_path: pathlib.Path,
) -> None:
    score = _position_score(tmp_path, "repeated", """
        chrom  pos_begin  pos_end  s
        chr1   10         10       0.1
        chr1   10         10       0.2
    """)

    records = list(score.fetch_records("chr1", 10, 10))

    assert len(records) == 2


def test_the_point_read_of_a_repeated_position_answers_from_the_first_record(
    tmp_path: pathlib.Path,
) -> None:
    # "Several records at this position" is the same rule on the same path,
    # so the point read stops refusing too, and answers from the first record
    # the table holds -- which is what a reader of a repaired resource gets
    # anyway, there being only one.
    score = _position_score(tmp_path, "repeated", """
        chrom  pos_begin  pos_end  s
        chr1   10         10       0.1
        chr1   10         10       0.2
    """)

    assert score.fetch_position_scores("chr1", 10) == [0.1]


@pytest.mark.parametrize("statistics_pass,payload", [
    ("_do_min_max", ["s"]),
    ("_do_histogram", {"s": _hist_conf()}),
])
def test_both_per_record_passes_refuse_a_malformed_position_score(
    tmp_path: pathlib.Path,
    statistics_pass: str,
    payload: object,
) -> None:
    # Both passes, because each reads the region for itself: a door only one
    # of them goes through leaves the other scanning unvalidated data.
    resource = _position_score_resource(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        getattr(GenomicScoreImplementation, statistics_pass)(
            resource, payload, "chr1", 1, 10)

    assert "at most one record per position" in str(excinfo.value)


def _fragment_score_reading_backwards(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> FragmentScore:
    """A fragment score whose contig's records arrive out of order.

    The records are handed in rather than read: no backend can deliver a
    contig backwards -- tabix refuses to index an unsorted file and the
    in-memory backend sorts each contig as it loads it -- so the rule is
    reachable only from a backend that has yet to exist.
    """
    resource = (
        a_grr()
        .with_resource(
            "backwards",
            a_fragment_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  pos_end  s
                chr1   10         19       0.1
                chr1   20         29       0.2
            """))
        .build_repo(tmp_path)
        .get_resource("backwards")
    )
    score = build_fragment_score_from_resource(resource)
    score.open()

    def out_of_order(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 20, 29, None, None, ("chr1", "20", "29", "0.1"))
        yield ("chr1", 10, 19, None, None, ("chr1", "10", "19", "0.2"))

    monkeypatch.setattr(score, "fetch_records", out_of_order)
    return score


def test_the_door_still_validates_a_fragment_score(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fragment score has no validator of its own yet (gain#590), so the
    # base rule -- a record must not begin before the one before it -- is
    # what keeps it validated during the scan.
    score = _fragment_score_reading_backwards(tmp_path, monkeypatch)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.scan_records("chr1", 1, 30, ["s"]))

    message = str(excinfo.value)
    assert "<backwards>" in message
    assert "chr1:10" in message
    assert "chr1:20" in message
    assert "must not move backwards" in message


def test_reading_that_same_fragment_score_raises_nothing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other half of the split, for the kind the BASE validator covers.
    score = _fragment_score_reading_backwards(tmp_path, monkeypatch)

    assert len(list(score.fetch_region_values("chr1", 1, 30, ["s"]))) == 2


def test_the_region_size_zero_path_refuses_a_malformed_position_score(
    tmp_path: pathlib.Path,
) -> None:
    # ``--region-size 0`` is a task of its own, and it reaches the per-record
    # passes with both bounds None -- the shape no bulk scan serves.  Routing
    # the two passes is EXPECTED to cover it; this is the check that it does.
    resource = _position_score_resource(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        GenomicScoreImplementation._do_noregion_histograms(resource)

    assert "at most one record per position" in str(excinfo.value)


def test_the_door_yields_exactly_what_the_read_yields(
    tmp_path: pathlib.Path,
) -> None:
    # Clipping is preserved to the base pair: a record straddling either edge
    # of the queried region reaches the scan trimmed exactly as it reaches a
    # reader, so no histogram weight and no min/max range moves with gain#588.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
        chr1   21         30       0.3
    """)

    assert list(score.scan_records("chr1", 5, 25)) == \
        list(score.fetch_region_values("chr1", 5, 25))
    assert list(score.scan_records("chr1", 5, 25)) == [
        (5, 10, [0.1]),
        (11, 20, [0.2]),
        (21, 25, [0.3]),
    ]


def test_the_door_yields_what_an_allele_score_reads_back(
    tmp_path: pathlib.Path,
) -> None:
    # Each kind normalizes its records its own way -- an allele score
    # collapses a record to the point it sits at -- and the door yields that,
    # not some shape of its own.  Otherwise what the scan measures would
    # depend on which door it came through.
    resource = (
        a_grr()
        .with_resource(
            "alleles",
            an_allele_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  reference  alternative  s
                chr1   10         A          G            0.1
                chr1   10         A          C            0.2
                chr1   16         C          T            0.3
            """))
        .build_repo(tmp_path)
        .get_resource("alleles")
    )
    score = build_allele_score_from_resource(resource)
    score.open()

    assert list(score.scan_records("chr1", 1, 20, ["s"])) == \
        list(score.fetch_region_values("chr1", 1, 20, ["s"]))
    # Two records share position 10 -- which is what an allele score IS --
    # and the in-memory backend hands them back ordered by their alleles.
    assert list(score.scan_records("chr1", 1, 20, ["s"])) == [
        (10, 10, [0.2]),
        (10, 10, [0.1]),
        (16, 16, [0.3]),
    ]


def test_the_position_rule_compares_raw_spans_not_clipped_ones(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two records overlapping at 50..100, queried from 120: the region read
    # drops the first one entirely (it ends before the query) and clips the
    # second, so nothing survives for a clipped comparison to reject.  The
    # raw spans still hold the overlap, and the door refuses on them.
    score = _position_score(tmp_path, "overlapping", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
    """)

    def overlapping(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 1, 100, None, None, ("chr1", "1", "100", "0.1"))
        yield ("chr1", 50, 150, None, None, ("chr1", "50", "150", "0.2"))

    monkeypatch.setattr(score, "fetch_records", overlapping)

    assert len(list(score.fetch_region_values("chr1", 120, 200, ["s"]))) == 1
    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.scan_records("chr1", 120, 200, ["s"]))

    assert "chr1:50" in str(excinfo.value)


def test_the_validator_hands_back_every_record_it_was_given(
    tmp_path: pathlib.Path,
) -> None:
    # A transducer, not a filter and not a sink: what the scan reads is what
    # the table yielded, in order, or nothing at all.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
    """)
    records: list[Record] = [
        ("chr1", 1, 10, None, None, ("chr1", "1", "10", "0.1")),
        ("chr1", 11, 20, None, None, ("chr1", "11", "20", "0.2")),
        ("chr2", 1, 10, None, None, ("chr2", "1", "10", "0.3")),
    ]

    assert list(score.validate_records(iter(records))) == records


def test_the_door_reads_the_region_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validation rides the scan's read rather than paying for one of its own.
    # A door that validated by re-reading would pass every test above.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
    """)
    reads = []
    fetch_records = score.fetch_records

    def counted(*args: object, **kwargs: object) -> Iterator[Record]:
        reads.append(args)
        return fetch_records(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(score, "fetch_records", counted)

    assert len(list(score.scan_records("chr1", 1, 20))) == 2
    assert len(reads) == 1
