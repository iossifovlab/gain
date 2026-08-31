# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""What a reader gets off a resource that carries no config (gain#1010).

``GenomicResource.config`` defaults to ``None``, and the repository
protocol really does build a config-less resource: ``build_genomic_resource``
constructs one as a bare handle purely so it has something to load
``genomic_resource.yaml`` *off*, and the in-memory repository builder does
the same while writing a resource's files.  So an unconfigured resource is
a live state, not a theoretical one.

``get_config()`` is the single place that decides what that state costs a
reader: it raises ``ValueError("use of unconfigured genomic resource:
<id>")``.  Every reader beside it inherits that decision by calling it.

These tests pin the wording at each seam that reads config off a resource,
because the wording is *inherited* rather than restated -- nothing else
held it down.  Four of those seams used to re-check the result for
``None`` and raise a second, differently-worded ``ValueError`` from a
branch that ``get_config()``'s own contract makes unreachable; removing
those branches must leave every message below unchanged.
"""
import pathlib

import pytest
from gain.gene_scores.gene_scores import GeneScore
from gain.gene_sets.gene_set import GeneSetCollection
from gain.gene_sets.implementations.gene_sets_impl import GeneSetCollectionImpl
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import build_filesystem_test_protocol

#: What ``get_config()`` raises, and so what every reader below must
#: surface.  The dead branches raised ``"resource <id> not configured"``
#: and ``"genomic resource <id> not configured"`` instead -- wordings no
#: caller could ever observe.
UNCONFIGURED = "use of unconfigured genomic resource: scores/unconfigured"


def an_unconfigured_resource(tmp_path: pathlib.Path) -> GenomicResource:
    """A resource built the way the protocol builds a bare handle."""
    proto = build_filesystem_test_protocol(tmp_path)
    return GenomicResource("scores/unconfigured", (0,), proto)


def test_get_type_reports_the_unconfigured_resource(
    tmp_path: pathlib.Path,
) -> None:
    resource = an_unconfigured_resource(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        resource.get_type()

    assert str(excinfo.value) == UNCONFIGURED


def test_gene_score_reports_the_unconfigured_resource(
    tmp_path: pathlib.Path,
) -> None:
    # Reached through `get_type()`, which the constructor calls to refuse
    # a resource of the wrong type before it reads any config of its own.
    resource = an_unconfigured_resource(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        GeneScore(resource)

    assert str(excinfo.value) == UNCONFIGURED


def test_gene_set_collection_reports_the_unconfigured_resource(
    tmp_path: pathlib.Path,
) -> None:
    # The constructor reads the config first thing, to validate it against
    # the collection's schema.
    resource = an_unconfigured_resource(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        GeneSetCollection(resource)

    assert str(excinfo.value) == UNCONFIGURED


def test_gene_set_collection_impl_reports_the_unconfigured_resource(
    tmp_path: pathlib.Path,
) -> None:
    # This one never used the config it read, so its read was dropped
    # whole rather than just unwrapped.  Several independent reads keep
    # the report: the implementation base class reads the config in
    # `__init__`, which runs first, and building the collection reads it
    # twice more -- once for the memo key, once in the collection's own
    # constructor.  Neutering any one of them leaves this test green --
    # so it pins the message, not the route, which is the only thing a
    # caller can observe anyway.
    resource = an_unconfigured_resource(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        GeneSetCollectionImpl(resource)

    assert str(excinfo.value) == UNCONFIGURED
