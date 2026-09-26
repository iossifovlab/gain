# pylint: disable=W0621,C0114,C0116
import pathlib

import pytest
from gain.gene_sets.gene_set import GeneSetCollection
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing.builders import a_grr
from gain.genomic_resources.testing.gene_set_collection_builder import (
    GeneSetCollectionBuilder,
    GeneSetSpec,
    ResourceValidationError,
    a_gene_set_collection,
)


def loaded(resource: GenomicResource) -> GeneSetCollection:
    return GeneSetCollection(resource).load()


def gene_sets_of(resource: GenomicResource) -> dict[str, list[str]]:
    """Each loaded set's name mapped to its genes, sorted."""
    return {
        gene_set.name: sorted(gene_set.syms)
        for gene_set in loaded(resource).get_all_gene_sets()
    }


def test_bare_builder_loads_its_default_gene_set(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_set_collection().build_resource(tmp_path)

    assert gene_sets_of(resource) == {
        "main_candidates": ["ANK2", "CHD8", "POGZ"],
    }


def test_authored_gene_sets_accumulate_and_replace_the_default(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_set_collection() \
        .with_gene_set("alpha", "The alpha set", genes=["G1", "G2"]) \
        .with_gene_set("beta", "The beta set", genes=["G3"]) \
        .build_resource(tmp_path)

    assert gene_sets_of(resource) == {"alpha": ["G1", "G2"], "beta": ["G3"]}


@pytest.mark.parametrize("desc", ["The alpha set", ""])
def test_gene_set_description_is_read_back(
    tmp_path: pathlib.Path, desc: str,
) -> None:
    resource = a_gene_set_collection() \
        .with_gene_set("alpha", desc, genes=["G1"]) \
        .build_resource(tmp_path)

    gene_set = loaded(resource).get_gene_set("alpha")

    assert gene_set is not None
    assert gene_set.desc == desc


def test_presentation_config_is_read_back(tmp_path: pathlib.Path) -> None:
    resource = a_gene_set_collection() \
        .with_id("candidates") \
        .with_web_label("Candidates") \
        .with_web_format_str("key| (|count|): |desc") \
        .build_resource(tmp_path)

    collection = loaded(resource)

    assert (
        collection.collection_id,
        collection.web_label,
        collection.web_format_str,
    ) == ("candidates", "Candidates", "key| (|count|): |desc")


def test_presentation_config_is_absent_unless_declared(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_set_collection().build_resource(tmp_path)

    config = resource.get_config()

    assert "web_label" not in config
    assert "web_format_str" not in config


def test_specialising_a_builder_leaves_the_base_unchanged(
    tmp_path: pathlib.Path,
) -> None:
    base = a_gene_set_collection() \
        .with_gene_set("alpha", "The alpha set", genes=["G1"])
    base.with_gene_set("beta", "The beta set", genes=["G2"])
    base.with_web_label("Changed")

    resource = base.build_resource(tmp_path)

    assert gene_sets_of(resource) == {"alpha": ["G1"]}
    assert loaded(resource).web_label == ""


def test_gene_set_does_not_share_the_callers_gene_list(
    tmp_path: pathlib.Path,
) -> None:
    genes = ["G1"]
    builder = a_gene_set_collection() \
        .with_gene_set("alpha", "The alpha set", genes=genes)
    genes.append("G2")

    resource = builder.build_resource(tmp_path)

    assert gene_sets_of(resource) == {"alpha": ["G1"]}


def test_composes_into_a_grr_by_resource_id(tmp_path: pathlib.Path) -> None:
    repo = a_grr() \
        .with_resource("gene_sets/alpha", a_gene_set_collection()
                       .with_gene_set("alpha", "The alpha set",
                                      genes=["G1"])) \
        .build_repo(tmp_path)

    resource = repo.get_resource("gene_sets/alpha")

    assert gene_sets_of(resource) == {"alpha": ["G1"]}


@pytest.mark.parametrize("second", ["alpha", "Alpha"])
def test_a_name_already_declared_ignoring_case_is_refused(
    second: str,
) -> None:
    builder = a_gene_set_collection() \
        .with_gene_set("alpha", "The alpha set", genes=["G1"])

    with pytest.raises(ResourceValidationError, match=repr(second)):
        builder.with_gene_set(second, "Another alpha", genes=["G2"])


@pytest.mark.parametrize(
    "name", ["", "a/b", ".hidden", " padded", "a\nb", "a\rb", "a\x00b"])
def test_a_name_that_is_not_one_file_name_is_refused(name: str) -> None:
    with pytest.raises(ResourceValidationError, match="gene set name"):
        a_gene_set_collection().with_gene_set(name, "desc", genes=["G1"])


@pytest.mark.parametrize("desc", ["two\nlines", "two\rlines", " padded "])
def test_a_description_that_is_not_one_stripped_line_is_refused(
    desc: str,
) -> None:
    with pytest.raises(ResourceValidationError, match="description"):
        a_gene_set_collection().with_gene_set("alpha", desc, genes=["G1"])


@pytest.mark.parametrize("gene", ["", " G1", "G1\nG2", "G1\rG2"])
def test_a_gene_that_is_not_one_line_is_refused(gene: str) -> None:
    with pytest.raises(ResourceValidationError, match=r"gene\(s\)"):
        a_gene_set_collection().with_gene_set(
            "alpha", "desc", genes=["G0", gene])


def test_duplicate_names_are_refused_when_constructed_directly() -> None:
    alpha = GeneSetSpec("alpha", "desc", ("G1",))

    with pytest.raises(ResourceValidationError, match="'alpha'"):
        GeneSetCollectionBuilder(gene_sets=(alpha, alpha))


@pytest.mark.parametrize("collection_id", ["", "denovo"])
def test_an_id_the_collection_cannot_open_is_refused(
    collection_id: str,
) -> None:
    with pytest.raises(ResourceValidationError, match="id"):
        a_gene_set_collection().with_id(collection_id)


def test_each_gene_set_is_written_to_a_file_named_after_it(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_set_collection() \
        .with_gene_set("alpha", "The alpha set", genes=["G1"]) \
        .with_gene_set("beta", "The beta set", genes=["G2"]) \
        .build_resource(tmp_path)

    assert GeneSetCollection(resource).files == {
        "GeneSets/alpha.txt", "GeneSets/beta.txt",
    }


def test_meta_is_read_back_through_the_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = a_gene_set_collection() \
        .with_meta(summary="Candidate genes") \
        .with_labels(species="human") \
        .build_resource(tmp_path)

    assert resource.get_summary() == "Candidate genes"
    assert resource.get_labels() == {"species": "human"}
