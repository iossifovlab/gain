# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any
from unittest.mock import ANY

import pytest
import yaml
from django.test import Client


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
                },
            ],
        },
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
                },
            ],
        },
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
        "/api/editor/pipeline_status?pipeline_id=t4c8/t4c8_pipeline",
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


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_aggregators(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]
    response = client.get("/api/editor/aggregators")

    assert response.status_code == 200
    aggregators = response.json()
    assert isinstance(aggregators, list)

    by_type = {a["aggregator_type"]: a for a in aggregators}

    assert "min" in by_type
    assert by_type["min"]["parametrized"] is False
    assert "default_parameter_value" not in by_type["min"]

    assert "join" in by_type
    assert by_type["join"]["parametrized"] is True
    assert by_type["join"]["default_parameter_value"] == ","


@pytest.mark.parametrize("current_client", ["admin", "user", "anonymous"])
def test_annotator_creation_workflow_with_aggregator(
    current_client: str, clients: dict[str, Client],
) -> None:
    client = clients[current_client]

    # Step 1: Get available aggregators
    response = client.get("/api/editor/aggregators")
    assert response.status_code == 200
    aggregators = {a["aggregator_type"]: a for a in response.json()}
    assert "join" in aggregators
    join_agg = aggregators["join"]

    # Step 2: Get valid aggregators for pos1
    response = client.post("/api/editor/annotator_aggregators", data={
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "pipeline_id": "pipeline/test_pipeline",
        "attribute_sources": ["pos1"],
    }, content_type="application/json")
    assert response.status_code == 200
    assert "join" in response.json()["pos1"]["aggregators"]

    # Step 3: Build YAML with aggregator as dict definition
    aggregator_dict = {
        "aggregator_type": join_agg["aggregator_type"],
        "parameters": [join_agg["default_parameter_value"]],
    }
    response = client.post("/api/editor/annotator_yaml", data={
        "pipeline_id": "pipeline/test_pipeline",
        "annotator_type": "position_score_annotator",
        "resource_id": "scores/pos1",
        "attributes": [{
            "name": "pos1",
            "source": "pos1",
            "internal": False,
            "aggregator": aggregator_dict,
        }],
    }, content_type="application/json")

    assert response.status_code == 200
    output = yaml.safe_load(response.json())
    assert output == [{
        "position_score_annotator": {
            "resource_id": "scores/pos1",
            "attributes": [{
                "name": "pos1",
                "source": "pos1",
                "internal": False,
                "aggregator": "join(,)",
            }],
        },
    }]
