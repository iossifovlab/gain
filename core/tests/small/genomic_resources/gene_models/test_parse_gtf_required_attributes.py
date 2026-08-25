# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A GTF record missing an attribute the parser requires is an error that
identifies the record.

There is no ``transcript_id`` to name such a record by -- that is the
attribute at issue -- so the message identifies it by feature and
position instead.
"""

from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "test", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


def test_a_transcript_without_transcript_id_names_the_record(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    with pytest.raises(
            ValueError,
            match=(
                "transcript record at X:100-200 has no "
                "transcript_id attribute"
            )):
        gtf_gene_models(
            _record("transcript", 100, 200, 'gene_id "G1";'),
        )


@pytest.mark.parametrize("feature", sorted(
    parsers.GTF_EXON_FEATURES | parsers.GTF_CODON_FEATURES))
def test_a_child_without_transcript_id_names_the_record(
    gtf_gene_models: Callable[..., GeneModels],
    feature: str,
) -> None:
    """The requirement is checked once, above the branch on feature.

    A child record is rejected for the missing attribute itself, before
    anything tries to look up the parent it cannot name.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"{feature} record at X:100-120 has no "
                "transcript_id attribute"
            )):
        gtf_gene_models(
            _record("transcript", 100, 200, 'gene_id "G1"; '
                                            'transcript_id "T1";'),
            _record(feature, 100, 120, 'gene_id "G1";'),
        )


def test_a_transcript_without_any_gene_label_names_the_record(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Any one of the three would have done, so none is blamed alone."""
    with pytest.raises(
            ValueError,
            match=(
                "transcript record at X:100-200 has no usable gene label; "
                "expected gene_name, gene_symbol or gene_id"
            )):
        gtf_gene_models(
            _record("transcript", 100, 200, 'transcript_id "T1";'),
        )


def test_an_empty_gene_label_falls_through_to_the_next_spelling(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Present-but-empty is not the same as absent.

    Requiring a gene label must not start rejecting -- or worse, start
    labelling a gene with -- an empty higher-precedence spelling.
    """
    gene_models = gtf_gene_models(
        _record("transcript", 100, 200,
                'gene_name ""; gene_id "G1"; transcript_id "T1";'),
    )

    assert gene_models.transcript_models["T1"].gene == "G1"


def test_an_empty_transcript_id_is_still_a_transcript_id(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """What is required is the attribute, not a useful value in it.

    Characterises what the parser does today rather than endorsing it:
    an empty id makes an oddly-keyed model. Tightening that is a
    behaviour change, and not one this guard should make by accident.
    """
    gene_models = gtf_gene_models(
        _record("transcript", 100, 200, 'gene_id "G1"; transcript_id "";'),
    )

    assert list(gene_models.transcript_models) == [""]


def test_gene_labels_that_are_all_present_but_empty_are_not_missing(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The same distinction on the other guard: empty is not absent."""
    gene_models = gtf_gene_models(
        _record("transcript", 100, 200,
                'gene_name ""; gene_symbol ""; gene_id ""; '
                'transcript_id "T1";'),
    )

    assert gene_models.transcript_models["T1"].gene == ""


def test_only_the_last_gene_label_spelling_has_to_be_there(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Between the two cases above, where the chain is asymmetric.

    ``or`` steps past the empty higher-precedence spellings and lands on
    an absent ``gene_id``, so an empty label does not save the record the
    way the all-empty case is saved by its own ``gene_id ""``. Hence
    "usable": ``gene_name`` is on the line, and is still no use.
    """
    with pytest.raises(ValueError, match="has no usable gene label"):
        gtf_gene_models(
            _record("transcript", 100, 200,
                    'gene_name ""; gene_symbol ""; transcript_id "T1";'),
        )
