# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""The two steps a CLI tool takes from its parsed arguments to its GRR."""
from typing import cast

import pytest
from gain.genomic_resources.genomic_context import (
    build_cli_genomic_context,
    get_grr_from_context,
    register_context_provider,
)
from gain.genomic_resources.genomic_context_base import SimpleGenomicContext
from gain.genomic_resources.genomic_context_cli import (
    CLIGenomicContextProvider,
)
from gain.genomic_resources.repository import (
    GenomicResourceProtocolRepo,
    GenomicResourceRepo,
)

pytestmark = pytest.mark.usefixtures("clean_genomic_context_providers")


def test_the_grr_named_on_the_command_line_is_the_one_served(
    t4c8_grr: GenomicResourceRepo,
) -> None:
    register_context_provider(CLIGenomicContextProvider())
    file_url = cast(GenomicResourceProtocolRepo, t4c8_grr).proto.get_url()
    assert file_url.startswith("file:///")
    args = {"grr_directory": file_url[7:], "grr_filename": None}

    context = build_cli_genomic_context(args)
    grr = get_grr_from_context(context)

    assert grr.get_resource("t4c8_genome") is not None


def test_a_context_without_a_grr_is_refused() -> None:
    context = SimpleGenomicContext({}, source="test")

    with pytest.raises(ValueError, match="no valid GRR configured"):
        get_grr_from_context(context)
