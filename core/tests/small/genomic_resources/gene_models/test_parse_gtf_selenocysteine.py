# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``Selenocysteine`` is a child record, not a transcript-level one.

GENCODE marks the recoded UGA codons of a selenoprotein with a
``Selenocysteine`` record per site, always after the ``transcript``
record it belongs to. The record contributes nothing to the model --
every site falls inside a ``CDS`` record of the same transcript, so the
coding interval already covers it -- so the parser's only interest in it
is that its parent exists.
"""

from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels

from tests.small.genomic_resources.gene_models.conftest import (
    transcript_digest,
)


def _attributes(**keys: str) -> str:
    return " ".join(f'{key} "{value}";' for key, value in keys.items())


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "chr1", "HAVANA", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


#: ``SELENON``/``ENST00000361547``, the GENCODE v46 selenoprotein these
#: records are modelled on. The coordinates below are compacted: the real
#: transcript spans 25800193-25818221 and carries its two recoded sites
#: 10 kb apart, which would need a third exon to say nothing more.
TRANSCRIPT_ATTRIBUTES = _attributes(
    gene_id="ENSG00000162430", gene_name="SELENON",
    transcript_id="ENST00000361547",
)


def test_a_selenocysteine_of_a_never_seen_transcript_names_it(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A parentless record must be reported, not turned into a model.

    Dispatched as a transcript-level feature it silently manufactured a
    three-base, exon-less model of its own instead.
    """
    with pytest.raises(
            ValueError,
            match=(
                "Selenocysteine transcript ENST00000361547 not found "
                "in transcript models"
            )):
        gtf_gene_models(
            _record("Selenocysteine", 25802093, 25802095,
                    TRANSCRIPT_ATTRIBUTES),
        )


def test_a_selenocysteine_preceding_its_transcript_blames_neither_the_other(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The transcript record must not be blamed for a duplicate.

    Manufacturing a model from the codon left the genuine ``transcript``
    record colliding with it, reported as ``already in transcript
    models`` -- which accused the one record in the file that was right.
    The records after the site are deliberately never reached: the point
    is that the file is rejected on the site, before they are dispatched.
    """
    with pytest.raises(ValueError) as excinfo:
        gtf_gene_models(
            _record("Selenocysteine", 25802093, 25802095,
                    TRANSCRIPT_ATTRIBUTES),
            _record("transcript", 25799245, 25818038,
                    TRANSCRIPT_ATTRIBUTES),
            _record("exon", 25799245, 25799616, TRANSCRIPT_ATTRIBUTES),
        )

    assert str(excinfo.value) == (
        "Selenocysteine transcript ENST00000361547 not found "
        "in transcript models"
    )


@pytest.mark.parametrize(
    "feature", sorted(parsers.GTF_EXONLESS_TRANSCRIPT_FEATURES))
def test_a_selenocysteine_child_of_a_skipped_biotype_blames_the_parent(
    gtf_gene_models: Callable[..., GeneModels],
    feature: str,
) -> None:
    """A skipped parent must stay skipped, and say so.

    Creating a model from the codon resurrected the very transcript the
    exonless-biotype skip had deliberately left out.
    """
    with pytest.raises(
            ValueError,
            match=(
                "Selenocysteine transcript ENST00000361547 was skipped "
                f"as exonless feature {feature}"
            )):
        gtf_gene_models(
            _record(feature, 25799245, 25818038, TRANSCRIPT_ATTRIBUTES),
            _record("Selenocysteine", 25802093, 25802095,
                    TRANSCRIPT_ATTRIBUTES),
        )


#: A well-formed selenoprotein, in the order and shape GENCODE writes it:
#: the transcript first, and one ``Selenocysteine`` record per recoded
#: site -- ``SELENON`` carries two, which is why 107 records cover only
#: 71 transcripts in ``gencode.v46.basic``.
WELL_FORMED = (
    _record("transcript", 25799245, 25818038, TRANSCRIPT_ATTRIBUTES),
    _record("exon", 25799245, 25799616, TRANSCRIPT_ATTRIBUTES),
    _record("exon", 25802000, 25802200, TRANSCRIPT_ATTRIBUTES),
    _record("start_codon", 25799300, 25799302, TRANSCRIPT_ATTRIBUTES),
    _record("stop_codon", 25802100, 25802102, TRANSCRIPT_ATTRIBUTES),
)
SELENOCYSTEINE_SITES = (
    _record("Selenocysteine", 25802093, 25802095, TRANSCRIPT_ATTRIBUTES),
    _record("Selenocysteine", 25802090, 25802092, TRANSCRIPT_ATTRIBUTES),
)


def test_a_well_formed_selenoprotein_parses_as_if_the_sites_were_absent(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The records must leave the model they annotate untouched.

    Compared against the same file with the sites deleted, so a record
    that added a model, an exon or a widened CDS would show up whichever
    field it reached.
    """
    annotated = gtf_gene_models(*WELL_FORMED, *SELENOCYSTEINE_SITES)

    assert transcript_digest(annotated) == transcript_digest(
        gtf_gene_models(*WELL_FORMED))
    assert list(annotated.transcript_models) == ["ENST00000361547"]

    transcript = annotated.transcript_models["ENST00000361547"]
    assert transcript.tx == (25799245, 25818038)
    assert transcript.cds == (25799300, 25802102)
