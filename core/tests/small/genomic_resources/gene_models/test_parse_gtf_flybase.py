# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""FlyBase-flavour GTF names the transcript-level feature by biotype.

Unlike Ensembl and RefSeq, which both emit a literal ``transcript`` feature,
FlyBase emits ``mRNA``, ``ncRNA``, ``tRNA`` and friends, and labels genes with
``gene_symbol`` rather than ``gene_name``.
"""

import pytest
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.testing import build_inmemory_test_resource

GENE_ATTRIBUTES = 'gene_id "FBgn0031081"; gene_symbol "Nep3";'
TRANSCRIPT_ATTRIBUTES = (
    'gene_id "FBgn0031081"; gene_symbol "Nep3"; '
    'transcript_id "FBtr0070000"; transcript_symbol "Nep3-RA";'
)


def _record(feature: str, start: int, end: int, attributes: str) -> str:
    return "\t".join([
        "X", "FlyBase", feature, str(start), str(end), ".", "+", ".",
        attributes,
    ])


def _gene_models(*records: str) -> GeneModels:
    """Build gene models from a FlyBase-flavour GTF of the given records."""
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.gtf, format: gtf}",
            "genes.gtf": "\n".join(records) + "\n",
        },
    )
    return build_gene_models_from_resource(res).load()


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


def _attributes(**keys: str) -> str:
    return " ".join(f'{key} "{value}";' for key, value in keys.items())


def _one_transcript(attributes: str) -> GeneModels:
    """Build gene models for a single mRNA transcript with one exon."""
    return _gene_models(
        _record("mRNA", 19961689, 19968479, attributes),
        _record("exon", 19961689, 19961845, attributes),
    )


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
    attributes = _attributes(
        gene_id="FBgn0031081", gene_symbol="Nep3",
        transcript_id="FBtr0070000",
    )
    res = build_inmemory_test_resource(
        content={
            "genomic_resource.yaml":
                "{type: gene_models, filename: genes.gtf, format: gtf,"
                " gene_mapping: names.txt}",
            "genes.gtf": "\n".join([
                _record("mRNA", 19961689, 19968479, attributes),
                _record("exon", 19961689, 19961845, attributes),
            ]) + "\n",
            "names.txt": "gene\talt_gene\nNep3\tNEP3\n",
        },
    )

    gene_models = build_gene_models_from_resource(res).load()

    assert gene_models.transcript_models["FBtr0070000"].gene == "NEP3"


def test_a_repeated_transcript_id_is_still_an_error() -> None:
    with pytest.raises(ValueError, match="already in transcript models"):
        _gene_models(
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
        )


def test_a_flybase_shaped_file_yields_no_exonless_transcript_model() -> None:
    """All nine FlyBase transcript-level biotypes, as they appear in a file.

    The two exon-less ones must not leave a degenerate model behind.
    """
    records = []
    exon_bearing = [
        "mRNA", "ncRNA", "pseudogene", "tRNA", "snoRNA", "rRNA", "snRNA",
    ]
    for index, feature in enumerate([*exon_bearing, "miRNA", "pre_miRNA"]):
        attributes = _attributes(
            gene_id=f"FBgn{index:07d}", gene_symbol=f"sym{index}",
            transcript_id=f"FBtr{index:07d}",
        )
        records.append(_record(feature, 100, 200, attributes))
        if feature in exon_bearing:
            records.append(_record("exon", 100, 200, attributes))

    gene_models = _gene_models(*records)

    assert len(gene_models.transcript_models) == len(exon_bearing)
    assert all(
        transcript.exons
        for transcript in gene_models.transcript_models.values()
    )


def test_unknown_feature_still_raises() -> None:
    """The change narrows the failure; it does not remove it."""
    with pytest.raises(ValueError, match="unknown feature Selenoprotein"):
        _gene_models(
            _record("mRNA", 19961689, 19968479, TRANSCRIPT_ATTRIBUTES),
            _record("Selenoprotein", 19961689, 19961845,
                    TRANSCRIPT_ATTRIBUTES),
        )
