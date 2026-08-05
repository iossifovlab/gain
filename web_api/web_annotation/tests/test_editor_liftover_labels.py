# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The liftover config the editor resolves out of a chain's labels.

``meta.labels`` is free-form YAML, so a chain resource may declare it as
something other than a mapping. The editor's liftover branch has to read
it the way every other reader does -- through ``get_labels`` -- and to ask
whether a label KEY is there rather than whether a string contains it
(gain#654).
"""
from typing import Any

import pytest
import yaml
from gain.genomic_resources.repository import GenomicResourceRepo
from gain.genomic_resources.testing import build_inmemory_test_repository
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from web_annotation.editor.views import ResourceAnnotators

CHAIN_ID = "chains/hg19_to_hg38"


def _grr_with_a_chain(labels: Any) -> GenomicResourceRepo:
    """A repository holding one chain resource with the given labels."""
    return build_inmemory_test_repository({
        CHAIN_ID: {
            "genomic_resource.yaml": yaml.safe_dump({
                "type": "liftover_chain",
                "filename": "liftover.chain.gz",
                "meta": {"labels": labels},
            }),
        },
    })


def _liftover_annotator_config(grr: GenomicResourceRepo) -> dict[str, Any]:
    """The config the editor offers for the chain resource in ``grr``."""
    view = ResourceAnnotators()
    view._grr = grr
    response = view.get(Request(APIRequestFactory().get(
        "/api/editor/resource_annotators", {"resource_id": CHAIN_ID})))

    assert response.status_code == 200
    configs = response.data["configs"]
    assert "liftover_annotator" in configs
    return dict(configs["liftover_annotator"])


def test_the_chain_labels_name_the_source_and_target_genomes() -> None:
    config = _liftover_annotator_config(_grr_with_a_chain(
        {"source_genome": "hg19", "target_genome": "hg38"}))

    assert config["source_genome"] == "hg19"
    assert config["target_genome"] == "hg38"


@pytest.mark.parametrize("labels", [
    "some text",
    # The string that passes a *substring* guard and then indexes a str
    # with a str: `"source_genome" in labels` is True here.
    "source_genome hg38",
    ["source_genome", "hg38"],
    2019,
])
def test_a_chain_whose_labels_are_not_a_mapping_resolves_a_config(
    labels: Any,
) -> None:
    """Neither a raise nor a genome id invented out of a substring."""
    config = _liftover_annotator_config(_grr_with_a_chain(labels))

    assert config["annotator_type"] == "liftover_annotator"
    assert "source_genome" not in config
    assert "target_genome" not in config
