# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""FlyBase-flavour GTF names the transcript-level feature by biotype.

Unlike Ensembl and RefSeq, which both emit a literal ``transcript`` feature,
FlyBase emits ``mRNA``, ``ncRNA``, ``tRNA`` and friends, and labels genes with
``gene_symbol`` rather than ``gene_name``.
"""

from pathlib import Path

import pytest
from gain.genomic_resources.gene_models import parsers
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_file,
    build_gene_models_from_resource,
)
from gain.genomic_resources.testing import build_inmemory_test_resource


def _attributes(**keys: str) -> str:
    return " ".join(f'{key} "{value}";' for key, value in keys.items())


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "FlyBase", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


def _gene_models(
    *records: str,
    gene_mapping: str | None = None,
) -> GeneModels:
    """Build gene models from a FlyBase-flavour GTF of the given records.

    ``gene_mapping`` is the body of an alternative-names table, header
    included, wired up through the resource config.
    """
    config = "type: gene_models, filename: genes.gtf, format: gtf"
    content = {"genes.gtf": "\n".join(records) + "\n"}
    if gene_mapping is not None:
        config += ", gene_mapping: names.txt"
        content["names.txt"] = gene_mapping
    content["genomic_resource.yaml"] = f"{{{config}}}"

    res = build_inmemory_test_resource(content=content)
    return build_gene_models_from_resource(res).load()


def _one_transcript(attributes: str, **kwargs: str) -> GeneModels:
    """Build gene models for a single mRNA transcript with one exon."""
    return _gene_models(
        _record("mRNA", 19961689, 19968479, attributes),
        _record("exon", 19961689, 19961845, attributes),
        **kwargs,
    )


GENE_ATTRIBUTES = _attributes(gene_id="FBgn0031081", gene_symbol="Nep3")
TRANSCRIPT_ATTRIBUTES = _attributes(
    gene_id="FBgn0031081", gene_symbol="Nep3",
    transcript_id="FBtr0070000", transcript_symbol="Nep3-RA",
)


# ---------------------------------------------------------------- dispatch


@pytest.mark.parametrize("feature", [
    "mRNA", "ncRNA", "pseudogene", "tRNA", "snoRNA", "rRNA", "snRNA",
])
def test_exon_bearing_biotype_is_a_transcript_level_feature(
    feature: str,
) -> None:
    gene_models = _gene_models(
        _record("gene", 19961297, 19969323, GENE_ATTRIBUTES),
        _record(feature, 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
        _record("exon", 19961689, 19961845, TRANSCRIPT_ATTRIBUTES),
        _record("exon", 19963955, 19968479, TRANSCRIPT_ATTRIBUTES),
    )

    transcript = gene_models.transcript_models["FBtr0070000"]

    assert transcript.tx == (19961689, 19968479)
    assert [(e.start, e.stop) for e in transcript.exons] == [
        (19961689, 19961845), (19963955, 19968479),
    ]


@pytest.mark.parametrize("feature", ["miRNA", "pre_miRNA"])
def test_exonless_biotype_yields_no_transcript_model(feature: str) -> None:
    """FlyBase gives these no ``exon`` records, so they carry no sequence."""
    gene_models = _gene_models(
        _record("gene", 19961297, 19969323, GENE_ATTRIBUTES),
        _record(feature, 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
    )

    assert gene_models.transcript_models == {}


def test_a_skipped_biotype_needs_no_transcript_id() -> None:
    """The skip must not depend on an attribute the record never uses."""
    gene_models = _gene_models(
        _record("miRNA", 100, 200, _attributes(gene_id="FBgn0031081")),
    )

    assert gene_models.transcript_models == {}


def test_the_skip_set_is_disjoint_from_the_accepted_set() -> None:
    """Otherwise the accepted set wins silently and the skip becomes dead."""
    assert not (
        parsers.GTF_TRANSCRIPT_FEATURES
        & parsers.GTF_EXONLESS_TRANSCRIPT_FEATURES
    )


# ------------------------------------------------------------- gene labels


def test_gene_symbol_resolves_the_gene_label() -> None:
    gene_models = _one_transcript(_attributes(
        gene_id="FBgn0031081", gene_symbol="Nep3",
        transcript_id="FBtr0070000",
    ))

    assert gene_models.transcript_models["FBtr0070000"].gene == "Nep3"


def test_gene_name_still_wins_over_gene_symbol() -> None:
    """Ensembl and RefSeq files, which carry both, are unaffected."""
    gene_models = _one_transcript(_attributes(
        gene_id="FBgn0031081", gene_name="Nep3", gene_symbol="Nep3-symbol",
        transcript_id="FBtr0070000",
    ))

    assert gene_models.transcript_models["FBtr0070000"].gene == "Nep3"


def test_gene_id_remains_the_last_resort() -> None:
    gene_models = _one_transcript(_attributes(
        gene_id="FBgn0031081", transcript_id="FBtr0070000",
    ))

    assert gene_models.transcript_models["FBtr0070000"].gene == "FBgn0031081"


def test_gene_mapping_applies_to_a_symbol_resolved_label() -> None:
    gene_models = _one_transcript(
        _attributes(
            gene_id="FBgn0031081", gene_symbol="Nep3",
            transcript_id="FBtr0070000",
        ),
        gene_mapping="gene\talt_gene\nNep3\tNEP3\n",
    )

    assert gene_models.transcript_models["FBtr0070000"].gene == "NEP3"


# --------------------------------------------------------- errors preserved


def test_a_repeated_transcript_id_is_still_an_error() -> None:
    with pytest.raises(ValueError, match="already in transcript models"):
        _gene_models(
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
        )


@pytest.mark.parametrize("feature", ["miRNA", "pre_miRNA"])
def test_an_exon_child_of_a_skipped_biotype_blames_the_parent(
    feature: str,
) -> None:
    """FlyBase's premise is that these biotypes have no exon children.

    A file that breaks it must say the parent was skipped, not point at
    the exon as if the transcript had never appeared.
    """
    with pytest.raises(
            ValueError,
            match=(
                f"transcript FBtr0070000 was skipped as "
                f"exonless feature {feature}"
            )):
        _gene_models(
            _record(feature, 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("exon", 19961689, 19961845, TRANSCRIPT_ATTRIBUTES),
        )


def test_a_codon_child_of_a_skipped_biotype_blames_the_parent() -> None:
    with pytest.raises(
            ValueError,
            match=(
                "transcript FBtr0070000 was skipped as "
                "exonless feature miRNA"
            )):
        _gene_models(
            _record("miRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("start_codon", 19961689, 19961691,
                    TRANSCRIPT_ATTRIBUTES),
        )


def test_unknown_feature_still_raises() -> None:
    """The change narrows the failure; it does not remove it."""
    with pytest.raises(ValueError, match="unknown feature Selenoprotein"):
        _gene_models(
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("Selenoprotein", 19961689, 19961845,
                    TRANSCRIPT_ATTRIBUTES),
        )


# ------------------------------------------------------------- real excerpt

FLYBASE_FIXTURE = str(
    Path(__file__).resolve().parent
    / "fixtures" / "gene_models" / "test_flybase.gtf",
)


def _flybase_excerpt() -> GeneModels:
    """Load the trimmed excerpt of a real ``dmel-all-r6.68.gtf.gz``.

    It carries one transcript of each of the nine transcript-level biotypes
    FlyBase emits, with their real spellings and attributes.
    """
    return build_gene_models_from_file(FLYBASE_FIXTURE, "gtf").load()


def test_real_flybase_excerpt_admits_the_exon_bearing_biotypes_by_symbol(
) -> None:
    """One transcript per biotype; the miRNA and pre_miRNA are absent."""
    gene_models = _flybase_excerpt()

    assert {
        tr_id: tm.gene
        for tr_id, tm in gene_models.transcript_models.items()
    } == {
        "FBtr0070000": "Nep3",                # mRNA
        "FBtr0070001": "tRNA:Pro-CGG-1-1",    # tRNA
        "FBtr0070292": "snoRNA:M",            # snoRNA
        "FBtr0078851": "snRNA:U1:82Eb",       # snRNA
        "FBtr0086345": "5SrRNA:CR33353",      # rRNA
        "FBtr0307588": "CR32821",             # pseudogene
        "FBtr0308931": "lncRNA:CR33218",      # ncRNA
    }


def test_real_flybase_excerpt_builds_a_full_mrna() -> None:
    """The widened dispatch still cooperates with UTR, CDS and codon rows.

    Only real data carries an mRNA with all of them at once.
    """
    transcript = _flybase_excerpt().transcript_models["FBtr0070000"]

    assert transcript.tx == (19961689, 19968479)
    assert transcript.cds == (19963955, 19967460)
    assert len(transcript.exons) == 9
    assert [exon.frame for exon in transcript.exons] == [
        -1, 0, 0, 1, 2, 2, 2, 1, 0,
    ]
