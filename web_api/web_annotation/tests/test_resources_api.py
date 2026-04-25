# pylint: disable=W0621,C0114,C0116,W0212,W0613
from django.test import Client
import pytest


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
@pytest.mark.parametrize(
    "type_filter,search_term,expected",
    [
        (
            None, None,
            {
                "page": 0,
                "pages": 1,
                "total_resources": 9,
                "resources": {
                    "hg38/GRCh38-hg38/genome",
                    "scores/pos1",
                    "scores/pos2",
                    "t4c8/gene_scores/t4c8_score",
                    "t4c8/gene_sets/main",
                    "t4c8/genomic_scores/score_one",
                    "t4c8/t4c8_genes",
                    "t4c8/t4c8_genome",
                    "cnv_collections/test_collection",
                }
            }
        ),
        (
            "genome", None,
            {
                "page": 0,
                "pages": 1,
                "total_resources": 2,
                "resources": {
                    "hg38/GRCh38-hg38/genome",
                    "t4c8/t4c8_genome",
                }
            }
        ),
        (
            "gene_set_collection", None,
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "resources": {
                    "t4c8/gene_sets/main",
                 }
            }
        ),
        (
            "position_score", None,
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 3,
                 "resources": {
                    "scores/pos1",
                    "scores/pos2",
                    "t4c8/genomic_scores/score_one",
                 }
            }
        ),
        (
            "position_score", "score_one",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "resources": {
                    "t4c8/genomic_scores/score_one",
                 }
            }
        ),
        (
            None, "score_one",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 1,
                 "resources": {
                    "t4c8/genomic_scores/score_one",
                 }
            },
        ),
        (
            None, "t4c8",
            {
                 "page": 0,
                 "pages": 1,
                 "total_resources": 5,
                 "resources": {
                    "t4c8/gene_scores/t4c8_score",
                    "t4c8/gene_sets/main",
                    "t4c8/genomic_scores/score_one",
                    "t4c8/t4c8_genes",
                    "t4c8/t4c8_genome",
                 }
            },
        ),
    ],
)
def test_get_resources(
    current_client: str, clients: dict[str, Client],
    type_filter: str | None,
    search_term: str | None,
    expected: list[str],
) -> None:
    client = clients[current_client]

    query_params = {}
    if type_filter:
        query_params["type"] = type_filter
    if search_term:
        query_params["search"] = search_term

    response = client.get("/api/resources/search", query_params=query_params)

    assert response.status_code == 200
    result = response.json()
    result["resources"] = {res["resource_id"] for res in result["resources"]}
    assert result == expected


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pagination(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    query_params = {"page_size": 2}
    response = client.get("/api/resources/search", query_params=query_params)
    assert response.status_code == 200
    response_json = response.json()
    assert len(response_json["resources"]) == 2
    assert response_json["pages"] == 5
    assert response_json["page"] == 0


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_get_resource_types(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.get("/api/resources/types")

    assert response.status_code == 200
    assert set(response.json()) == {
        "genome",
        "gene_models",
        "liftover_chain",
        "gene_set_collection",
        "position_score",
        "allele_score",
        "gene_score",
        "cnv_collection",
    }
