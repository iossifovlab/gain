# pylint: disable=W0621,C0114,C0116,W0212,W0613
import gzip
import logging
import os
import pathlib
from collections.abc import Callable

import pytest
from gain.gene_sets.gene_term import (
    GeneInfo,
    GeneTerms,
    NCBIGeneInfo,
    get_clean_gene_id,
    load_gene_terms,
    load_ncbi_gene_info,
    rename_gene_terms,
)


@pytest.fixture
def fixture_filename() -> Callable:
    def _fixture_filename(filename: str) -> str:
        return os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "fixtures",
            filename)

    return _fixture_filename


def test_load_ncbi_gene_info(fixture_filename: Callable[[str], str]) -> None:
    gene_info_file = fixture_filename("reduced-humanGeneInfo.txt")
    result = load_ncbi_gene_info(gene_info_file)

    assert result is not None


def test_load_protein_domains(fixture_filename: Callable[[str], str]) -> None:
    domains_filename = fixture_filename("reduced-Domain-map.txt")
    domains = load_gene_terms(domains_filename)
    assert domains is not None

    gene_info_file = fixture_filename("reduced-humanGeneInfo.txt")
    ncbi_gene_info = load_ncbi_gene_info(gene_info_file)

    result = rename_gene_terms(domains, "sym", ncbi_gene_info)

    assert result is not None
    assert len(result.t2g["CRF"]) == 2
    assert set(result.t2g["CRF"].keys()) == {"NOS1AP", "UCN2"}
    assert len(result.t2g["TAFH"]) == 5
    assert set(result.t2g["TAFH"].keys()) == {
        "CBFA2T3",
        "TAF4B",
        "TAF4",
        "RUNX1T1",
        "CBFA2T2",
    }


def test_load_gmt_file(fixture_filename: Callable[[str], str]) -> None:
    gene_terms = load_gene_terms(
        fixture_filename("reduced-c2.all.v4.0.symbols.gmt"))
    assert gene_terms is not None

    assert len(gene_terms.t2g[
        "KEGG_PENTOSE_AND_GLUCURONATE_INTERCONVERSIONS"]) == 28

    assert set(gene_terms.t2g[
        "KEGG_PENTOSE_AND_GLUCURONATE_INTERCONVERSIONS"].keys()) == {
            "AKR1B1",
            "UGT2B7",
            "UGT1A10",
            "UGT1A6",
            "GUSB",
            "UGT2A3",
            "DHDH",
            "UGT2B28",
            "UGT1A4",
            "LOC729020",
            "RPE",
            "CRYL1",
            "UGT1A3",
            "UGT2A1",
            "UGT2B10",
            "UGT2B4",
            "UGDH",
            "UGT1A7",
            "UGT2B11",
            "XYLB",
            "UGT1A1",
            "UGT2B15",
            "UGP2",
            "UGT1A8",
            "UGT2B17",
            "UGT1A5",
            "DCXR",
          "UGT1A9",
        }


def test_load_gene_terms_gzipped_map(tmp_path: pathlib.Path) -> None:
    map_gz = tmp_path / "test-map.txt.gz"
    map_gz.write_bytes(gzip.compress(
        b"#geneNS\tsym\n"
        b"POGZ\ttest:01 test:02\n"
        b"CHD8\ttest:02 test:03\n",
    ))
    (tmp_path / "test-mapnames.txt").write_text(
        "test:01\ttest_first\n"
        "test:02\ttest_second\n"
        "test:03\ttest_third\n",
    )

    gene_terms = load_gene_terms(str(map_gz))
    assert gene_terms is not None
    assert set(gene_terms.t2g["test:01"].keys()) == {"POGZ"}
    assert set(gene_terms.t2g["test:02"].keys()) == {"POGZ", "CHD8"}
    assert gene_terms.t_desc["test:01"] == "test_first"
    assert gene_terms.t_desc["test:02"] == "test_second"
    assert gene_terms.t_desc["test:03"] == "test_third"


def test_load_gene_sets_directory(
    fixture_filename: Callable[[str], str],
) -> None:

    gene_terms = load_gene_terms(
        fixture_filename("GeneSets"))
    assert gene_terms is not None

    assert len(gene_terms.t2g["Main Candidates"]) == 9
    assert set(gene_terms.t2g["Main Candidates"].keys()) == {
        "POGZ",
        "CHD8",
        "ANK2",
        "FAT4",
        "NBEA",
        "CELSR1",
        "USP7",
        "GOLGA5",
        "PCSK2",
    }
    assert gene_terms.t_desc["Main Candidates"] == \
        "Description of gene set Main Candidates"


def _gene_info(gene_id: str, gene_sym: str) -> GeneInfo:
    return GeneInfo(
        gene_id=gene_id, gene_sym=gene_sym, synonyms=set(), description="")


@pytest.fixture
def small_ncbi_gene_info() -> NCBIGeneInfo:
    pogz = _gene_info("23126", "POGZ")
    chd8 = _gene_info("57680", "CHD8")
    dup_a = _gene_info("1", "DUP")
    dup_b = _gene_info("2", "DUP")
    return NCBIGeneInfo(
        genes={gi.gene_id: gi for gi in (pogz, chd8, dup_a, dup_b)},
        ns_tokens={
            "sym": {
                "POGZ": [pogz],
                "CHD8": [chd8],
                "DUP": [dup_a, dup_b],
            },
        },
    )


def test_get_clean_gene_id_returns_the_gene_id_of_a_unique_term(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    assert get_clean_gene_id(small_ncbi_gene_info, "sym", "POGZ") == "23126"


def test_get_clean_gene_id_returns_none_for_a_missing_namespace(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    assert get_clean_gene_id(small_ncbi_gene_info, "syns", "POGZ") is None


def test_get_clean_gene_id_returns_none_for_a_missing_term(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    assert get_clean_gene_id(small_ncbi_gene_info, "sym", "NOGENE") is None


def test_get_clean_gene_id_returns_none_for_an_ambiguous_term(
    small_ncbi_gene_info: NCBIGeneInfo,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        result = get_clean_gene_id(small_ncbi_gene_info, "sym", "DUP")

    assert result is None
    assert "multiple tokens for term DUP in name space sym" in caplog.text


def _sym_gene_terms() -> GeneTerms:
    gene_terms = GeneTerms()
    gene_terms.gene_ns = "sym"
    for gene, term in [
        ("POGZ", "t1"), ("CHD8", "t1"), ("DUP", "t2"), ("NOGENE", "t3"),
    ]:
        gene_terms.g2t[gene][term] += 1
        gene_terms.t2g[term][gene] += 1
    gene_terms.t_desc = {"t1": "first", "t2": "second", "t3": "third"}
    return gene_terms


def test_rename_gene_terms_from_sym_to_id_keeps_unique_genes(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    gene_terms = _sym_gene_terms()

    result = rename_gene_terms(gene_terms, "id", small_ncbi_gene_info)

    assert result.gene_ns == "id"
    assert dict(result.t2g) == {"t1": {"23126": 1, "57680": 1}}
    assert result.t_desc == {"t1": "first"}


def test_rename_gene_terms_from_sym_to_id_leaves_the_input_unchanged(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    gene_terms = _sym_gene_terms()

    rename_gene_terms(gene_terms, "id", small_ncbi_gene_info)

    assert gene_terms.gene_ns == "sym"
    assert set(gene_terms.g2t) == {"POGZ", "CHD8", "DUP", "NOGENE"}
    assert gene_terms.t_desc == {"t1": "first", "t2": "second", "t3": "third"}


def test_rename_gene_terms_to_the_same_namespace_returns_a_copy(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    gene_terms = _sym_gene_terms()

    result = rename_gene_terms(gene_terms, "sym", small_ncbi_gene_info)

    assert result is not gene_terms
    assert result.g2t is not gene_terms.g2t
    assert result.t2g is not gene_terms.t2g
    assert result.t_desc is not gene_terms.t_desc
    assert result.gene_ns == "sym"
    assert dict(result.g2t) == dict(gene_terms.g2t)


def test_rename_gene_terms_rejects_an_unknown_namespace(
    small_ncbi_gene_info: NCBIGeneInfo,
) -> None:
    gene_terms = _sym_gene_terms()

    with pytest.raises(AssertionError, match="must be either 'id' or 'sym'"):
        rename_gene_terms(gene_terms, "syns", small_ncbi_gene_info)


def test_gene_terms_save_to_a_map_file_name(tmp_path: pathlib.Path) -> None:
    gene_terms = _sym_gene_terms()
    map_file = tmp_path / "terms-map.txt"

    gene_terms.save(str(map_file))

    assert map_file.read_text() == (
        "#geneNS\tsym\n"
        "CHD8\tt1\n"
        "DUP\tt2\n"
        "NOGENE\tt3\n"
        "POGZ\tt1\n"
    )
    assert (tmp_path / "terms-mapnames.txt").read_text() == (
        "t1\tfirst\n"
        "t2\tsecond\n"
        "t3\tthird\n"
    )


def test_gene_terms_save_to_a_base_name_adds_the_suffixes(
    tmp_path: pathlib.Path,
) -> None:
    gene_terms = _sym_gene_terms()

    gene_terms.save(str(tmp_path / "terms"))

    assert (tmp_path / "terms-map.txt").read_text().startswith(
        "#geneNS\tsym\n")
    assert (tmp_path / "terms-mapnames.txt").read_text().startswith(
        "t1\tfirst\n")


def test_gene_terms_save_round_trips_through_load_gene_terms(
    tmp_path: pathlib.Path,
) -> None:
    gene_terms = _sym_gene_terms()
    gene_terms.g2t["POGZ"]["t2"] += 2
    gene_terms.t2g["t2"]["POGZ"] += 2
    map_file = tmp_path / "terms-map.txt"

    gene_terms.save(str(map_file))
    loaded = load_gene_terms(str(map_file))

    assert loaded is not None
    assert loaded.gene_ns == "sym"
    assert dict(loaded.t2g["t2"]) == {"DUP": 1, "POGZ": 2}
    assert loaded.t_desc == gene_terms.t_desc
