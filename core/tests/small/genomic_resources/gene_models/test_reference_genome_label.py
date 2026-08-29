# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Where ``GeneModels`` gets its reference-genome label from (gain#1009).

``meta`` and ``meta.labels`` are free-form YAML, so a curator can write
either as a scalar, a list or an int.  ``GenomicResource`` narrows both
levels for every reader -- ``get_meta`` the outer one (gain#1004),
``get_labels`` the inner one (gain#654) -- and neither raises.

``GeneModels`` used to reach past both, indexing its validated config down
to ``meta.labels`` behind two ``is not None`` guards that a scalar
satisfies.  Nothing went wrong, because ``gene_models`` runs the base
schema and that schema refuses a non-mapping at either level before the
read happens.  The read was therefore correct only for a reason stated
nowhere near it, and pinned by no test.  It now goes through the same
narrowing accessor as every other reader, so it stays correct whether or
not validation keeps covering it.

Validation refuses; reading degrades -- the same split as gain#1004.
"""
import pytest
import pytest_mock
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_inmemory_test_resource,
    convert_to_tab_separated,
)

# refflat, the format the sibling resource tests use
GMM_CONTENT = """
#geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
TP53      tx1  1     +      10      100   12       95     3         10,50,70   15,60,100
POGZ      tx3  17    +      10      100   12       95     3         10,50,70   15,60,100
"""  # noqa: E501


def a_gene_models_resource(meta_block: str) -> GenomicResource:
    """A ``gene_models`` resource carrying ``meta_block`` verbatim.

    The block is spliced in as text rather than built from a mapping so
    that a test can write the shapes a curator can write -- an explicit
    YAML null, or no block at all -- which a mapping cannot express.

    The models file is declared and written so that the resource is
    shaped like a real one, but nothing here loads it: constructing
    ``GeneModels`` validates the config and reads the label, and these
    tests observe only that label.  Every case would pass just the same
    over an empty ``genes.txt`` -- so if one ever needs the records, it
    has to call ``load()`` and say so.
    """
    return build_inmemory_test_resource(content={
        "genomic_resource.yaml":
            "type: gene_models\nfilename: genes.txt\nformat: refflat\n"
            + meta_block,
        "genes.txt": convert_to_tab_separated(GMM_CONTENT),
    })


def test_a_narrowed_away_labels_block_leaves_no_reference_genome(
    mocker: pytest_mock.MockerFixture,
) -> None:
    """The label follows the narrowing accessor, not the raw config.

    A resource whose ``meta.labels`` is a non-mapping is what this
    protects against, and it cannot be built here: the base schema
    refuses it at construction (see the sibling test), so the state it
    would produce further in -- the accessor reporting no labels over a
    config that still holds some -- is staged with a double instead.

    Whatever the config says, a reader that consults the accessor sees no
    labels and must report no reference genome.  A reader that indexes the
    config itself reads around the narrowing and still finds ``hg38``.
    """
    resource = a_gene_models_resource(
        "meta:\n  labels:\n    reference_genome: hg38\n")
    mocker.patch.object(GenomicResource, "get_labels", return_value={})

    gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id is None


def test_a_declared_reference_genome_label_is_read() -> None:
    """The one shape that yields a value, read off a well-formed block."""
    resource = a_gene_models_resource(
        "meta:\n  labels:\n    reference_genome: hg38\n")

    gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id == "hg38"


@pytest.mark.parametrize("meta_block", [
    pytest.param(
        "meta:\n  labels:\n    domain: gene\n", id="labels-without-the-key"),
    pytest.param("meta:\n  labels:\n", id="labels-an-explicit-yaml-null"),
    pytest.param("meta:\n  description: prose\n", id="meta-without-labels"),
    pytest.param("", id="no-meta-block-at-all"),
])
def test_a_resource_declaring_no_reference_genome_has_none(
    meta_block: str,
) -> None:
    """Every spelling of "no reference genome" reads as absent, not empty.

    The four differ in how much of the block exists, and a reader that
    narrows one level but not the other gets a different one of them
    wrong -- so they are pinned together rather than by a single case.
    """
    resource = a_gene_models_resource(meta_block)

    gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id is None


@pytest.mark.parametrize("meta_block", [
    pytest.param("meta:\n  labels: hg38\n", id="labels-as-a-string"),
    pytest.param("meta:\n  labels: [a, b]\n", id="labels-as-a-list"),
    pytest.param("meta:\n  labels: 2019\n", id="labels-as-an-int"),
    pytest.param("meta: some text\n", id="meta-as-a-string"),
    pytest.param("meta: [a, b]\n", id="meta-as-a-list"),
])
def test_the_schema_still_refuses_a_non_mapping_meta_level(
    meta_block: str,
) -> None:
    """Validation stays the guard; the accessor is only the backstop.

    Reading through the narrowing accessor must not quietly make a
    malformed ``gene_models`` resource loadable -- the base schema types
    both levels, and a resource that violates it is still refused before
    any label is read, at the same place and with the same error as
    before (gain#1009).
    """
    resource = a_gene_models_resource(meta_block)

    with pytest.raises(ValueError, match="Invalid configuration"):
        GeneModels(resource)
