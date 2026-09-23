"""The ``filename`` key every file-backed resource type requires (gain#1613).

Five resource types read their ``filename`` unconditionally, so each one's
config schema requires it, from a single shared fragment.  One table here
proves the refusal for all five at their public construction seam.
"""
from collections.abc import Callable
from typing import Any

import pytest
from gain.gene_scores.gene_scores import build_gene_score_from_resource
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.genomic_scores import build_score_from_resource
from gain.genomic_resources.liftover_chain import (
    build_liftover_chain_from_resource,
)
from gain.genomic_resources.reference_genome import (
    build_reference_genome_from_resource,
)
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.resource_implementation import (
    get_required_filename_schema,
)
from gain.genomic_resources.testing import build_inmemory_test_repository

from .conftest import captured_warnings

#: A resource id distinctive enough that finding it in a refusal means
#: something (a single-resource fixture would give it an EMPTY id).
A_RESOURCE_GROUP, A_RESOURCE_NAME = "resources", "no_filename"
A_RESOURCE_ID = f"{A_RESOURCE_GROUP}/{A_RESOURCE_NAME}"

#: What cerberus reports for a top-level ``filename`` that is missing.
NO_FILENAME = "{'filename': ['required field']}"

_A_SCORES_BLOCK = (
    "scores:\n"
    "    - id: score\n"
    "      type: float\n"
)


@pytest.mark.parametrize(("config", "build", "expected_errors"), [
    pytest.param(
        "type: genome\n",
        build_reference_genome_from_resource,
        NO_FILENAME,
        id="genome"),
    pytest.param(
        "type: liftover_chain\n",
        build_liftover_chain_from_resource,
        NO_FILENAME,
        id="liftover_chain"),
    pytest.param(
        "type: gene_models\n",
        build_gene_models_from_resource,
        NO_FILENAME,
        id="gene_models"),
    pytest.param(
        "type: gene_score\n" + _A_SCORES_BLOCK,
        build_gene_score_from_resource,
        NO_FILENAME,
        id="gene_score"),
    pytest.param(
        "type: position_score\ntable:\n    format: tsv\n"
        + _A_SCORES_BLOCK + "      name: score\n",
        build_score_from_resource,
        "{'table': [{'filename': ['required field']}]}",
        id="position_score"),
])
def test_a_resource_without_its_filename_is_refused(
    config: str,
    build: Callable[[Any], object],
    expected_errors: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A config with no ``filename`` is refused at construction, naming the
    resource, and the logged errors name the key and nothing else."""
    resource = build_inmemory_test_repository({
        A_RESOURCE_GROUP: {A_RESOURCE_NAME: {GR_CONF_FILE_NAME: config}},
    }).get_resource(A_RESOURCE_ID)

    with pytest.raises(
            MalformedResourceError,
            match=f"Invalid configuration: {A_RESOURCE_ID}"):
        build(resource)

    # The whole error dict, not a substring of it: the missing filename is
    # the ONLY thing wrong with the config, so the refusal is its doing.
    [message] = captured_warnings(caplog)
    assert message.endswith(f". {expected_errors}")


def test_each_call_builds_its_own_fragment() -> None:
    """Every call returns a new fragment, down to the rule dict: cerberus
    rewrites nested rule dicts in place, so no two schemas may share one."""
    first = get_required_filename_schema()
    second = get_required_filename_schema()

    assert first == second
    assert first["filename"] is not second["filename"]
