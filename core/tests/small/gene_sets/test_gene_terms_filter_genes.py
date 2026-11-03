# pylint: disable=C0114,C0116
from gain.gene_sets.gene_term import GeneTerms


def _gene_terms() -> GeneTerms:
    terms = GeneTerms()
    terms.g2t["G1"]["T1"] = 1
    terms.g2t["G1"]["T2"] = 2
    terms.g2t["G2"]["T2"] = 1
    terms.g2t["G3"]["T3"] = 3
    for gene, gene_terms in terms.g2t.items():
        for term, count in gene_terms.items():
            terms.t2g[term][gene] = count
    terms.t_desc = {"T1": "term one", "T2": "term two", "T3": "term three"}
    terms.gene_ns = "sym"
    return terms


def test_filter_genes_keeps_only_the_genes_that_the_filter_returns() -> None:
    terms = _gene_terms()

    terms.filter_genes(lambda genes: [g for g in genes if g != "G3"])

    assert {g: dict(ts) for g, ts in terms.g2t.items()} == {
        "G1": {"T1": 1, "T2": 2},
        "G2": {"T2": 1},
    }


def test_filter_genes_rebuilds_the_term_to_gene_map() -> None:
    terms = _gene_terms()

    terms.filter_genes(lambda genes: [g for g in genes if g != "G1"])

    assert {t: dict(gs) for t, gs in terms.t2g.items()} == {
        "T2": {"G2": 1},
        "T3": {"G3": 3},
    }


def test_filter_genes_drops_the_description_of_a_term_with_no_genes() -> None:
    terms = _gene_terms()

    terms.filter_genes(lambda genes: [g for g in genes if g != "G1"])

    assert terms.t_desc == {"T2": "term two", "T3": "term three"}


def test_filter_genes_passes_the_gene_names_to_the_filter() -> None:
    terms = _gene_terms()
    seen: list[list[str]] = []

    def keep_all(genes: list[str]) -> list[str]:
        seen.append(genes)
        return genes

    terms.filter_genes(keep_all)

    assert seen == [["G1", "G2", "G3"]]
