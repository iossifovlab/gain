# pylint: disable=C0114,C0116
import pathlib

import pytest
from gain.genomic_resources.repository_factory import (
    build_resource_implementation,
)
from gain.genomic_resources.resource_implementation import (
    InfoImplementationMixin,
)
from gain.genomic_resources.testing.liftover_chain_builder import (
    LiftoverChainBuilder,
    a_liftover_chain,
)


def template_data(
    builder: LiftoverChainBuilder, tmp_path: pathlib.Path,
) -> dict:
    resource = builder.build_resource(tmp_path)
    impl = build_resource_implementation(resource)
    assert isinstance(impl, InfoImplementationMixin)
    return impl.get_template_data()


@pytest.mark.parametrize(
    ("coordinates", "prefixes", "described", "absent", "expected"),
    [
        pytest.param(
            "variant_coordinates", {"add_prefix": "chr"},
            "variant_chrom", "target_chrom",
            "Adds chrom prefix chr to variants before performing liftover.",
            id="variant-add"),
        pytest.param(
            "variant_coordinates", {"del_prefix": "chr"},
            "variant_chrom", "target_chrom",
            "Deletes chrom prefix chr from variants before performing "
            "liftover.",
            id="variant-del"),
        pytest.param(
            "variant_coordinates", {"del_prefix": "chr", "add_prefix": "Chr"},
            "variant_chrom", "target_chrom",
            "Deletes chrom prefix chr from variants before performing "
            "liftover.",
            id="variant-del-wins-over-add"),
        pytest.param(
            "target_coordinates", {"add_prefix": "chr"},
            "target_chrom", "variant_chrom",
            "Adds chrom prefix chr to variants after performing liftover.",
            id="target-add"),
        pytest.param(
            "target_coordinates", {"del_prefix": "chr"},
            "target_chrom", "variant_chrom",
            "Deletes chrom prefix chr from variants after performing "
            "liftover.",
            id="target-del"),
        pytest.param(
            "target_coordinates", {"del_prefix": "chr", "add_prefix": "Chr"},
            "target_chrom", "variant_chrom",
            "Deletes chrom prefix chr from variants after performing "
            "liftover.",
            id="target-del-wins-over-add"),
    ],
)
def test_chrom_prefix_is_described(
    tmp_path: pathlib.Path,
    coordinates: str,
    prefixes: dict[str, str],
    described: str,
    absent: str,
    expected: str,
) -> None:
    data = template_data(
        a_liftover_chain().with_chrom_prefix(**{coordinates: prefixes}),
        tmp_path)

    assert data[described] == expected
    assert absent not in data


def test_plain_chain_passes_the_config_through(
    tmp_path: pathlib.Path,
) -> None:
    data = template_data(a_liftover_chain(), tmp_path)

    assert {
        key: value for key, value in data.items()
        if key not in {"resource_files", "labels"}
    } == {"type": "liftover_chain", "filename": "liftover.chain.gz"}


def test_meta_is_rendered_as_markdown(tmp_path: pathlib.Path) -> None:
    data = template_data(
        a_liftover_chain().with_meta(summary="A *chain*"), tmp_path)

    assert "<em>chain</em>" in data["meta"]
