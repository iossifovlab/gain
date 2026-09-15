# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The reference-genome label: where it is read, and what it may hold.

Two questions, and gain#1009 settled only the first.  gain#1050 is the
second: the accessor promises a *mapping* and says nothing about what is
in it -- ``get_meta``'s docstring is explicit that a reader of a field
narrows that field itself -- so the label's VALUE was read unnarrowed
into an attribute annotated ``str | None``.  Six of the eight shapes a
curator can write violated that annotation, and validation caught none
of them: the base schema types ``meta.labels`` as a mapping, not its
values.

They failed in two ways, split by the ``or`` chain every reader of the
attribute chains it with -- the truthy shapes won it and died past it,
the falsy ones lost it and were replaced in silence.  Which is which,
and what each one cost, is on the two tests that demonstrate it.

As for the block around it: ``meta`` and ``meta.labels`` are free-form
YAML too, so a curator can write either as a scalar, a list or an int.
``GenomicResource`` narrows both levels for every reader -- ``get_meta``
the outer one (gain#1004), ``get_labels`` the inner one (gain#654) --
and neither raises.

``GeneModels`` used to reach past both, indexing its validated config down
to ``meta.labels`` behind two ``is not None`` guards that a scalar
satisfies.  Nothing went wrong, because ``gene_models`` runs the base
schema and that schema refuses a non-mapping at either level before the
read happens.  The read was therefore correct only for a reason stated
nowhere near it, and pinned by no test.  It now goes through the same
narrowing accessor as every other reader, so it stays correct whether or
not validation keeps covering it.

Validation refuses; reading degrades -- the same split as gain#1004.

Untested here, deliberately: a non-empty ``str`` that is still not a
usable id -- ``" hg38 "``, or the trailing newline a folded scalar
leaves.  Nothing narrows those; see the reader's own docstring for why.
"""
import logging
import pathlib
from typing import Any

import pytest
import pytest_mock
from gain.annotation.annotation_config import (
    AnnotationPreamble,
    AnnotatorInfo,
)
from gain.annotation.annotation_pipeline import AnnotationPipeline
from gain.annotation.utils import find_annotator_reference_genome
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_reference_genome,
)
from gain.genomic_resources.testing.gene_models_builder import a_gene_models

from ..conftest import captured_warnings

A_GENE_MODELS_ID = "gene_models/broken"

A_GENOME_ID = "genomes/from_the_preamble"


def a_gene_models_resource_built_from(
    tmp_path: pathlib.Path, builder: Any,
) -> GenomicResource:
    """Realize ``builder`` under a real resource id.

    Through a GRR rather than ``build_resource``, which gives a lone
    resource an EMPTY id -- ``id in message`` then holds for every
    message ever written and pins nothing.

    Nothing here loads the models file: constructing ``GeneModels``
    validates the config and reads the label, and these tests observe
    only that label.  A test that ever wants the records has to call
    ``load()`` and say so.
    """
    return (
        a_grr()
        .with_resource(A_GENE_MODELS_ID, builder)
        .build_repo(tmp_path)
        .get_resource(A_GENE_MODELS_ID)
    )


def a_gene_models_whose_reference_genome_is(
    tmp_path: pathlib.Path, value: Any,
) -> GenomicResource:
    """A ``gene_models`` resource declaring ``reference_genome: value``.

    Built through the ``a_gene_models`` builder rather than by splicing
    yaml, so the label value is written down as the YAML type it is --
    ``MetaMixin`` renders the block through ``yaml.safe_dump``, so an
    ``int`` stays an int and a list stays a list.
    """
    return a_gene_models_resource_built_from(
        tmp_path, a_gene_models().with_labels(reference_genome=value))


@pytest.mark.parametrize("builder", [
    pytest.param(
        a_gene_models().with_raw_labels("hg38"), id="labels-as-a-string"),
    pytest.param(
        a_gene_models().with_raw_labels(["a", "b"]), id="labels-as-a-list"),
    pytest.param(
        a_gene_models().with_raw_labels(2019), id="labels-as-an-int"),
    pytest.param(
        a_gene_models().with_raw_meta("some text"), id="meta-as-a-string"),
    pytest.param(
        a_gene_models().with_raw_meta(["a", "b"]), id="meta-as-a-list"),
])
def test_the_schema_still_refuses_a_non_mapping_meta_level(
    tmp_path: pathlib.Path,
    builder: Any,
) -> None:
    """Validation stays the guard; the accessor is only the backstop.

    Reading through the narrowing accessor must not quietly make a
    malformed ``gene_models`` resource loadable -- the base schema types
    both levels, and a resource that violates it is still refused before
    any label is read, at the same place and with the same error as
    before (gain#1009).
    """
    resource = a_gene_models_resource_built_from(tmp_path, builder)

    with pytest.raises(ValueError, match="Invalid configuration"):
        GeneModels(resource)


def test_a_narrowed_away_labels_block_leaves_no_reference_genome(
    tmp_path: pathlib.Path,
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
    resource = a_gene_models_whose_reference_genome_is(tmp_path, "hg38")
    mocker.patch.object(GenomicResource, "get_labels", return_value={})

    gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id is None


@pytest.mark.parametrize(("value", "reported_as"), [
    pytest.param(2019, "int", id="an-int"),
    pytest.param(0, "int", id="the-int-zero"),
    pytest.param(False, "bool", id="a-bool"),
    pytest.param(["a", "b"], "list", id="a-list"),
    pytest.param({"k": "v"}, "dict", id="a-nested-mapping"),
    pytest.param("", "empty", id="an-empty-string"),
])
def test_every_unusable_reference_genome_label_reads_as_absent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    value: Any,
    reported_as: str,
) -> None:
    """The six values a curator can write that cannot name a resource.

    They divide by how they fail rather than by type: the three truthy
    ones reach genome resolution and die there with a ``TypeError``
    naming nothing, and the three falsy ones are swallowed by the ``or``
    chain every reader of the attribute chains it with.  Both halves are
    the same defect -- a value that is not a resource id -- so both are
    narrowed here, at the one place that reads the label.
    """
    resource = a_gene_models_whose_reference_genome_is(tmp_path, value)

    with caplog.at_level(logging.WARNING):
        gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id is None
    warnings = captured_warnings(caplog)
    assert len(warnings) == 1
    assert A_GENE_MODELS_ID in warnings[0]
    assert reported_as in warnings[0]


@pytest.mark.parametrize("builder", [
    pytest.param(a_gene_models(), id="no-meta-block-at-all"),
    pytest.param(
        a_gene_models().with_meta(description="prose"),
        id="meta-without-labels"),
    pytest.param(
        a_gene_models().with_raw_labels(None),
        id="labels-an-explicit-yaml-null"),
    pytest.param(
        a_gene_models().with_labels(domain="gene"),
        id="labels-without-the-key"),
    pytest.param(
        a_gene_models().with_labels(reference_genome=None),
        id="the-key-an-explicit-yaml-null"),
])
def test_declaring_no_reference_genome_is_silent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    builder: Any,
) -> None:
    """Not declaring a genome is not a curator mistake, so it is not one.

    Every spelling of "no reference genome" -- down to the explicit YAML
    null the production GRRs carry -- has to stay silent, or the warning
    that means "fix this resource" fires on the resources that are
    already right.
    """
    resource = a_gene_models_resource_built_from(tmp_path, builder)

    with caplog.at_level(logging.WARNING):
        gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id is None
    assert captured_warnings(caplog) == []


def test_a_usable_reference_genome_label_is_read_and_is_silent(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The one shape that yields a value, and yields it quietly."""
    resource = a_gene_models_whose_reference_genome_is(tmp_path, "hg38")

    with caplog.at_level(logging.WARNING):
        gene_models = GeneModels(resource)

    assert gene_models.reference_genome_id == "hg38"
    assert captured_warnings(caplog) == []


def test_a_narrowed_label_falls_through_to_the_preamble_genome(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the narrowing buys, one seam up from the attribute.

    ``find_annotator_reference_genome`` chains the label with ``or``, so
    before the narrowing an ``int`` here was truthy, won the chain, and
    reached ``build_reference_genome_from_resource_id`` -- which ends in
    a regex and raised ``TypeError: expected string or bytes-like
    object, got 'int'``, naming neither the resource nor the label.

    Narrowed, the unusable label reads as absent and the chain moves on
    to the preamble, which is the documented precedence.  That is the
    decided trade: the hard failure becomes a warning that names the
    resource, and annotation proceeds against the genome the pipeline
    declares (gain#1050, on the gain#654 / gain#1004 terms -- validation
    refuses, reading degrades).
    """
    repo = (
        a_grr()
        .with_resource(
            A_GENE_MODELS_ID,
            a_gene_models().with_labels(reference_genome=2019))
        .with_resource(A_GENOME_ID, a_reference_genome())
        .build_repo(tmp_path)
    )
    with caplog.at_level(logging.WARNING):
        gene_models = GeneModels(repo.get_resource(A_GENE_MODELS_ID))

    genome = find_annotator_reference_genome(
        AnnotatorInfo("effect_annotator", [], {}), gene_models,
        _a_pipeline_declaring(repo, A_GENOME_ID), repo)

    assert genome.resource_id == A_GENOME_ID
    # The fall-through is not silent: the resource that lost its own
    # declaration is still named, which is what the TypeError never did.
    warnings = captured_warnings(caplog)
    assert len(warnings) == 1
    assert A_GENE_MODELS_ID in warnings[0]


def _a_pipeline_declaring(
    repo: Any, genome_id: str,
) -> AnnotationPipeline:
    """A pipeline whose preamble names ``genome_id`` and nothing else."""
    pipeline = AnnotationPipeline(repo)
    pipeline.preamble = AnnotationPreamble(
        summary="", description="",
        input_reference_genome=genome_id,
        input_reference_genome_res=None, metadata={})
    return pipeline
