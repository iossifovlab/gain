# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Diagnostics for gene models file format inference (gain#856).

Inference tries every supported format against a prefix of the file. When it
does not settle on exactly one format, the reader needs to know which formats
were tried, why each was rejected, and which file it was.
"""
import logging
import pathlib
import re
from io import StringIO

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_file,
)
from gain.genomic_resources.gene_models.parsers import (
    SUPPORTED_GENE_MODELS_FILE_FORMATS,
    infer_gene_model_parser,
    parse_default_gene_models_format,
)

from tests.small.genomic_resources.gene_models.conftest import (
    build_from_content,
)

# Matches no supported gene models format.
UNINFERRABLE_CONTENT = "this is not any gene models format at all\n"

# A headerless record with the refseq/ccds column layout. Both formats parse
# it, and its transcript name has neither format's accession shape, so the
# gain#869 content tie-break cannot settle the collision either.
AMBIGUOUS_CONTENT = (
    "0\tENST00000269305\t17\t-\t7571719\t7590868\t7572826\t7590856\t2\t"
    "7571719,7572926\t7573008,7573009\t0\tTP53\tcmpl\tcmpl\t0,0\n"
)

# GTF-shaped, but "tag" is not a 'key value' pair -- the gain#852 shape.
MALFORMED_GTF_CONTENT = (
    'chr1\ttest\ttranscript\t100\t200\t.\t+\t.\t'
    'gene_id "ENSG001"; transcript_id "ENST001"; tag;\n'
    'chr1\ttest\texon\t100\t150\t.\t+\t.\t'
    'gene_id "ENSG001"; transcript_id "ENST001"; tag;\n'
)

# GTF layout, but a lone gene feature builds no transcript models. ``gene``
# is ignored outright, and an Ensembl one carries no transcript_id at all.
GENE_ONLY_GTF_CONTENT = (
    'chr1\ttest\tgene\t100\t200\t.\t+\t.\t'
    'gene_id "ENSG001";\n'
)

# A headerless refflat record -- inference settles on exactly one format.
REFFLAT_CONTENT = (
    "TP53\tNM_000546\t17\t-\t7571719\t7590868\t7572826\t7590856\t2\t"
    "7571719,7572926\t7573008,7573009\n"
)


@pytest.fixture
def uninferrable_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "mystery_gene_models.txt"
    path.write_text(UNINFERRABLE_CONTENT)
    return path


def test_load_failure_names_the_file(
    uninferrable_file: pathlib.Path,
) -> None:
    """A file whose format cannot be inferred is named in the error."""
    gene_models = build_gene_models_from_file(str(uninferrable_file))

    with pytest.raises(ValueError, match=re.escape("mystery_gene_models.txt")):
        gene_models.load()


def test_load_failure_accounts_for_every_format_tried(
    uninferrable_file: pathlib.Path,
) -> None:
    """The error explains every format inference rejected, not just some.

    Formats reject through two channels -- raising, and quietly returning no
    transcript models. Both have to reach the reader.
    """
    gene_models = build_gene_models_from_file(str(uninferrable_file))

    with pytest.raises(ValueError) as excinfo:
        gene_models.load()

    message = str(excinfo.value)
    unreported = sorted(
        fmt for fmt in SUPPORTED_GENE_MODELS_FILE_FORMATS
        if fmt not in message
    )
    assert not unreported, f"formats missing from the error: {unreported}"


def test_default_format_rejects_missing_columns_quietly() -> None:
    """The default format rejects like its siblings, by returning nothing.

    It used to reject through a message-less bare assert, which reached the
    ledger as a blank reason on every single inference.
    """
    data = StringIO("chr\ttrID\tgene\n1\ttx1\tTP53\n")

    assert parse_default_gene_models_format(data, None, 50) is None


def test_inference_failure_reports_every_reason_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed inference is diagnosable from the default log level."""
    caplog.set_level(logging.WARNING)

    assert infer_gene_model_parser(StringIO(UNINFERRABLE_CONTENT)) is None

    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    reported = warnings[0].getMessage()
    unreported = sorted(
        fmt for fmt in SUPPORTED_GENE_MODELS_FILE_FORMATS
        if fmt not in reported
    )
    assert not unreported, f"formats missing from the log: {unreported}"


def test_no_rejection_reason_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message-less exception still yields something the reader can use.

    An empty reason is worse than no line at all -- it claims the format was
    considered while saying nothing about why it lost.
    """
    def raise_without_message(
        infile: object, gene_mapping: object = None, nrows: object = None,
    ) -> None:
        raise AssertionError

    monkeypatch.setattr(
        parsers, "parse_gtf_gene_models_format", raise_without_message)

    inference = parsers.infer_gene_models_format(
        StringIO(UNINFERRABLE_CONTENT))

    uninformative = [
        (fmt, reason) for fmt, reason in inference.rejected
        if not reason.strip() or reason.rstrip().endswith(":")
    ]
    assert not uninformative, f"uninformative reasons: {uninformative}"


def test_successful_inference_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Diagnosing failures must not make the ordinary path noisy."""
    caplog.set_level(logging.WARNING)

    assert infer_gene_model_parser(StringIO(REFFLAT_CONTENT)) == "refflat"

    assert caplog.records == []


def test_ambiguous_format_names_the_candidates(
    tmp_path: pathlib.Path,
) -> None:
    """An ambiguous file is as undiagnosable as an unmatched one.

    A headerless file with the refseq/ccds column layout matches both
    formats, and a transcript name with neither accession shape keeps the
    tie-break out of it, so inference refuses to choose -- a valid file
    that will not load, previously reported as "can't infer".
    """
    gene_models = build_from_content(
        tmp_path, "ambiguous_gene_models.txt", AMBIGUOUS_CONTENT)

    with pytest.raises(ValueError) as excinfo:
        gene_models.load()

    # The ledger names every format tried, so merely finding "ccds" and
    # "refseq" in the message would hold for an unmatched file too. Pin the
    # headline, which is what distinguishes ambiguity.
    assert "ambiguous: ccds, refseq" in str(excinfo.value)


def test_report_states_that_only_a_prefix_was_sampled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inferring a format is not evidence that the whole file parses.

    Inference reads a prefix, so a malformed record past it is invisible --
    which is how a corrupted file infers cleanly and then loads wrong.
    """
    caplog.set_level(logging.WARNING)

    infer_gene_model_parser(StringIO(UNINFERRABLE_CONTENT))

    reported = caplog.records[0].getMessage()
    assert str(parsers.INFERENCE_SAMPLE_ROWS) in reported
    assert "only the first" in reported


def test_underlying_parse_error_reaches_the_reader(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Regression for the gain#852 hunt.

    A GTF-shaped file whose attribute column trips the GTF parser reported
    only "can't infer gene models file format"; the parser's own account of
    the problem was logged at debug and thrown away.
    """
    caplog.set_level(logging.ERROR)
    gene_models = build_from_content(
        tmp_path, "malformed_attributes.gtf", MALFORMED_GTF_CONTENT)

    with pytest.raises(ValueError) as excinfo:
        gene_models.load()

    assert "malformed GTF attribute" in str(excinfo.value)
    # Both channels, so neither can be dropped without a test going red.
    assert "malformed GTF attribute" in caplog.text


def test_layout_match_without_records_is_its_own_reason(
    tmp_path: pathlib.Path,
) -> None:
    """The third rejection channel: right columns, no transcript models.

    A GTF carrying only a gene feature has the GTF column layout but builds
    nothing from it, which is neither a raise nor a layout mismatch.
    """
    gene_models = build_from_content(
        tmp_path, "gene_only.gtf", GENE_ONLY_GTF_CONTENT)

    with pytest.raises(ValueError) as excinfo:
        gene_models.load()

    expected = (
        "gtf: has this format's column layout but yielded no "
        "transcript models"
    )
    assert expected in str(excinfo.value)


def test_successful_inference_still_notes_the_prefix(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The corrupted-macaque case: inference succeeds and the file is wrong.

    A malformed record past the sampled prefix is invisible to inference, so
    the file infers cleanly and then loads corrupted. The success path is
    exactly where "this was only a prefix" needs saying.
    """
    caplog.set_level(logging.INFO)

    build_from_content(
        tmp_path, "inferrable_gene_models.txt", REFFLAT_CONTENT).load()

    assert "only the first" in caplog.text
    assert str(parsers.INFERENCE_SAMPLE_ROWS) in caplog.text


def test_successful_load_emits_nothing_above_info(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The path production actually takes must stay quiet when it works."""
    caplog.set_level(logging.WARNING)

    build_from_content(
        tmp_path, "quiet_gene_models.txt", REFFLAT_CONTENT).load()

    assert caplog.records == []
