"""A ``gene_models`` config outside its schema is refused at construction.

The type runs the schema it declares (ADR 0031), so a config the schema
does not accept refuses the resource in ``GeneModels.__init__`` with a
``MalformedResourceError`` naming the resource -- never later, in
``load()``, with a bare ``KeyError`` naming nothing.
"""
# pylint: disable=W0621,C0114,C0116,W0212,W0613
from typing import Any

import pytest
import yaml
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.repository import GR_CONF_FILE_NAME
from gain.genomic_resources.resource_errors import MalformedResourceError
from gain.genomic_resources.testing import build_inmemory_test_repository

#: The resource's id, deep enough to look like a real one and distinctive
#: enough that finding it in a refusal means something (a single-resource
#: fixture would give it an EMPTY id, which every message matches).
A_GENE_MODELS_GROUP, A_GENE_MODELS_NAME = "gene_models", "models567"
A_GENE_MODELS_ID = f"{A_GENE_MODELS_GROUP}/{A_GENE_MODELS_NAME}"


def _gene_models_under_a_real_id_configured(
    config: dict[str, Any],
) -> GeneModels:
    """Construct ``GeneModels`` under ``A_GENE_MODELS_ID`` from ``config``.

    Hand-rolled yaml on purpose: ``a_gene_models()`` always writes a
    complete config, so it cannot author one without ``filename`` or
    with a key outside the schema.
    """
    resource = build_inmemory_test_repository({
        A_GENE_MODELS_GROUP: {
            A_GENE_MODELS_NAME: {GR_CONF_FILE_NAME: yaml.safe_dump(config)},
        },
    }).get_resource(A_GENE_MODELS_ID)
    return GeneModels(resource)


@pytest.mark.parametrize("config", [
    # A typo of `filename`.
    pytest.param({"type": "gene_models",
                  "filename": "genes.txt",
                  "file_name": "genes.txt"},
                 id="a-key-the-schema-does-not-know"),
    # No `filename` key at all: the one field gene models cannot do
    # without, refused here rather than on load.
    pytest.param({"type": "gene_models", "format": "refflat"},
                 id="no-filename"),
])
def test_a_gene_models_config_outside_its_schema_is_refused(
    config: dict[str, Any],
) -> None:
    """A config outside the schema refuses the resource at construction,
    naming the resource."""
    with pytest.raises(MalformedResourceError, match=A_GENE_MODELS_ID):
        _gene_models_under_a_real_id_configured(config)
