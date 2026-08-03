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

import pytest
from gain.genomic_resources.genomic_position_table.record import ALT
from gain.genomic_resources.genomic_scores import AlleleScore
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
