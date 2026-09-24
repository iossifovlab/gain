"""Fluent, immutable test-data builder for ``liftover_chain`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`, like
:mod:`.gene_models_builder`: ``builders`` is past pylint's
``max-module-lines``, so the dependency runs ONE WAY -- this module
imports the shared single-realize seam from ``builders`` and ``builders``
does not import back -- and ``a_liftover_chain`` is imported from here.

The data is a gzipped UCSC chain file, written by ``setup_gzip``.
"""
from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping
from typing import Any

import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import setup_directories, setup_gzip
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_CHAIN_FILENAME = "liftover.chain.gz"

#: What a bare builder lifts over through: one ``+`` strand chain taking
#: ``chr1`` (length 100) onto ``chr1`` (length 110) shifted by 10, so
#: ``chr1:5`` lifts to ``chr1:15``.
_DEFAULT_CHAIN = """
chain 1000 chr1 100 + 0 100 chr1 110 + 10 110 1
100
"""


@dataclasses.dataclass(frozen=True)
class LiftoverChainBuilder(MetaMixin):
    """Immutable builder for a single ``liftover_chain`` resource."""

    chains: tuple[str, ...] = ()
    chrom_prefix: dict[str, dict[str, str]] | None = None

    def with_chain(self, block: str) -> LiftoverChainBuilder:
        """Author one chain block, replacing the default chain.

        ``block`` is one UCSC chain -- its ``chain`` header line and its
        alignment lines -- whitespace-separated, as ``setup_gzip`` takes
        it.  Each call adds a chain; they are written in call order.
        """
        return dataclasses.replace(self, chains=(*self.chains, block))

    def with_chrom_prefix(
        self, *,
        variant_coordinates: Mapping[str, str] | None = None,
        target_coordinates: Mapping[str, str] | None = None,
    ) -> LiftoverChainBuilder:
        """Emit a ``chrom_prefix:`` block renaming contigs around the chain.

        Each side takes the schema's own vocabulary, ``add_prefix`` and/or
        ``del_prefix``: ``variant_coordinates`` renames the contig a
        coordinate is queried on before the chain sees it,
        ``target_coordinates`` renames the contig the chain lifts it to.
        A side left ``None`` is not emitted, and leaving both out is a
        validation error rather than an empty block.  The block REPLACES
        any previously declared one, and the mappings are copied.
        """
        if variant_coordinates is None and target_coordinates is None:
            raise ResourceValidationError(
                "with_chrom_prefix requires at least one of "
                "variant_coordinates or target_coordinates; an empty "
                "chrom_prefix block renames nothing")
        chrom_prefix: dict[str, dict[str, str]] = {}
        if variant_coordinates is not None:
            chrom_prefix["variant_coordinates"] = dict(variant_coordinates)
        if target_coordinates is not None:
            chrom_prefix["target_coordinates"] = dict(target_coordinates)
        return dataclasses.replace(self, chrom_prefix=chrom_prefix)

    def _render_config(self) -> str:
        config: dict[str, Any] = {
            "type": "liftover_chain",
            "filename": _CHAIN_FILENAME,
        }
        if self.chrom_prefix is not None:
            config["chrom_prefix"] = self.chrom_prefix
        return yaml.safe_dump(
            config, default_flow_style=False, sort_keys=False,
        ) + self.render_meta()

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this liftover-chain resource into ``resource_dir``."""
        setup_directories(
            resource_dir, {GR_CONF_FILE_NAME: self._render_config()})
        # ``setup_gzip`` drops blank lines, so the chains are written back
        # to back; ``pyliftover`` starts a new chain at each header line.
        setup_gzip(
            resource_dir / _CHAIN_FILENAME,
            "\n".join(self.chains or (_DEFAULT_CHAIN,)))

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def a_liftover_chain() -> LiftoverChainBuilder:
    """Return an immutable liftover-chain builder."""
    return LiftoverChainBuilder()
