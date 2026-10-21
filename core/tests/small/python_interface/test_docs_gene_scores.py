"""Pin the offline claims of the "Gene scores" page.

The tests run the code that the page describes and assert the results
that the page states. They read no ``.rst`` file. The page is
``docs/source/development/resources/gene_scores.rst``. The examples of the
page use ``gene_properties/gene_scores/LGD`` and show its values; the tests
use the mini-GRR gene score ``mini_genescore`` instead, with the one gene
``geneA`` and the score ``my_genescore`` of 2. Missing values and several
scores need a resource that mini-GRR does not hold, so those tests build
one with ``a_gene_score``. The tests pin these claims:

- Both builders give a ``GeneScore``, which has no ``open()`` or
  ``close()`` and answers at once.
- ``get_gene_value`` returns ``None`` for an absent gene, a missing value
  or an unknown score id. The other reads raise ``ValueError`` for an
  unknown score id.
- ``to_dict`` and ``get_score_df`` leave out the missing values, and
  ``get_score_df`` has the columns ``gene`` and the score id.
- ``get_min``, ``get_max`` and ``get_values`` describe a score.
- ``get_genes`` takes an inclusive range or a list of values, and returns
  a set without the genes of missing values.
- ``score_definitions`` and ``get_score_definition`` give ``GeneScoreDef``
  objects, and ``get_score_histogram`` reads the histogram of a score.
- The score id can be left out of the histogram methods of a resource with
  one score.
"""
import pathlib

import pytest
from gain import grr
from gain.gene_scores.gene_scores import GeneScore, GeneScoreDef
from gain.genomic_resources.histogram import NumberHistogram
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing.builders import a_gene_score, a_grr

GENE_SCORE = "mini_genescore"
SCORE_ID = "my_genescore"

_WITH_MISSING = """
    gene   rank  score
    G1     1.0   0.5
    G2     2.0   NA
    G3     3.0   0.7
    G4     4.0   0.9
"""


@pytest.fixture(name="gene_score")
def fixture_gene_score(repo: GenomicResourceRepo) -> GeneScore:
    """The mini-GRR gene score."""
    return grr.build_gene_score_from_resource_id(GENE_SCORE, repo)


@pytest.fixture(name="with_missing")
def fixture_with_missing(tmp_path: pathlib.Path) -> GeneScore:
    """Two scores, and ``G2`` has no ``score`` value."""
    repo = a_grr().with_resource(
        "genes", a_gene_score().with_score("rank").with_score("score")
        .with_data(_WITH_MISSING),
    ).build_repo(tmp_path)
    return grr.build_gene_score_from_resource_id("genes", repo)


def test_both_builders_give_a_gene_score(repo: GenomicResourceRepo) -> None:
    """The builders take a resource id or a ``GenomicResource``."""
    from_resource = grr.build_gene_score_from_resource(
        repo.get_resource(GENE_SCORE))

    assert isinstance(
        grr.build_gene_score_from_resource_id(GENE_SCORE, repo), GeneScore)
    assert isinstance(from_resource, GeneScore)


def test_gene_score_needs_no_open(gene_score: GeneScore) -> None:
    """The builder loads the table, so the score answers at once."""
    assert not hasattr(gene_score, "open")
    assert not hasattr(gene_score, "close")
    assert gene_score.get_gene_value(SCORE_ID, "geneA") == 2


def test_get_gene_value_gives_none_when_it_has_no_answer(
    with_missing: GeneScore,
) -> None:
    """An absent gene, a missing value and an unknown score give ``None``."""
    assert with_missing.get_gene_value("score", "NO_SUCH_GENE") is None
    assert with_missing.get_gene_value("score", "G2") is None
    assert with_missing.get_gene_value("no_such_score", "G1") is None


@pytest.mark.parametrize("method", [
    "to_dict", "get_score_df", "get_min", "get_max", "get_values",
    "get_genes",
])
def test_other_reads_raise_for_an_unknown_score(
    gene_score: GeneScore, method: str,
) -> None:
    """Every other read refuses an unknown score id."""
    with pytest.raises(ValueError, match="no_such_score"):
        getattr(gene_score, method)("no_such_score")


def test_to_dict_leaves_out_missing_values(with_missing: GeneScore) -> None:
    """``G2`` has no value, so it is not a key."""
    assert with_missing.to_dict("score") == {
        "G1": 0.5, "G3": 0.7, "G4": 0.9}


def test_score_df_has_gene_and_score_columns(
    with_missing: GeneScore,
) -> None:
    """The data frame has ``gene`` and the score, without missing rows."""
    frame = with_missing.get_score_df("score")

    assert list(frame.columns) == ["gene", "score"]
    assert frame["gene"].tolist() == ["G1", "G3", "G4"]


def test_min_max_and_values(gene_score: GeneScore) -> None:
    """The minimum, the maximum and the list of values of a score."""
    assert gene_score.get_min(SCORE_ID) == 2
    assert gene_score.get_max(SCORE_ID) == 2
    assert gene_score.get_values(SCORE_ID) == [2]


def test_get_genes_range_is_inclusive(with_missing: GeneScore) -> None:
    """Both ends of the range are included, and the result is a set."""
    genes = with_missing.get_genes("rank", score_min=2.0, score_max=3.0)

    assert isinstance(genes, set)
    assert genes == {"G2", "G3"}


def test_get_genes_open_ends_are_unrestricted(
    with_missing: GeneScore,
) -> None:
    """A missing bound leaves that end of the range open."""
    assert with_missing.get_genes("rank", score_min=3.0) == {"G3", "G4"}
    assert with_missing.get_genes("rank", score_max=2.0) == {"G1", "G2"}


def test_get_genes_leaves_out_missing_values(
    with_missing: GeneScore,
) -> None:
    """``G2`` has no ``score`` value, so no range selects it."""
    assert with_missing.get_genes("score") == {"G1", "G3", "G4"}


def test_get_genes_by_values(gene_score: GeneScore) -> None:
    """``values`` selects the genes whose score is one of the values."""
    assert gene_score.get_genes(SCORE_ID, values=["2"]) == {"geneA"}


def test_score_definitions(with_missing: GeneScore) -> None:
    """The definitions are ``GeneScoreDef`` objects, in config order."""
    assert list(with_missing.score_definitions) == ["rank", "score"]
    score_def = with_missing.get_score_definition("rank")
    assert isinstance(score_def, GeneScoreDef)


def test_score_histogram(gene_score: GeneScore) -> None:
    """The histogram of a gene score counts genes."""
    hist = gene_score.get_score_histogram(SCORE_ID)

    assert isinstance(hist, NumberHistogram)
    assert sum(hist.bars) + sum(hist.out_of_range_bins) == 1


def test_single_score_id_can_be_left_out(gene_score: GeneScore) -> None:
    """A resource with one score answers the histogram methods without it."""
    assert gene_score.get_score_histogram().to_dict() == \
        gene_score.get_score_histogram(SCORE_ID).to_dict()
    assert gene_score.get_histogram_config() == \
        gene_score.get_histogram_config(SCORE_ID)
    assert gene_score.get_score_range() == \
        gene_score.get_score_range(SCORE_ID)


def test_several_scores_need_the_score_id(with_missing: GeneScore) -> None:
    """A resource with two scores refuses a call without a score id."""
    with pytest.raises(ValueError, match="rank"):
        with_missing.get_score_histogram()
