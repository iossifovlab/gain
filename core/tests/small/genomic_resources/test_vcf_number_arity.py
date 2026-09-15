"""A VCF INFO field whose value count does not match its ALT alleles.

``Number=A`` declares one value per ALT allele and ``Number=R`` one per
allele *including the reference*, so on a well-formed record the number of
values a field carries is fixed by the record's ALT column.  A row that
breaks that is malformed under the VCF spec -- but nothing rejects it, so
the read path has to answer something.

These tests pin both halves of that answer (#289):

* the **value**: an allele with no value of its own reads ``None``, the same
  null an absent INFO key already yields, rather than raising
  ``IndexError`` out of the middle of a scan;
* the **diagnostic**: a mismatched value count is a broken resource, so it
  is logged -- once per table, not once per line.
"""
from __future__ import annotations

import logging
import pathlib
import textwrap

import pytest
from gain.genomic_resources.aggregators import ScoreAggregationQuery
from gain.genomic_resources.genomic_position_table.record import ALT
from gain.genomic_resources.genomic_scores import (
    AlleleScore,
    build_score_from_resource,
)
from gain.genomic_resources.histogram import (
    NumberHistogram,
    NumberHistogramConfig,
)
from gain.genomic_resources.implementations.genomic_scores_impl import scan
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    setup_directories,
    setup_vcf,
)
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_vcf_info_score,
)


def _open_vcf_score(tmp_path: pathlib.Path, data: str) -> AlleleScore:
    """Realize a one-resource GRR from VCF text and open its allele score."""
    repo = (
        a_grr()
        .with_resource("vcf", a_vcf_info_score().with_data(data))
        .build_repo(tmp_path)
    )
    return AlleleScore(repo.get_resource("vcf")).open()


def test_number_a_short_tuple_reads_null_for_the_allele_past_its_end(
    tmp_path: pathlib.Path,
) -> None:
    """A **Number=A** field with fewer values than ALT alleles reads null.

    ``ALT=T,G`` with a single value is malformed -- Number=A declares one
    value per ALT allele -- and it used to abort the whole fetch: the second
    allele indexed the one-element tuple at offset 1 and the read died with
    ``IndexError: tuple index out of range``, outside the ``try`` that turns
    a bad cell into a logged null.  The rule #256 settled answers it with no
    special case: no applicable per-ALT value -> the score is null.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=d11
""")
    with score:
        results = [
            (record[ALT], score.get_score_value_from_record(record, "S"))
            for record in score.fetch_records("chr1", 5, 5)
        ]

    assert results == [("T", "d11"), ("G", None)]


def test_number_r_short_tuple_reads_null_for_the_allele_past_its_end(
    tmp_path: pathlib.Path,
) -> None:
    """A **Number=R** field with too few values reads null, and does not raise.

    ``Number=R`` carries one value per allele *including the reference*, so a
    record with two ALT alleles must carry three; this one carries two.  The
    exposure is the same as Number=A's one offset along -- an ALT allele
    reads at ``allele_index + 1`` -- so the second allele indexed past the
    end and raised.  It reads the same null.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=R,Number=R,Type=String,Description="ref plus one per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      R=r00,r01
""")
    with score:
        results = [
            (record[ALT], score.get_score_value_from_record(record, "R"))
            for record in score.fetch_records("chr1", 5, 5)
        ]

    assert results == [("T", "r01"), ("G", None)]


def test_a_short_number_a_tuple_warns_once_per_table_not_once_per_line(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The malformed shape is reported -- once for the table, not per row.

    A silent null would hide a data-quality problem the resource author is
    the only one who can fix, so a value count that does not match the
    record's alleles is logged.  It is logged ONCE: this is the per-record
    score read, the hot path #237 exists to keep cheap, and a table whose
    field is malformed on one row is usually malformed on all of them --
    three rows here would otherwise be three (or six, one per allele)
    identical lines saying the same thing about the same resource.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=d11
chr1   6   .  A   T,G  .    .      S=d21
chr1   7   .  A   T,G  .    .      S=d31
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, "S")
            for record in score.fetch_records("chr1", 5, 7)
        ]

    assert values == ["d11", None, "d21", None, "d31", None]
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    # The report has to be actionable on its own: which field, at what
    # number, and how the counts disagree.
    assert "INFO field S (Number=A)" in message
    assert "1 value(s) for 2 allele(s)" in message


def test_number_a_over_long_tuple_reads_its_own_allele_and_warns(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A **Number=A** field with MORE values than ALT alleles is malformed too.

    ``ALT=T`` with two values is the mirror of the short tuple and equally
    broken, but lower-stakes: nothing indexes past the end, so the extra
    value is simply never selected by any allele.  Silence would be the easy
    answer -- there is no crash and no wrong value to point at -- and it is
    the wrong one: a value that no allele can ever read is a value the author
    believes is being annotated.  So it reads its own allele, drops the rest,
    and says so through the same once-per-table channel.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T    .    .      S=d11,d12
""")
    with caplog.at_level(logging.WARNING), score:
        results = [
            (record[ALT], score.get_score_value_from_record(record, "S"))
            for record in score.fetch_records("chr1", 5, 5)
        ]

    assert results == [("T", "d11")]
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "INFO field S (Number=A)" in warnings[0].getMessage()


def test_number_r_over_long_tuple_reads_its_own_allele_and_warns(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """**Number=R** with more values than alleles: same answer as Number=A.

    One ALT allele means two values, the reference's and its own; this row
    carries three.  The allele still reads its own offset (``allele_index +
    1``) and the surplus is dropped -- and reported, because a Number=R field
    is exactly as capable of being miscounted as a Number=A one.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=R,Number=R,Type=String,Description="ref plus one per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T    .    .      R=r00,r01,r02
""")
    with caplog.at_level(logging.WARNING), score:
        results = [
            (record[ALT], score.get_score_value_from_record(record, "R"))
            for record in score.fetch_records("chr1", 5, 5)
        ]

    assert results == [("T", "r01")]
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "INFO field R (Number=R)" in warnings[0].getMessage()


def test_the_warning_is_silenced_per_table_and_not_process_wide(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Two malformed tables warn twice: "once" is scoped to the table.

    The flag that suppresses the repeat lives on the score DEFINITION, which
    is built per ``GenomicScore``, so it silences the second ROW of a table
    and never the second RESOURCE.  A process-level flag would read the same
    in the single-table tests above and quietly swallow every resource after
    the first -- exactly the report an annotation run over a whole GRR needs
    most.
    """
    first = _open_vcf_score(tmp_path / "first", """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=d11
""")
    second = _open_vcf_score(tmp_path / "second", """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   5   .  A   T,G  .    .      S=e11
""")
    with caplog.at_level(logging.WARNING):
        for score in (first, second):
            with score:
                for record in score.fetch_records("chr1", 5, 5):
                    score.get_score_value_from_record(record, "S")

    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 2


def test_an_alt_less_number_a_record_still_nulls_without_a_warning(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The ALT-less Number=A record is #256's, and is left exactly as it was.

    ``ALT=.`` means zero ALT alleles, so ``S=d01`` on such a row is malformed
    on the same arithmetic as everything above -- one value, no alleles.  It
    is nonetheless NOT this check's business: #256 decided that row on
    purpose (no ALT allele -> no applicable value -> null), and the guard it
    installed returns before the arity check is ever reached.  Reporting it
    here would re-open a settled decision through the log, so the guard stays
    first and this row stays silent.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   2   .  A   .    .    .      S=d01
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, "S")
            for record in score.fetch_records("chr1", 2, 2)
        ]

    assert values == [None]
    assert [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ] == []


def test_a_well_formed_table_reads_every_allele_and_says_nothing(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The check must not cry wolf over correctly-counted fields.

    Every row here is well-formed at both numbers -- one ``S`` per ALT, one
    ``R`` per ALT plus the reference, across a single-allele row, a
    two-allele row and an ALT-less one -- so the values come back unchanged
    and nothing is logged.  A check that fired here would put a warning on
    every legitimate multi-allelic VCF in the tree.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=S,Number=A,Type=String,Description="one value per ALT">
##INFO=<ID=R,Number=R,Type=String,Description="ref plus one per ALT">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   2   .  A   .    .    .      R=r00
chr1   5   .  A   T    .    .      S=d11;R=r10,r11
chr1   9   .  A   T,G  .    .      S=d21,d22;R=r20,r21,r22
""")
    with caplog.at_level(logging.WARNING), score:
        results = [
            (record[ALT],
             score.get_score_value_from_record(record, "S"),
             score.get_score_value_from_record(record, "R"))
            for record in score.fetch_records("chr1", 2, 9)
        ]

    assert results == [
        (None, None, "r00"),
        ("T", "d11", "r11"),
        ("T", "d21", "r21"),
        ("G", "d22", "r22"),
    ]
    assert [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ] == []


# ---------------------------------------------------------------------------
# A declared-SCALAR field (Number=1) whose row carries more than one value
# (#1257).  htslib does not enforce a header's Number on read, so pysam hands
# the tuple over as-is; the score layer dispatches on the DECLARED number and
# used to pass that tuple through as the score value, which no ScoreValue
# admits.  The rule is the one #289 settled for A/R: the row is malformed, it
# reads null, and it is reported once per table.
# ---------------------------------------------------------------------------


def test_a_number_1_row_carrying_two_values_reads_null(
    tmp_path: pathlib.Path,
) -> None:
    """A **Number=1** field handed a tuple reads ``None``, not the tuple.

    ``SC=2.5,3.5`` on a field the header calls ``Number=1`` decodes to
    ``(2.5, 3.5)``.  Before #1257 that tuple WAS the score value: a
    ``Number=1`` field has no parser (pysam is trusted to have decoded a
    scalar), so nothing between the INFO lookup and the consumer touched it,
    and a score declared ``float`` handed a ``tuple`` to annotation output,
    to the aggregators (``max`` raised ``TypeError``, ``concatenate``
    flattened it) and to the statistics build (``ValueError``).  The
    neighbouring well-formed rows read their scalars unchanged.

    ``Type=String`` is pinned alongside because text is where the leak hid
    best: ``('a', 'b')`` is a legal-looking value to a consumer that does
    not check types.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
##INFO=<ID=SS,Number=1,Type=String,Description="declared scalar text">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   3   .  A   T    .    .      SC=1.5;SS=a
chr1   6   .  A   T    .    .      SC=2.5,3.5;SS=a,b
chr1   9   .  A   T    .    .      SC=4.5;SS=c
""")
    with score:
        values = [
            (score.get_score_value_from_record(record, "SC"),
             score.get_score_value_from_record(record, "SS"))
            for record in score.fetch_records("chr1", 3, 9)
        ]

    assert values == [(1.5, "a"), (None, None), (4.5, "c")]


def test_a_number_1_tuple_is_reported_once_per_field_per_table(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The refused row is reported -- once per FIELD, not per row.

    Same reasoning as the per-allele report: a field wrong on one row is
    normally wrong on every row, and this is the per-record read #237 keeps
    cheap.  Two malformed rows of ``SC`` are one line; ``OTHER``, malformed
    on its own row, gets its own line, because the flag is per definition.
    The message has to be actionable on its own -- which field, at what
    declared number, how many values arrived, and the row to open.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
##INFO=<ID=OTHER,Number=1,Type=Integer,Description="another scalar">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   3   .  A   T    .    .      SC=1.5;OTHER=1
chr1   6   .  A   T    .    .      SC=2.5,3.5;OTHER=2
chr1   9   .  A   T    .    .      SC=4.5,5.5,6.5;OTHER=3,4
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            (score.get_score_value_from_record(record, "SC"),
             score.get_score_value_from_record(record, "OTHER"))
            for record in score.fetch_records("chr1", 3, 9)
        ]

    assert values == [(1.5, 1), (None, 2), (None, None)]
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(messages) == 2
    assert "INFO field SC (Number=1) of chr1:6 carries 2 value(s)" \
        in messages[0]
    assert "reads as null" in messages[0]
    assert "INFO field OTHER (Number=1) of chr1:9 carries 2 value(s)" \
        in messages[1]


def test_a_number_1_row_with_a_trailing_empty_element_is_refused_as_arity(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """``SS=a,`` is a two-element tuple, and the arity check sees it first.

    pysam decodes the trailing comma as ``('a', None)``.  The empty-element
    drop (#630) exists for multi-valued fields, where an empty element is
    one bad element among good ones; on a declared-scalar field the row is
    malformed by COUNT before any element is looked at, and before #1257
    the drop ran first and handed on ``('a',)`` -- a one-element tuple as
    the score value, plus an empty-element warning about a field that is
    not multi-valued.  The check is decided on the tuple as pysam hands it
    over, as the per-allele check already is, so the row reads null and
    only the arity report fires.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=SS,Number=1,Type=String,Description="declared scalar text">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   6   .  A   T    .    .      SS=a,
""")
    with caplog.at_level(logging.WARNING), score:
        values = [
            score.get_score_value_from_record(record, "SS")
            for record in score.fetch_records("chr1", 6, 6)
        ]

    assert values == [None]
    messages = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(messages) == 1
    assert "INFO field SS (Number=1) of chr1:6 carries 2 value(s)" \
        in messages[0]
    assert "empty element" not in messages[0]


def _open_typed_vcf_score(
    tmp_path: pathlib.Path, data: str, scores_block: str,
) -> AlleleScore:
    """Open an allele score over ``data`` with the ``scores:`` block given.

    Hand-rolled because ``a_vcf_info_score()`` emits no ``scores:`` block,
    and the block is what the config-typed test is about.  One of the
    copies gain#1290 exists to fold into the builder.
    """
    setup_directories(tmp_path, {
        "genomic_resource.yaml": textwrap.dedent("""
            type: allele_score
            table:
                filename: data.vcf.gz
        """) + scores_block,
    })
    setup_vcf(tmp_path / "data.vcf.gz", data)
    score = build_score_from_resource(build_filesystem_test_resource(tmp_path))
    assert isinstance(score, AlleleScore)
    return score.open()


def test_a_config_typed_number_1_field_refuses_the_row_before_its_parser(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``scores:`` entry with a ``type:`` gets the same null, at the door.

    Before #1257 the tuple reached the config's parser, and what happened
    next depended on the type stated: ``float((2.5, 3.5))`` raised, so the
    row read null under one ``unable to parse value`` traceback PER ROW;
    ``str(('a', 'b'))`` does not raise, so the row read the repr
    ``"('a', 'b')"`` as a legal-looking value with no log at all.  Refusing
    the row before any parser sees it gives both the null and the
    once-per-table arity report, and nothing else.
    """
    score = _open_typed_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
##INFO=<ID=SS,Number=1,Type=String,Description="declared scalar text">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   3   .  A   T    .    .      SC=1.5;SS=a
chr1   6   .  A   T    .    .      SC=2.5,3.5;SS=a,b
""", textwrap.dedent("""
            scores:
            - id: SC
              name: SC
              type: float
            - id: SS
              name: SS
              type: str
    """))
    with caplog.at_level(logging.WARNING), score:
        values = [
            (score.get_score_value_from_record(record, "SC"),
             score.get_score_value_from_record(record, "SS"))
            for record in score.fetch_records("chr1", 3, 6)
        ]

    assert values == [(1.5, "a"), (None, None)]
    messages = [record.getMessage() for record in caplog.records]
    assert not any("unable to parse" in message for message in messages)
    assert sorted(
        message.split(" of ")[0] for message in messages
        if "carries 2 value(s)" in message
    ) == ["INFO field SC (Number=1)", "INFO field SS (Number=1)"]


def test_a_refused_number_1_row_is_inert_to_the_region_aggregators(
    tmp_path: pathlib.Path,
) -> None:
    """Region aggregation folds the well-formed rows and never sees the tuple.

    The two default aggregators failed in opposite ways on the leaked tuple:
    ``max`` over the ``float`` field raised ``TypeError: '>' not supported
    between instances of 'float' and 'tuple'`` and took the whole fetch --
    the well-formed rows included -- down with it; ``concatenate`` over the
    ``str`` field FLATTENED the tuple's elements into its list, four values
    from three rows, no error, no log.  A refused row reads null, and every
    aggregator already skips a null.
    """
    score = _open_vcf_score(tmp_path, """
##fileformat=VCFv4.1
##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
##INFO=<ID=SS,Number=1,Type=String,Description="declared scalar text">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   3   .  A   T    .    .      SC=1.5;SS=a
chr1   6   .  A   T    .    .      SC=2.5,3.5;SS=a,b
chr1   9   .  A   T    .    .      SC=4.5;SS=c
""")
    with score:
        folded = score.get_allele_scores_in_region_agg(
            "chr1", 1, 10,
            queries=[
                ScoreAggregationQuery("SC", None),
                ScoreAggregationQuery("SS", None),
            ])

    assert folded is not None
    assert folded.values == (4.5, ["a", "c"])


def test_a_refused_number_1_row_leaves_the_statistics_passes_folding(
    tmp_path: pathlib.Path,
) -> None:
    """The min/max and histogram passes fold the well-formed rows.

    The leaked tuple was the one shape the statistics build could not
    contain: ``np.isnan((2.5, 3.5))`` is an array, its ``bool`` raises
    ``ValueError``, and neither reducer's ``except TypeError`` caught it,
    so ONE malformed row cost the resource its whole statistics build
    (gain#1337 widens that catch; this pins that the door is shut
    regardless).  Both scores of the resource fold, the malformed row
    contributing nothing, like the null it is.
    """
    resource = a_vcf_info_score().with_data("""
##fileformat=VCFv4.1
##INFO=<ID=SC,Number=1,Type=Float,Description="declared scalar">
##INFO=<ID=OK,Number=1,Type=Float,Description="well-formed neighbour">
#CHROM POS ID REF ALT QUAL FILTER INFO
chr1   3   .  A   T    .    .      SC=1.5;OK=10
chr1   6   .  A   T    .    .      SC=2.5,3.5;OK=20
chr1   9   .  A   T    .    .      SC=4.5;OK=30
""").build_resource(tmp_path)
    hist_conf = NumberHistogramConfig.from_dict({
        "type": "number",
        "view_range": {"min": 0, "max": 40},
        "number_of_bins": 4,
        "x_log_scale": False,
        "y_log_scale": False,
    })

    min_max = scan.do_min_max(resource, ["SC", "OK"], "chr1", 1, 10)
    histograms = scan.do_histogram(
        resource, {"SC": hist_conf, "OK": hist_conf}, "chr1", 1, 10)

    assert (min_max["SC"].min, min_max["SC"].max) == (1.5, 4.5)
    assert (min_max["OK"].min, min_max["OK"].max) == (10.0, 30.0)
    assert isinstance(histograms["SC"], NumberHistogram)
    assert isinstance(histograms["OK"], NumberHistogram)
    assert list(histograms["SC"].bars) == [2, 0, 0, 0]
    assert list(histograms["OK"].bars) == [0, 1, 1, 1]
