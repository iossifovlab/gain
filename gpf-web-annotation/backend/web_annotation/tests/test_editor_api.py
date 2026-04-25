# pylint: disable=W0621,C0114,C0116,W0212,W0613
import textwrap
from typing import Any
import yaml

from unittest.mock import ANY

from django.test import Client
import pytest


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_types(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.get("/api/editor/annotator_types")

    assert response.status_code == 200
    assert set(response.json()) == {
        "allele_score_annotator",
        "position_score_annotator",
        "effect_annotator",
        "gene_set_annotator",
        "liftover_annotator",
        "normalize_allele_annotator",
        "gene_score_annotator",
        "simple_effect_annotator",
        "cnv_collection_annotator",
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
@pytest.mark.parametrize("annotator_type,extra_parameters,expected", [
    (
        "position_score_annotator",
        {},
        {
            "annotator_type": "position_score_annotator",
            "documentation_url": ANY,
            "resource_id": {
                "field_type": "resource",
                "resource_type": "position_score",
                "optional": False,
            },
            "input_annotatable": {
                "field_type": "attribute",
                "attribute_type": "annotatable",
                "optional": True,
            },
        },
    ),
    (
        "gene_set_annotator",
        {},
        {
            "annotator_type": "gene_set_annotator",
            "documentation_url": ANY,
            "resource_id": {
                "field_type": "resource",
                "resource_type": "gene_set_collection",
                "optional": False,
            },
            "input_gene_list": {
                "field_type": "attribute",
                "attribute_type": "gene_list",
                "optional": False,
            },
        },
    ),
])
def test_annotator_config(
    current_client: str, clients: dict[str, Client],
    annotator_type: str, extra_parameters: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_config", data={
        "annotator_type": annotator_type,
        **extra_parameters,
    }, content_type="application/json")

    assert response.status_code == 200
    config = response.json()
    assert config == expected


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_attributes_position_score(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    }, content_type="application/json")

    assert response.status_code == 200
    assert response.json() == {
        "page": 0,
        "total_pages": 1,
        "total_attributes": 1,
        "attributes": [{
            "name": "pos1",
            "source": "pos1",
            "type": "float",
            "description": "test position score",
            "default": True,
            "internal": False,
        }]
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_attributes_cnv_collection(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "cnv_collection",
        "resource_id": "cnv_collections/test_collection",
        "pipeline_id": "pipeline/test_pipeline",
    }, content_type="application/json")

    assert response.status_code == 200
    json = response.json()

    assert json["page"] == 0
    assert json["total_pages"] == 1
    assert json["total_attributes"] == 4
    assert json["attributes"][0] == {
        "name": "count",
        "source": "count",
        "type": "int",
        "description": (
            "The number of CNVs overlapping with the annotatable."
        ),
        "default": True,
        "internal": False,
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_effect_annotator_attributes(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
    }, content_type="application/json")

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["page"] == 0
    assert response_data["total_pages"] == 2
    assert response_data["total_attributes"] == 61
    assert len(response_data["attributes"]) == 50
    attr_names = [attr["name"] for attr in response_data["attributes"]]

    assert "worst_effect" in attr_names
    assert "worst_effect_genes" in attr_names
    assert "gene_effects" in attr_names
    assert "effect_details" in attr_names
    assert "gene_list" in attr_names


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_attributes_search(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
        "search": "worst_effect",
    }, content_type="application/json")

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["page"] == 0
    assert response_data["total_pages"] == 1
    assert response_data["total_attributes"] == 3
    assert len(response_data["attributes"]) == 3
    assert response_data["attributes"][0]["name"] == "worst_effect"
    assert response_data["attributes"][1]["name"] == "worst_effect_genes"
    assert response_data["attributes"][2]["name"] == "worst_effect_gene_list"

    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "effect_annotator",
        "genome": "t4c8/t4c8_genome",
        "gene_models": "t4c8/t4c8_genes",
        "pipeline_id": "pipeline/test_pipeline",
        "search": "all transcripts",
    }, content_type="application/json")

    assert response.status_code == 200
    response_data = response.json()
    assert response_data["page"] == 0
    assert response_data["total_pages"] == 1
    assert response_data["total_attributes"] == 1
    assert len(response_data["attributes"]) == 1
    assert response_data["attributes"][0]["name"] == "worst_effect"


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_yaml_position_score(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {
                "name": "pos1",
                "source": "pos1",
                "internal": False,
            }
        ]
    }, content_type="application/json")

    assert response.status_code == 200
    yaml_output = response.json()

    output = yaml.safe_load(yaml_output)
    assert output == [{
        "position_score": {
            "resource_id": "scores/pos1",
            "attributes": [
                {
                    "name": "pos1",
                    "source": "pos1",
                    "internal": False,
                }
            ],
        }
    }]


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_yaml_position_score_exact_format(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {
                "name": "pos1",
                "source": "pos1",
                "internal": False,
            }
        ]
    }, content_type="application/json")

    assert response.status_code == 200
    yaml_output = response.json()

    assert yaml_output.strip() == textwrap.dedent("""
    - position_score:
        resource_id: scores/pos1
        attributes:
        - name: pos1
          source: pos1
          internal: false
    """).strip()


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_yaml_position_score_errors_on_name_clash(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score",
        "resource_id": "scores/pos1",
        "attributes": [
            {
                "name": "position_1",
                "source": "pos1",
                "internal": False,
            }
        ]
    }, content_type="application/json")

    assert response.status_code == 400
    error = response.json()["error"]

    assert error == (
        "Invalid annotator configuration: "
        "Repeated attributes in pipeline were found - {'position_1': ['A0']}"
    )


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_creation_workflow(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    # Step 1: Get annotator types
    response = client.get("/api/editor/annotator_types")
    assert response.status_code == 200
    annotator_types = response.json()
    assert "position_score_annotator" in annotator_types

    # Step 2: Get annotator config
    response = client.post("/api/editor/annotator_config", data={
        "annotator_type": "position_score_annotator",
    }, content_type="application/json")
    assert response.status_code == 200
    config = response.json()
    assert config == {
        "annotator_type": "position_score_annotator",
        "documentation_url": ANY,
        "resource_id": {
            "field_type": "resource",
            "resource_type": "position_score",
            "optional": False,
        },
        "input_annotatable": {
            "field_type": "attribute",
            "attribute_type": "annotatable",
            "optional": True,
        },
    }
    assert config["annotator_type"] == "position_score_annotator"

    # Step 3: Get position scores

    response = client.get("/api/resources?type=position_score")
    assert response.status_code == 200
    resources = response.json()
    assert "scores/pos1" in resources

    # Step 4: Get annotator attributes
    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    }, content_type="application/json")
    assert response.status_code == 200
    json = response.json()
    assert len(json["attributes"]) == 1
    assert json["attributes"][0]["name"] == "pos1"
    json["attributes"][0]["name"] = "pos1_score"

    # Step 5: Get annotator YAML
    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "attributes": [
            {
                "name": attr["name"],
                "source": attr["source"],
                "internal": attr["internal"],
            }
            for attr in json["attributes"]
        ],
    }, content_type="application/json")
    assert response.status_code == 200
    yaml_output = response.json()

    output = yaml.safe_load(yaml_output)
    expected = [{
        "position_score_annotator": {
            "resource_id": "scores/pos1",
            "attributes": [
                {
                    "name": "pos1_score",
                    "source": "pos1",
                    "internal": False,
                }
            ],
        }
    }]
    assert output == expected


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_creation_resource_workflow(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    # Step 1: Get resource types
    response = client.get("/api/resources/types")
    assert response.status_code == 200
    assert "position_score" in response.json()

    # Step 2: Get resources of type position_score
    response = client.get("/api/resources?type=position_score")
    assert response.status_code == 200

    assert "scores/pos1" in response.json()

    # Step 3: Get available annotators for the resource
    response = client.get("/api/editor/resource_annotators", query_params={
        "resource_id": "scores/pos1",
    }, content_type="application/json")
    assert response.status_code == 200
    annotators = response.json()
    assert "configs" in annotators
    assert "default" in annotators
    assert annotators["default"] in annotators["configs"]
    assert annotators["default"] == "position_score_annotator"
    annotator_configs = annotators["configs"]
    assert len(annotator_configs) == 1
    annotator = annotator_configs[annotators["default"]]
    assert annotator["annotator_type"] == "position_score_annotator"
    assert annotator["resource_id"] == "scores/pos1"

    # Step 4: Get annotator config
    response = client.post(
        "/api/editor/annotator_config",
        data=annotator,
        content_type="application/json",
    )
    assert response.status_code == 200
    config = response.json()
    assert config["annotator_type"] == "position_score_annotator"
    assert config["resource_id"]["value"] == "scores/pos1"

    # Step 5: Get annotator attributes
    response = client.post("/api/editor/annotator_attributes", data={
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
    }, content_type="application/json")
    assert response.status_code == 200
    json = response.json()
    assert len(json["attributes"]) == 1
    assert json["attributes"][0]["name"] == "pos1"
    json["attributes"][0]["name"] = "pos1_score"

    # Step 6: Get annotator YAML
    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "attributes": [
            {
                "name": attr["name"],
                "source": attr["source"],
                "internal": attr["internal"],
            }
            for attr in json["attributes"]
        ],
    }, content_type="application/json")
    assert response.status_code == 200
    yaml_output = response.json()

    output = yaml.safe_load(yaml_output)
    expected = [{
        "position_score_annotator": {
            "resource_id": "scores/pos1",
            "attributes": [
                {
                    "name": "pos1_score",
                    "source": "pos1",
                    "internal": False,
                }
            ],
        }
    }]
    assert output == expected


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pipeline_status(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    response = client.get(
        "/api/editor/pipeline_status?pipeline_id=pipeline/test_pipeline",
    )
    assert response.status_code == 200

    assert response.json() == {
        "attributes_count": 1,
        "annotators_count": 1,
        "annotatables": [],
        "gene_lists": [],
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pipeline_attributes(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    response = client.get(
        "/api/editor/pipeline_attributes?pipeline_id=pipeline/test_pipeline"
        "&attribute_type=attribute",
    )
    assert response.status_code == 200

    assert response.json() == ["position_1"]

    response = client.get(
        "/api/editor/pipeline_attributes?pipeline_id=pipeline/test_pipeline",
    )
    assert response.status_code == 200

    assert response.json() == ["position_1"]


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pipeline_status_t4c8(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    response = client.get(
        "/api/editor/pipeline_status?pipeline_id=t4c8/t4c8_pipeline"
    )
    assert response.status_code == 200

    assert response.json() == {
        "attributes_count": 5,
        "annotators_count": 2,
        "annotatables": [],
        "gene_lists": ["gene_list"],
    }


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_pipeline_attributes_t4c8(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    response = client.get(
        "/api/editor/pipeline_attributes?pipeline_id=t4c8/t4c8_pipeline"
        "&attribute_type=gene_list",
    )
    assert response.status_code == 200

    assert response.json() == ["gene_list"]

    response = client.get(
        "/api/editor/pipeline_attributes?pipeline_id=t4c8/t4c8_pipeline",
    )
    assert response.status_code == 200

    assert response.json() == [
        "worst_effect",
        "gene_effects",
        "effect_details",
        "gene_list",
        "t4c8_score",
    ]
