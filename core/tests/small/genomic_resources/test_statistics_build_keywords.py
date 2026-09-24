# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""A misspelled statistics-build keyword is an error, not a silent default.

``grr_manage`` hands ``region_size=`` and ``grr=`` to every resource
kind's ``create_statistics_build_tasks``.  While the method took
``**kwargs``, ``region_sise=10`` kept the 3 Gb default region and
re-chunked the whole statistics graph without a word -- the #865 shape
(gain#883).  The keywords are explicit now, so a typo is a ``TypeError``.
"""
import inspect
import pathlib
from importlib.metadata import entry_points

import pytest
from gain.genomic_resources import get_resource_implementation_builder
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    GenomicResourceImplementation,
)
from gain.genomic_resources.testing.builders import (
    ResourceBuilder,
    a_basic_resource,
    a_grr,
    a_position_score,
    a_reference_genome,
)


@pytest.mark.parametrize("builder", [
    pytest.param(
        a_reference_genome().with_chromosome("chr1", "ACGT" * 10),
        id="genome-reads-region-size"),
    pytest.param(
        a_position_score()
        .with_score("score", "float")
        .with_data("""
            chrom  pos_begin  score
            chr1   10         0.1
        """),
        id="genomic-score-reads-region-size-and-grr"),
    pytest.param(a_basic_resource(), id="basic-ignores-both"),
])
def test_a_misspelled_keyword_is_a_type_error(
    builder: ResourceBuilder, tmp_path: pathlib.Path,
) -> None:
    repo = a_grr().with_resource("res", builder).build_repo(tmp_path)
    impl = build_resource_implementation(repo.get_resource("res"))

    with pytest.raises(TypeError, match="region_sise"):
        impl.create_statistics_build_tasks(  # pylint: disable=unexpected-keyword-arg
            region_sise=10)  # type: ignore[call-arg]


# gain's own kinds only: a plugin installed alongside (gpf's enrichment
# kind) is that package's contract to keep.
_GAIN_KINDS = sorted(
    entry_point.name
    for entry_point in entry_points(
        group="gain.genomic_resources.implementations")
    if entry_point.dist is not None
    and entry_point.dist.name == "gain-core"
)

_REGISTERED_IMPLEMENTATIONS = [
    pytest.param(get_resource_implementation_builder(kind), id=kind)
    for kind in _GAIN_KINDS
]


def test_the_sweep_sees_gain_s_registered_kinds() -> None:
    assert {"genome", "position_score", "gene_models", "basic"} <= set(
        _GAIN_KINDS)


@pytest.mark.parametrize("impl_class", _REGISTERED_IMPLEMENTATIONS)
def test_every_registered_kind_refuses_an_unknown_keyword(
    impl_class: type[GenomicResourceImplementation],
) -> None:
    signature = inspect.signature(impl_class.create_statistics_build_tasks)

    with pytest.raises(TypeError, match="region_sise"):
        signature.bind(None, region_sise=10)


@pytest.mark.parametrize("impl_class", _REGISTERED_IMPLEMENTATIONS)
def test_every_registered_kind_accepts_what_grr_manage_passes(
    impl_class: type[GenomicResourceImplementation],
) -> None:
    signature = inspect.signature(impl_class.create_statistics_build_tasks)

    bound = signature.bind(None, region_size=10, grr=None)

    assert set(bound.arguments) == {"self", "region_size", "grr"}
