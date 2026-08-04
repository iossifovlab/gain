"""The statistics scan's own way in to a region (gain#588, ADR 0008).

The scan reads a region as ``region_values_from_records`` over
``validate_records(fetch_records(...))``; every plain read -- ``fetch_records``,
``fetch_region_values``, ``fetch_region_weighted_values``,
``fetch_position_scores`` -- is the same transform over the same records with
that middle link left out, and checks nothing.  These tests pin both halves of
the split, because either half alone is quietly undoable: a scan that stops
composing the validator still serves every read, and a read that starts
validating again still passes the scan's tests.
"""
# pylint: disable=C0116,W0212,W0621
import math
import pathlib
from collections.abc import Iterator

import pytest
from gain.genomic_resources.genomic_position_table.record import Record
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    FragmentScore,
    PositionScore,
    build_allele_score_from_resource,
    build_fragment_score_from_resource,
    build_position_score_from_resource,
)
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import (
    GenomicScoreImplementation,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_tabix,
)
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


def test_the_region_read_is_the_transform_over_the_record_stream(
    tmp_path: pathlib.Path,
) -> None:
    # The identity the split rests on: reading a region IS extracting the
    # kind's score values from that region's records.  The scan reads the
    # same way with one extra link -- ``validate_records`` -- composed in
    # front, which is only possible while the transform is a function OF a
    # record stream rather than something wrapped around the fetch.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
        chr1   21         30       0.3
    """)

    assert list(score.fetch_region_values("chr1", 5, 25)) == list(
        score.region_values_from_records(
            score.fetch_records("chr1", 5, 25), "chr1", 5, 25))


def test_the_scan_refuses_a_position_score_whose_records_touch(
    tmp_path: pathlib.Path,
) -> None:
    score = _position_score(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_records(score.fetch_records("chr1", 1, 10)))

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


def test_a_region_split_between_two_touching_records_still_refuses_them(
    tmp_path: pathlib.Path,
) -> None:
    # The scan cuts a contig into regions, so the pair that breaks the rule
    # can be handed to the passes in two pieces.  A record straddling the cut
    # is served to the later region too -- that is what makes the verdict a
    # property of the resource rather than of ``--region-size``.
    #
    # Driven at the per-record pass directly: the region-size parametrisation
    # in test_cli_repair.py goes through the CLI, where a float score is
    # bulk-eligible and the vectorized guard answers instead, so it does not
    # reach the rule this slice added.
    resource = _position_score_resource(tmp_path, "touching", TOUCHING_RECORDS)

    # The first region holds only the first record: nothing to compare it to.
    assert GenomicScoreImplementation._do_min_max(
        resource, ["s"], "chr1", 1, 4)["s"].max == pytest.approx(0.1)

    # The second is where they meet -- the straddling record arrives with it.
    with pytest.raises(MalformedResourceError) as excinfo:
        GenomicScoreImplementation._do_min_max(resource, ["s"], "chr1", 5, 9)

    assert "at most one record per position" in str(excinfo.value)


@pytest.mark.parametrize("statistics_pass,payload", [
    ("_do_min_max", ["s"]),
    ("_do_histogram", {"s": _hist_conf()}),
])
def test_both_per_record_passes_refuse_a_malformed_position_score(
    tmp_path: pathlib.Path,
    statistics_pass: str,
    payload: object,
) -> None:
    # Both passes, because each reads the region for itself: a helper only
    # one of them reads through leaves the other scanning unvalidated data.
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


def _allele_score_reading_backwards(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> AlleleScore:
    """An allele score whose contig's records arrive out of order.

    Handed in for the same reason as the fragment one: no backend delivers a
    contig backwards, so the rule is reachable only this way.
    """
    resource = (
        a_grr()
        .with_resource(
            "backwards_alleles",
            an_allele_score()
            .with_score("s", "float")
            .with_data("""
                chrom  pos_begin  reference  alternative  s
                chr1   10         A          G            0.1
                chr1   20         C          T            0.2
            """))
        .build_repo(tmp_path)
        .get_resource("backwards_alleles")
    )
    score = build_allele_score_from_resource(resource)
    score.open()

    def out_of_order(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 20, 20, "C", "T", ("chr1", "20", "C", "T", "0.2"))
        yield ("chr1", 10, 10, "A", "G", ("chr1", "10", "A", "G", "0.1"))

    monkeypatch.setattr(score, "fetch_records", out_of_order)
    return score


def test_the_scan_still_validates_a_fragment_score(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A fragment score has no validator of its own yet (gain#590), so the
    # base rule -- a record must not begin before the one before it -- is
    # what keeps it validated during the scan.
    #
    # Driven through the scan's own composition rather than by calling
    # ``validate_records`` by hand: what criterion 11 claims is that the SCAN
    # validates a non-position kind, and calling the validator directly would
    # hold even if ``_scan_region`` had stopped composing it.
    score = _fragment_score_reading_backwards(tmp_path, monkeypatch)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(GenomicScoreImplementation._scan_region(
            score, "chr1", 1, 30, ["s"]))

    message = str(excinfo.value)
    assert "<backwards>" in message
    assert "chr1:10" in message
    assert "chr1:20" in message
    assert "must not move backwards" in message


def test_the_scan_still_validates_an_allele_score(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other kind the base validator carries until gain#589 gives it one
    # of its own.  An allele score also still guards its READ path, so the
    # message is what says which one fired: the base's wording is generic
    # ("this score's records"), the read guard's names the kind.
    score = _allele_score_reading_backwards(tmp_path, monkeypatch)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(GenomicScoreImplementation._scan_region(
            score, "chr1", 1, 30, ["s"]))

    message = str(excinfo.value)
    assert "<backwards_alleles>" in message
    assert "this score's records must not move backwards" in message


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
    # the two passes through one helper is EXPECTED to cover it; this is the
    # check that it does.
    resource = _position_score_resource(tmp_path, "touching", TOUCHING_RECORDS)

    with pytest.raises(MalformedResourceError) as excinfo:
        GenomicScoreImplementation._do_noregion_histograms(resource)

    assert "at most one record per position" in str(excinfo.value)


def test_a_region_read_clips_every_record_to_the_query(
    tmp_path: pathlib.Path,
) -> None:
    # Clipping is preserved to the base pair: a record straddling either edge
    # of the queried region reaches the scan trimmed exactly as it reaches a
    # reader -- one transform serves both -- so no histogram weight and no
    # min/max range moves with gain#588.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
        chr1   21         30       0.3
    """)

    assert list(score.fetch_region_values("chr1", 5, 25)) == [
        (5, 10, [0.1]),
        (11, 20, [0.2]),
        (21, 25, [0.3]),
    ]


def _end_column_mismatch_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """A position score whose INDEXED end is wider than its configured one.

    The tabix index is built over column 2 while ``pos_end`` reads column 3,
    which is the gain#553 config/index mismatch -- unfixed on master, and not
    expressible through the builders.  A region query is answered by the
    index, so the record at ``chr1:1`` (indexed end 1000) comes back for a
    query far past its configured end of 10.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": """
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
                  index: 3
            scores:
            - id: s
              index: 4
              type: float
        """,
    })
    setup_tabix(
        tmp_path / "data.txt.gz",
        """
        chr1  1    1000  10   0.1
        chr1  500  600   600  0.9
        """,
        seq_col=0, start_col=1, end_col=2)
    return build_filesystem_test_resource(tmp_path)


def test_both_scan_paths_measure_an_out_of_region_record_alike(
    tmp_path: pathlib.Path,
) -> None:
    # A record can reach the score layer without overlapping the region it was
    # asked for: the tabix index answers the query and does not know the
    # configured end (gain#553).  Both scan paths drop it on the same edge --
    # the per-record one in ``_clipped_score_values``, the vectorized one in
    # ``_clip_keep_guard``'s ``pos_end >= start`` mask -- so what a resource
    # measures cannot depend on which path it was eligible for.
    #
    # This is the pin for that equivalence, and it discriminates: with the
    # per-record skip removed, ``_do_min_max`` folds 0.1 into the range while
    # the bulk pass reports nan, and ``_do_histogram`` counts an INVERTED
    # span, whose width as a weight is negative, into a bar.
    resource = _end_column_mismatch_resource(tmp_path)

    per_record_hist = GenomicScoreImplementation._do_histogram(
        resource, {"s": _hist_conf()}, "chr1", 100, 200)
    bulk_hist = GenomicScoreImplementation._do_histogram_bulk(
        resource, {"s": _hist_conf()}, "chr1", 100, 200)
    per_record_mm = GenomicScoreImplementation._do_min_max(
        resource, ["s"], "chr1", 100, 200)
    bulk_mm = GenomicScoreImplementation._do_min_max_bulk(
        resource, ["s"], "chr1", 100, 200)

    per_record_bars = per_record_hist["s"]
    bulk_bars = bulk_hist["s"]
    assert isinstance(per_record_bars, NumberHistogram)
    assert isinstance(bulk_bars, NumberHistogram)

    assert list(per_record_bars.bars) == list(bulk_bars.bars)
    # Nothing overlapped the region, so nothing was counted -- and no bar is
    # negative, which is what counting an inverted span would produce.
    assert list(per_record_bars.bars) == [0] * 10

    assert math.isnan(per_record_mm["s"].min) is math.isnan(bulk_mm["s"].min)
    assert math.isnan(per_record_mm["s"].max) is math.isnan(bulk_mm["s"].max)
    # Both report "no value seen" rather than one of them reporting 0.1.
    assert math.isnan(per_record_mm["s"].min)
    assert math.isnan(per_record_mm["s"].max)


def test_the_scan_measures_a_well_formed_region_exactly_as_a_reader_reads_it(
    tmp_path: pathlib.Path,
) -> None:
    # The extra link refuses or it passes the records straight on: it is not
    # allowed to change the spans, because a histogram weight and a min/max
    # range are computed from them.
    score = _position_score(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
        chr1   21         30       0.3
    """)

    assert list(GenomicScoreImplementation._scan_region(
        score, "chr1", 5, 25, ["s"])) == \
        list(score.fetch_region_values("chr1", 5, 25, ["s"]))


def test_an_allele_score_reads_each_record_as_the_point_it_sits_at(
    tmp_path: pathlib.Path,
) -> None:
    # Each kind reads its records its own way -- an allele score collapses a
    # record to the point it sits at -- and states that ONCE, in
    # ``region_values_from_records``.  So the scan and a reader cannot
    # measure different things.
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

    assert list(score.region_values_from_records(
        score.fetch_records("chr1", 1, 20), "chr1", 1, 20, ["s"])) == \
        list(score.fetch_region_values("chr1", 1, 20, ["s"]))
    # Two records share position 10 -- which is what an allele score IS --
    # and the in-memory backend hands them back ordered by their alleles.
    assert list(score.fetch_region_values("chr1", 1, 20, ["s"])) == [
        (10, 10, [0.2]),
        (10, 10, [0.1]),
        (16, 16, [0.3]),
    ]


def test_the_position_rule_compares_raw_spans_not_clipped_ones(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two records overlapping at 50..100, queried from 120: a clipped
    # comparison sees the first one pulled back to the query's start and the
    # second clipped too, so the overlap it would have to reject is not in
    # what the read yields.  The raw spans still hold it, and the validator
    # sits in front of the transform, where they still exist.
    score = _position_score(tmp_path, "overlapping", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
    """)

    def overlapping(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 1, 100, None, None, ("chr1", "1", "100", "0.1"))
        yield ("chr1", 50, 150, None, None, ("chr1", "50", "150", "0.2"))

    monkeypatch.setattr(score, "fetch_records", overlapping)

    # The read yields ONE record: the first ends at 100, before the query
    # begins, and is dropped rather than clipped to an inverted span.  So
    # what the read hands on holds no overlap at all -- a rule reading these
    # spans could not find one, which is the whole reason the validator sits
    # in front of the transform rather than behind it.
    assert list(score.fetch_region_values("chr1", 120, 200, ["s"])) == [
        (120, 150, [0.2]),
    ]
    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_records(score.fetch_records("chr1", 120, 200)))

    assert "chr1:50" in str(excinfo.value)


def test_the_position_rule_carries_a_zero_end_rather_than_dropping_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The carry is tested with ``prev_end is not None``, not for truthiness.
    # Master's guard read ``if prev_end and left <= prev_end``, which skips
    # the whole check whenever the previous record ended at 0 -- so a pair of
    # records claiming position 0 walked past a rule that exists to refuse
    # exactly that.  Regressing to the truthiness form makes this the one
    # test that fails.
    score = _position_score(tmp_path, "at_zero", """
        chrom  pos_begin  pos_end  s
        chr1   1          5        0.1
    """)

    def at_zero(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 0, 0, None, None, ("chr1", "0", "0", "0.1"))
        yield ("chr1", 0, 0, None, None, ("chr1", "0", "0", "0.2"))

    monkeypatch.setattr(score, "fetch_records", at_zero)

    with pytest.raises(MalformedResourceError) as excinfo:
        list(score.validate_records(score.fetch_records("chr1", 0, 10)))

    assert "at most one record per position" in str(excinfo.value)


def test_the_base_rule_starts_each_contig_afresh(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "Must not begin before the one before it" is a claim about ONE contig.
    # Without the reset, the first record of a new contig is compared against
    # the last of the previous one, and every resource whose second contig
    # starts at a lower position than the first ended at -- which is most of
    # them -- is refused as malformed.
    #
    # A fragment score, because the base validator is what covers it; a
    # position score would take its own override instead.
    score = _fragment_score_reading_backwards(tmp_path, monkeypatch)

    def across_contigs(*_args: object, **_kwargs: object) -> Iterator[Record]:
        yield ("chr1", 100, 109, None, None, ("chr1", "100", "109", "0.1"))
        yield ("chr2", 5, 14, None, None, ("chr2", "5", "14", "0.2"))

    monkeypatch.setattr(score, "fetch_records", across_contigs)

    assert len(list(score.validate_records(
        score.fetch_records("chr1", 1, 200)))) == 2


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


def test_a_statistics_pass_reads_the_region_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validation rides the scan's read rather than paying for one of its own:
    # the validator is a transducer over the very stream the transform
    # consumes.  A scan that validated by re-reading the region would pass
    # every test above.
    resource = _position_score_resource(tmp_path, "well_formed", """
        chrom  pos_begin  pos_end  s
        chr1   1          10       0.1
        chr1   11         20       0.2
    """)
    reads = []
    fetch_records = PositionScore.fetch_records

    def counted(
        self: PositionScore, *args: object, **kwargs: object,
    ) -> Iterator[Record]:
        reads.append(args)
        return fetch_records(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PositionScore, "fetch_records", counted)

    assert GenomicScoreImplementation._do_min_max(
        resource, ["s"], "chr1", 1, 20)["s"].max == 0.2
    assert len(reads) == 1
