# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The liftover config the editor resolves out of a chain's labels.

``meta.labels`` is free-form YAML, so a chain resource may declare it as
something other than a mapping, and a label's VALUE as something other
than a resource id. The editor's liftover branch has to read it the way
every other reader does -- through ``get_labels`` -- and to ask whether a
label KEY is there rather than whether a string contains it (gain#654);
and a value that cannot name a resource has to be left out of the config
it offers, reported, rather than copied through for ``get_resource`` to
choke on later (gain#1053, gain#1079).
"""
import logging
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


def _label_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Only the warnings a ``meta.labels`` read emitted."""
    return [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING
        and "meta.labels." in record.getMessage()
    ]


GOOD_IDS = {"source_genome": "hg19", "target_genome": "hg38"}


def _labels_with(label: str, value: Any) -> dict[str, Any]:
    """The two usable ids, with ``label`` replaced by ``value``."""
    return {**GOOD_IDS, label: value}


def test_the_chain_labels_name_the_source_and_target_genomes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    grr = _grr_with_a_chain(GOOD_IDS)

    with caplog.at_level(logging.WARNING):
        config = _liftover_annotator_config(grr)

    assert config["source_genome"] == "hg19"
    assert config["target_genome"] == "hg38"
    assert _label_warnings(caplog) == []


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


@pytest.mark.parametrize("label", ["source_genome", "target_genome"])
@pytest.mark.parametrize(("value", "reason"), [
    (2019, "int"),
    (["hg19", "hg38"], "list"),
    ({"id": "hg19"}, "dict"),
    ("", "empty"),
])
def test_a_label_value_that_cannot_be_an_id_is_left_out_and_reported(
    caplog: pytest.LogCaptureFixture, label: str, value: Any, reason: str,
) -> None:
    """Only the bad label is dropped; the warning says which and why."""
    grr = _grr_with_a_chain(_labels_with(label, value))
    other = next(name for name in GOOD_IDS if name != label)

    with caplog.at_level(logging.WARNING):
        config = _liftover_annotator_config(grr)

    assert label not in config
    assert config[other] == GOOD_IDS[other]
    warnings = _label_warnings(caplog)
    assert len(warnings) == 1
    assert f"<{CHAIN_ID}>" in warnings[0]
    assert label in warnings[0]
    assert reason in warnings[0]


@pytest.mark.parametrize("label", ["source_genome", "target_genome"])
def test_an_explicit_null_label_is_left_out_silently(
    caplog: pytest.LogCaptureFixture, label: str,
) -> None:
    """The spelling the production GRRs carry is not a curator mistake."""
    grr = _grr_with_a_chain(_labels_with(label, None))
    other = next(name for name in GOOD_IDS if name != label)

    with caplog.at_level(logging.WARNING):
        config = _liftover_annotator_config(grr)

    assert label not in config
    assert config[other] == GOOD_IDS[other]
    assert _label_warnings(caplog) == []
