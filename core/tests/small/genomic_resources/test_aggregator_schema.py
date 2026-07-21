"""The resource-config aggregator schema follows the aggregator registry.

``AGGREGATOR_SCHEMA`` validates the ``position_aggregator`` /
``allele_aggregator`` / ``nucleotide_aggregator`` fields of a score in a
``genomic_resource.yaml``.  It used to be a hand-written list of regexes
maintained alongside ``AGGREGATOR_CLASS_DICT``, and the two drifted.  These
tests pin the schema to the registry so a name that can be built is a name a
resource may configure.
"""

import pathlib
from typing import Any

import pytest
from cerberus import Validator
from gain.genomic_resources.aggregators import (
    AGGREGATOR_CLASS_DICT,
    AGGREGATOR_SCHEMA,
    Aggregator,
)
from gain.genomic_resources.genomic_scores import AlleleScore, PositionScore
from gain.genomic_resources.testing.builders import (
    a_grr,
    a_position_score,
    an_allele_score,
)


def _accepts(configured: Any) -> bool:
    """Return whether a resource may configure ``configured`` as aggregator."""
    validator = Validator({"position_aggregator": AGGREGATOR_SCHEMA})
    return bool(validator.validate({"position_aggregator": configured}))


def _configured_form(name: str) -> str:
    """Return how a resource YAML spells the registered aggregator ``name``.

    A parametrized aggregator is spelled ``name(parameter)``; every other one
    is spelled by its bare name.  Read off the registered class, so a newly
    registered aggregator needs no edit here either.
    """
    aggregator_class = AGGREGATOR_CLASS_DICT[name]
    if aggregator_class.parametrized:
        return f"{name}({aggregator_class.default_parameter})"
    return name


def test_count_is_accepted_as_a_resource_aggregator() -> None:
    assert _accepts("count")


@pytest.mark.parametrize("name", list(AGGREGATOR_CLASS_DICT))
def test_every_registered_aggregator_is_accepted(name: str) -> None:
    configured = _configured_form(name)

    assert Aggregator.build(configured) is not None
    assert _accepts(configured), \
        f"registered aggregator {configured!r} rejected by a resource config"


def test_join_with_an_empty_separator_is_accepted() -> None:
    """``join()`` builds -- as the ``concatenate`` equivalent -- so it passes.

    The old hand-written schema demanded a non-empty separator while the
    definition parser accepted an empty one; the parser wins.
    """
    assert Aggregator.build("join()") is not None
    assert _accepts("join()")


@pytest.mark.parametrize("configured", [
    "minn",
    "",
    "join",
    "count()",
    {"aggregator_type": "join", "parameters": [";"]},
])
def test_unbuildable_resource_aggregator_is_rejected(configured: Any) -> None:
    """What a resource may not configure.

    An unregistered name; a bare parametrized name, which has no separator to
    build with; a parameter on an unparametrized aggregator; and the dict
    form, which is an annotation-pipeline spelling -- a resource configures an
    aggregator by its string form.
    """
    assert not _accepts(configured)


def test_a_position_score_may_configure_count(tmp_path: pathlib.Path) -> None:
    """The bug, at the surface it was reported on: a resource YAML."""
    repo = (
        a_grr()
        .with_resource(
            "scores/counted",
            a_position_score()
            .with_score("phastCons", "float")
            .with_position_aggregator("count")
            .with_score_line(chrom="1", pos_begin="10", phastCons="0.1"),
        )
        .build_repo(tmp_path)
    )

    score = PositionScore(repo.get_resource("scores/counted")).open()

    score_def = score.get_score_definition("phastCons")
    assert score_def is not None
    assert score_def.pos_aggregator == "count"


def test_an_allele_score_may_configure_count(tmp_path: pathlib.Path) -> None:
    repo = (
        a_grr()
        .with_resource(
            "scores/counted",
            an_allele_score()
            .with_score("freq", "float")
            .with_allele_aggregator("count")
            .with_score_line(
                chrom="1", pos_begin="10",
                reference="A", alternative="G", freq="0.1"),
        )
        .build_repo(tmp_path)
    )

    score = AlleleScore(repo.get_resource("scores/counted")).open()

    score_def = score.get_score_definition("freq")
    assert score_def is not None
    assert score_def.allele_aggregator == "count"


def test_a_resource_naming_an_unregistered_aggregator_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """Deriving the schema widened it to the registry -- and no further."""
    repo = (
        a_grr()
        .with_resource(
            "scores/bad",
            a_position_score()
            .with_score("phastCons", "float")
            .with_position_aggregator("minn")
            .with_score_line(chrom="1", pos_begin="10", phastCons="0.1"),
        )
        .build_repo(tmp_path)
    )

    with pytest.raises(ValueError, match="Invalid configuration"):
        PositionScore(repo.get_resource("scores/bad"))
