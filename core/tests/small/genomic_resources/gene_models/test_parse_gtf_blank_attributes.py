# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A GTF record whose attributes column is blank is an error that
identifies the record.

pandas delivers a blank cell as a float ``NaN``, and the attribute
scanner used to call string methods straight on it -- so the failure
escaped as a bare ``AttributeError`` naming a float, with nothing to say
which record, or even that the file was at fault (gain#907).
"""

import re
from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "test", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


#: A well-formed record to lead every file with. The column-count probe
#: reads the first row, so a short row there makes the file match no
#: layout at all -- a separate wrinkle, deliberately not under test here.
GOOD_TRANSCRIPT = _record(
    "transcript", 100, 200, 'gene_id "G1"; transcript_id "T1";')


def test_a_transcript_with_a_blank_attributes_column_names_the_record(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    with pytest.raises(
            ValueError,
            match=(
                "transcript record at X:300-400 has an empty "
                "attributes column"
            )):
        gtf_gene_models(
            GOOD_TRANSCRIPT,
            _record("transcript", 300, 400, ""),
        )


def test_a_record_that_omits_the_attributes_column_names_the_record(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The two spellings of a blank cell are one case.

    ``_record`` leaves an empty ninth column; a short row has no ninth
    column at all. pandas delivers ``NaN`` for both, so both must be
    reported the same way. Only the row shape varies here -- the feature
    stays a ``transcript``, so a failure can only mean the shape.
    """
    short_row = "\t".join(
        ["X", "test", "transcript", "300", "400", ".", "+", "."])

    with pytest.raises(
            ValueError,
            match=(
                "transcript record at X:300-400 has an empty "
                "attributes column"
            )):
        gtf_gene_models(GOOD_TRANSCRIPT, short_row)


def test_a_child_record_with_a_blank_attributes_column_is_reported_too(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The other axis: a child record, not a transcript.

    Kept apart from the row-shape case above so that a failure names
    which of the two axes broke.
    """
    with pytest.raises(
            ValueError,
            match="exon record at X:300-400 has an empty attributes column"):
        gtf_gene_models(GOOD_TRANSCRIPT, _record("exon", 300, 400, ""))


def test_an_attributes_column_pandas_did_not_read_as_text_is_reported(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A blank cell is not the only cell that arrives as a non-string.

    The headerless read infers a dtype per column, so a file whose ninth
    column is numeric on every row hands the scanner a float. That is not
    a blank cell -- it is a file that is not GTF -- and it has to be
    rejected as malformed rather than escape as an ``AttributeError``
    naming a float, which is the very message gain#907 is about.
    """
    with pytest.raises(
            ValueError,
            match=re.escape("malformed GTF attribute '1.5'")):
        gtf_gene_models(_record("transcript", 100, 200, "1.5"))


def test_an_ignored_feature_keeps_its_blank_attributes_column(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Reporting a blank column must not start rejecting skipped records.

    An ignored feature is skipped before its attributes are read at all,
    which is what lets an Ensembl ``gene`` record through without the
    ``transcript_id`` it genuinely lacks. A blank column on such a record
    is nothing the parser needs an opinion about, so the new guard has to
    stay below the skip.
    """
    gene_models = gtf_gene_models(
        GOOD_TRANSCRIPT,
        _record("gene", 300, 400, ""),
        _record("exon", 100, 150, 'gene_id "G1"; transcript_id "T1";'),
    )

    assert list(gene_models.transcript_models) == ["T1"]
