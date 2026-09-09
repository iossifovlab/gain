# pylint: disable=W0621,C0114,C0116,W0212,W0613
import argparse
import pathlib
import sys
from typing import cast

import pytest
import pytest_mock
from gain.genomic_resources.gene_models.gene_models import (
    GeneModels,
)
from gain.genomic_resources.genomic_context import (
    build_cli_genomic_context,
    context_providers_add_argparser_arguments,
    context_providers_init,
    context_providers_init_with_argparser,
    get_genomic_context,
    get_grr_from_context,
    register_context_provider,
)
from gain.genomic_resources.genomic_context_cli import (
    CLIGenomicContextProvider,
)
from gain.genomic_resources.reference_genome import (
    ReferenceGenome,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)

pytestmark = pytest.mark.usefixtures("clean_genomic_context_providers")


@pytest.fixture
def grr_dirname(t4c8_grr: GenomicResourceRepo) -> str:
    file_url = cast(GenomicResourceProtocolRepo, t4c8_grr).proto.get_url()
    assert file_url.startswith("file:///")
    return file_url[7:]


def test_cli_genomic_context_reference_genome(
    grr_dirname: str,
) -> None:
    # Given

    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    provider = CLIGenomicContextProvider()
    provider.add_argparser_arguments(parser)

    argv = [
        "--grr-directory", grr_dirname,
        "--ref", "t4c8_genome",
    ]

    # When
    args = parser.parse_args(argv)
    context = provider.init(**vars(args))

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "reference_genome", "genomic_resources_repository"}

    genome = context.get_reference_genome()
    assert genome is not None
    assert isinstance(genome, ReferenceGenome)
    assert genome.resource.resource_id == "t4c8_genome"


@pytest.mark.parametrize("skip,dropped,kept", [
    ({"skip_cli_reference_genome": True}, ["-R", "--ref"], ["-G", "-g"]),
    ({"skip_cli_gene_models": True}, ["-G", "--genes"], ["-R", "-g"]),
    ({"skip_cli_reference_genome": True, "skip_cli_gene_models": True},
     ["-R", "-G"], ["-g", "--grr-directory"]),
])
def test_cli_genomic_context_provider_options_can_be_skipped(
    skip: dict[str, bool], dropped: list[str], kept: list[str],
) -> None:
    # A tool that resolves neither from the command line -- binning_tool
    # takes its genome from the run definition -- leaves them out; the
    # GRR options are never skippable.
    register_context_provider(CLIGenomicContextProvider())
    parser = argparse.ArgumentParser()
    context_providers_add_argparser_arguments(parser, **skip)

    usage = parser.format_usage()

    assert all(flag not in usage for flag in dropped)
    assert all(flag in usage for flag in kept)


def test_cli_genomic_context_provider_offers_every_option_by_default() -> None:
    register_context_provider(CLIGenomicContextProvider())
    parser = argparse.ArgumentParser()
    context_providers_add_argparser_arguments(parser)

    usage = parser.format_usage()

    assert all(flag in usage for flag in ["-g", "--grr-directory", "-R", "-G"])


def test_cli_genomic_context_provider_reference_genome(
    grr_dirname: str,
) -> None:
    # Given
    register_context_provider(CLIGenomicContextProvider())
    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    context_providers_add_argparser_arguments(parser)

    argv = [
        "--grr-directory", grr_dirname,
        "--ref", "t4c8_genome",
    ]

    # When
    args = parser.parse_args(argv)
    context_providers_init(**vars(args))
    context = get_genomic_context()

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "reference_genome", "genomic_resources_repository"}

    genome = context.get_reference_genome()
    assert genome is not None
    assert isinstance(genome, ReferenceGenome)
    assert genome.resource.resource_id == "t4c8_genome"


def test_cli_genomic_context_gene_models(
    grr_dirname: str,
) -> None:
    # Given
    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    provider = CLIGenomicContextProvider()
    provider.add_argparser_arguments(parser)

    argv = [
        "--grr-directory", grr_dirname,
        "--genes", "t4c8_genes",
    ]

    # When
    args = parser.parse_args(argv)
    context = provider.init(**vars(args))

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "gene_models", "genomic_resources_repository"}

    gene_models = context.get_gene_models()
    assert gene_models is not None
    assert isinstance(gene_models, GeneModels)
    assert gene_models.resource.resource_id == \
        "t4c8_genes"


def test_cli_genomic_context_provider_gene_models(
    grr_dirname: str,
) -> None:
    # Given
    register_context_provider(CLIGenomicContextProvider())
    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    context_providers_add_argparser_arguments(parser)

    argv = [
        "--grr-directory", grr_dirname,
        "--genes", "t4c8_genes",
    ]

    # When
    args = parser.parse_args(argv)
    context_providers_init(**vars(args))
    context = get_genomic_context()

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "gene_models", "genomic_resources_repository"}

    gene_models = context.get_gene_models()
    assert gene_models is not None
    assert isinstance(gene_models, GeneModels)
    assert gene_models.resource.resource_id == \
        "t4c8_genes"


def test_cli_genomic_context_no_grr(
) -> None:
    # Given
    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    provider = CLIGenomicContextProvider()
    provider.add_argparser_arguments(parser)

    argv = [
        "--genes", "t4c8_genes",
    ]

    # When
    args = parser.parse_args(argv)
    context = provider.init(**vars(args))

    # Then
    assert context is None


def test_cli_genomic_context_provider_no_grr(
) -> None:
    # Given
    register_context_provider(CLIGenomicContextProvider())
    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    context_providers_add_argparser_arguments(parser)

    argv = [
        "--genes", "t4c8_genes",
    ]

    # When
    args = parser.parse_args(argv)
    context_providers_init(**vars(args))
    context = get_genomic_context()

    # Then
    assert context is not None
    assert context.get_context_keys() == set()


def test_cli_genomic_context_grr_definition(
    t4c8_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    # Given
    (tmp_path / "grr_config.yaml").write_text(
        f"""{t4c8_grr.definition}""")

    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    provider = CLIGenomicContextProvider()
    provider.add_argparser_arguments(parser)

    argv = [
        "-g", str(tmp_path / "grr_config.yaml"),
    ]

    # When
    args = parser.parse_args(argv)
    context = provider.init(**vars(args))

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "genomic_resources_repository"}

    grr = context.get_genomic_resources_repository()
    assert grr is not None
    assert isinstance(grr, GenomicResourceRepo)


def test_cli_genomic_context_providers_grr_definition(
    t4c8_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
) -> None:
    # Given
    (tmp_path / "grr_config.yaml").write_text(
        f"""{t4c8_grr.definition}""")

    register_context_provider(CLIGenomicContextProvider())

    parser = argparse.ArgumentParser(
        description="Test CLI genomic context",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    context_providers_add_argparser_arguments(parser)

    argv = [
        "-g", str(tmp_path / "grr_config.yaml"),
    ]

    # When
    args = parser.parse_args(argv)
    context_providers_init(**vars(args))
    context = get_genomic_context()

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "genomic_resources_repository"}

    grr = context.get_genomic_resources_repository()
    assert grr is not None
    assert isinstance(grr, GenomicResourceRepo)


def test_cli_genomic_context_providers_init_with_argparser(
    t4c8_grr: GenomicResourceRepo,
    tmp_path: pathlib.Path,
    mocker: pytest_mock.MockerFixture,
) -> None:
    # Given
    (tmp_path / "grr_config.yaml").write_text(
        f"""{t4c8_grr.definition}""")

    register_context_provider(CLIGenomicContextProvider())
    mocker.patch.object(sys, "argv", new=[
        "test_tool",
        "-g", str(tmp_path / "grr_config.yaml"),
    ])

    # When
    context_providers_init_with_argparser("test_tool")
    context = get_genomic_context()

    # Then
    assert context is not None

    assert context.get_context_keys() == {
        "genomic_resources_repository"}

    grr = context.get_genomic_resources_repository()
    assert grr is not None
    assert isinstance(grr, GenomicResourceRepo)


def test_the_grr_named_on_the_command_line_is_the_one_served(
    grr_dirname: str,
) -> None:
    # The two steps a CLI tool takes from its parsed arguments to its GRR.
    register_context_provider(CLIGenomicContextProvider())

    context = build_cli_genomic_context({"grr_directory": grr_dirname})
    grr = get_grr_from_context(context)

    assert grr.get_resource("t4c8_genome") is not None
