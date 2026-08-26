# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""``CDS`` records state the coding extent; the codon records only bound it.

GENCODE flags a transcript whose ends are unconfirmed with ``mRNA_start_NF``
/ ``cds_end_NF`` and friends, and then emits only the codon record it can
vouch for -- often neither. Reconstructing the coding interval from the
codon records alone therefore collapses such a transcript to that codon's
own three bases, or leaves it inverted and reported as non-coding, while
the ``CDS`` records that state the real extent sit in the same file.

Folding the ``CDS`` records into the interval cannot disturb a complete
transcript: GENCODE and Ensembl exclude the stop codon from their ``CDS``
records, so wherever both codons are present the codon span already covers
the ``CDS`` span. Measured over ``gencode.v49.annotation.gtf.gz``, the
union differs from the codon span for none of the 203,640 complete
transcripts and for 30,344 of the 30,355 incomplete ones. The remaining
eleven carry a single ``CDS`` record identical to their one codon and
really are three bases of coding sequence; the fix leaves them alone.

Being incomplete is not the same as being tagged so: 157 of those 30,355
carry no ``*_NF`` tag at all. Three of them are ``MT-ND1``/``2``/``3``,
whose ORFs end on an incomplete stop codon completed by polyadenylation,
so GENCODE has no codon to annotate.
"""

from collections.abc import Callable

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.serialization import (
    gene_models_to_gtf,
)

from tests.small.genomic_resources.gene_models.conftest import (
    transcript_digest,
)


def _attributes(**keys: str) -> str:
    return " ".join(f'{key} "{value}";' for key, value in keys.items())


def _record(
    chrom: str, strand: str, feature: str,
    start: int, end: int, attributes: str,
) -> str:
    return "\t".join([
        chrom, "HAVANA", feature, str(start), str(end), ".", strand, ".",
        attributes,
    ])


def _records(
    chrom: str, strand: str, attributes: str,
) -> Callable[[str, int, int], str]:
    """Bind one transcript's chromosome, strand and attributes.

    Stated once per fixture rather than on every record: the parse loop
    reads neither field from a child record, so a stray one would be
    accepted in silence instead of failing a test.
    """
    def _build(feature: str, start: int, end: int) -> str:
        return _record(chrom, strand, feature, start, end, attributes)
    return _build


#: ``UNC13D``/``ENST00000590762``, the GENCODE v49 transcript the issue
#: measured: tagged ``cds_end_NF``, so it carries a ``start_codon`` and no
#: ``stop_codon``. Its ten ``CDS`` records span 75840225-75843722, 3498 bp;
#: the parser used to record 75843720-75843722, the start codon alone.
#: Three of its ten exon/``CDS`` pairs are reproduced below, coordinates
#: unaltered -- the first, the last, and one in between. Dropping the
#: other seven shortens the sequence but not the span, which is what the
#: outermost pairs decide.
UNC13D = _attributes(
    gene_id="ENSG00000092929", gene_name="UNC13D",
    transcript_id="ENST00000590762",
)
_unc13d = _records("chr17", "-", UNC13D)
UNC13D_CDS_SPAN = (75840225, 75843722)
UNC13D_LAST_CDS = (75840225, 75840329)
UNC13D_START_CODON = _unc13d("start_codon", 75843720, 75843722)
UNC13D_BODY = (
    _unc13d("transcript", 75840225, 75843769),
    _unc13d("exon", 75843663, 75843769),
    _unc13d("CDS", 75843663, 75843722),
    _unc13d("exon", 75842433, 75842613),
    _unc13d("CDS", 75842433, 75842613),
    _unc13d("exon", *UNC13D_LAST_CDS),
    _unc13d("CDS", *UNC13D_LAST_CDS),
)


def test_a_transcript_with_only_a_start_codon_spans_its_cds_records(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The coding interval must not collapse to the one codon present.

    Ignoring the ``CDS`` records left this transcript's interval equal to
    the three bases of its own start codon -- reported as coding, with
    3498 bp of stated coding sequence reduced to 3.
    """
    gene_models = gtf_gene_models(*UNC13D_BODY, UNC13D_START_CODON)

    transcript = gene_models.transcript_models["ENST00000590762"]
    assert transcript.cds == UNC13D_CDS_SPAN
    assert transcript.is_coding()


#: ``IFNA6``/``ENST00000259555``, a single-exon GENCODE v49 transcript with
#: a ``stop_codon`` and no ``start_codon``. Coordinates unaltered; its two
#: ``UTR`` records are left out, being ignored features either way.
#: It carries no ``*_NF`` tag at all -- it is ``basic`` and
#: ``GENCODE_Primary`` -- which is why the defect reaches the ``basic``
#: flavours that deployments pin, not only ``comprehensive``.
IFNA6 = _attributes(
    gene_id="ENSG00000120235", gene_name="IFNA6",
    transcript_id="ENST00000259555",
)
_ifna6 = _records("chr9", "-", IFNA6)
IFNA6_BODY = (
    _ifna6("transcript", 21350253, 21350956),
    _ifna6("exon", 21350253, 21350956),
    _ifna6("CDS", 21350321, 21350890),
)
IFNA6_STOP_CODON = _ifna6("stop_codon", 21350318, 21350320)


def test_a_transcript_with_only_a_stop_codon_spans_its_cds_and_the_codon(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """The stop codon extends the interval past the ``CDS`` records.

    GENCODE excludes the stop codon from the ``CDS`` records, so neither
    source covers the other here: taking only the codon gave a 3 bp
    interval, and taking only the ``CDS`` records would stop three bases
    short of the transcript's actual end.
    """
    gene_models = gtf_gene_models(*IFNA6_BODY, IFNA6_STOP_CODON)

    transcript = gene_models.transcript_models["ENST00000259555"]
    assert transcript.cds == (21350318, 21350890)


#: ``MT-ND3``/``ENST00000361227``, reproduced record for record: three
#: records, all coextensive. GENCODE emits no codon record for it -- a
#: mitochondrial ORF ends on an incomplete stop codon completed by
#: polyadenylation, so there is none to annotate. It is nonetheless
#: ``Ensembl_canonical`` and ``appris_principal_1``, and it is not alone:
#: ``MT-ND1`` and ``MT-ND2`` are the same shape.
MT_ND3 = _attributes(
    gene_id="ENSG00000198840", gene_name="MT-ND3",
    transcript_id="ENST00000361227",
)
_mt_nd3 = _records("chrM", "+", MT_ND3)
MT_ND3_BODY = (
    _mt_nd3("transcript", 10059, 10404),
    _mt_nd3("exon", 10059, 10404),
    _mt_nd3("CDS", 10059, 10404),
)


def test_a_transcript_with_no_codon_records_is_coding(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """``CDS`` records alone must establish that a transcript codes.

    With nothing to widen it, the interval stayed at the inverted
    ``(tx_end, tx_start)`` it is seeded with, so ``is_coding()`` was
    False and the whole ORF was invisible -- for three canonical
    mitochondrial protein-coding genes, among others.
    """
    gene_models = gtf_gene_models(*MT_ND3_BODY)

    transcript = gene_models.transcript_models["ENST00000361227"]
    assert transcript.is_coding()
    assert transcript.cds == (10059, 10404)


#: ``SMIM34``/``ENST00000450895``, a complete two-exon GENCODE v49
#: transcript, reproduced record for record. Its ``CDS`` records do *not*
#: coincide with its codon span -- they stop at 34419221 while the stop
#: codon reaches 34419218 -- so this fixture would notice the ``CDS``
#: records being folded in wrongly, rather than passing because the two
#: sources happen to agree.
SMIM34 = _attributes(
    gene_id="ENSG00000243627", gene_name="SMIM34",
    transcript_id="ENST00000450895",
)
_smim34 = _records("chr21", "-", SMIM34)
SMIM34_EXONS = (
    _smim34("transcript", 34418715, 34423951),
    _smim34("exon", 34423833, 34423951),
    _smim34("exon", 34418715, 34419630),
)
SMIM34_CODONS = (
    _smim34("start_codon", 34423837, 34423839),
    _smim34("stop_codon", 34419218, 34419220),
)
SMIM34_CDS_RECORDS = (
    _smim34("CDS", 34423833, 34423839),
    _smim34("CDS", 34419221, 34419630),
)


def test_a_complete_transcript_parses_as_if_its_cds_records_were_absent(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Reading the ``CDS`` records must not disturb what already worked.

    Compared against the same file with the ``CDS`` records deleted, so a
    shifted interval or a re-derived exon frame would show up whichever
    field it reached. This is the property that lets the published gene
    models be rebuilt: it holds for all 203,640 complete transcripts of
    GENCODE v49 and all 19,353 of MANE 1.4.
    """
    with_cds = gtf_gene_models(
        *SMIM34_EXONS, *SMIM34_CODONS, *SMIM34_CDS_RECORDS)

    assert transcript_digest(with_cds) == transcript_digest(
        gtf_gene_models(*SMIM34_EXONS, *SMIM34_CODONS))

    transcript = with_cds.transcript_models["ENST00000450895"]
    assert transcript.cds == (34419218, 34423839)


def test_a_transcript_with_neither_cds_nor_codon_records_stays_non_coding(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A genuinely non-coding transcript must keep its inverted interval.

    The seeded ``(tx_end, tx_start)`` is what reports a transcript as
    non-coding, and nothing may widen it into a coding one by accident.
    ``SMIM34`` stripped of everything that states coding, so the only
    difference from the fixture above is the records under test.
    """
    gene_models = gtf_gene_models(*SMIM34_EXONS)

    transcript = gene_models.transcript_models["ENST00000450895"]
    assert not transcript.is_coding()
    assert transcript.cds == (34423951, 34418715)


def test_a_cds_of_a_never_seen_transcript_names_it(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A parentless ``CDS`` must be reported, not silently dropped.

    Skipped outright it said nothing at all; now that it carries the
    coding extent, a record whose parent is missing is a broken file.
    """
    with pytest.raises(
            ValueError,
            match=(
                "CDS transcript ENST00000590762 not found "
                "in transcript models"
            )):
        gtf_gene_models(_unc13d("CDS", *UNC13D_LAST_CDS))


@pytest.mark.parametrize(
    "feature", sorted(parsers.GTF_EXONLESS_TRANSCRIPT_FEATURES))
def test_a_cds_child_of_a_skipped_biotype_blames_the_parent(
    gtf_gene_models: Callable[..., GeneModels],
    feature: str,
) -> None:
    """A skipped parent must stay skipped, and say why."""
    with pytest.raises(
            ValueError,
            match=(
                f"CDS transcript ENST00000590762 was skipped "
                f"as exonless feature {feature}"
            )):
        gtf_gene_models(
            _unc13d(feature, 75840225, 75843769),
            _unc13d("CDS", *UNC13D_LAST_CDS),
        )


def test_a_cds_with_a_malformed_attribute_column_is_rejected(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """A ``CDS`` record's attributes are now parsed, so they must parse.

    Pinned because it is the one behaviour that stands in the way of
    skipping the full attribute parse for ``CDS`` records, which measures
    at roughly a tenth of GTF load time. Reading only ``transcript_id``
    out of the column would be faster and would accept this file.
    """
    with pytest.raises(ValueError, match="malformed GTF attribute"):
        gtf_gene_models(
            *UNC13D_BODY,
            _record("chr17", "-", "CDS", *UNC13D_LAST_CDS,
                    'transcript_id "ENST00000590762"; tag;'),
        )


def test_an_incomplete_transcript_survives_a_gtf_round_trip(
    gtf_gene_models: Callable[..., GeneModels],
) -> None:
    """Writing GTF back out must restate the extent that was read in.

    The writer derives its ``CDS`` records from the coding interval, so
    while the interval was a 3 bp stub it emitted no ``CDS`` record at
    all -- the stub fitted inside the terminal codons it also writes --
    and three UTR records covered the whole ORF instead.
    """
    serialized = gene_models_to_gtf(
        gtf_gene_models(*UNC13D_BODY, UNC13D_START_CODON)).getvalue()

    emitted = [
        line for line in serialized.splitlines()
        if not line.startswith("#") and line.split("\t")[2] == "CDS"
    ]
    assert len(emitted) == 3

    reread = gtf_gene_models(*serialized.splitlines())
    assert reread.transcript_models["ENST00000590762"].cds == UNC13D_CDS_SPAN
