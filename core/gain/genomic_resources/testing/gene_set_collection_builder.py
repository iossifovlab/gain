"""Fluent, immutable test-data builder for ``gene_set_collection`` resources.

A sibling of :mod:`gain.genomic_resources.testing.builders`, like
:mod:`.liftover_chain_builder`: the dependency runs ONE WAY -- this module
imports the shared single-realize seam from ``builders`` and ``builders``
does not import back -- and ``a_gene_set_collection`` is imported from here.

The resource is realized in the ``directory`` format: one file per gene
set under ``GeneSets/``, each holding the set's name, its description and
then one gene symbol per line.  The config, the directory and the file
names are all derived from the one list of declared gene sets.
"""
from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Sequence
from typing import Any

import yaml

from gain.genomic_resources.repository import (
    GR_CONF_FILE_NAME,
    GenomicResource,
)
from gain.genomic_resources.testing import setup_directories
from gain.genomic_resources.testing.builders import _build_single_resource
from gain.genomic_resources.testing.resource_meta import MetaMixin
from gain.genomic_resources.testing.score_specs import (
    ResourceValidationError,
)

_DIRECTORY = "GeneSets"


def _is_one_line(value: str, *, allow_empty: bool = False) -> bool:
    """Whether ``value`` reads back unchanged as one stripped line.

    ``isprintable`` refuses every line break the text-mode reader splits
    on (``\n``, ``\r`` and the rest) along with the other control
    characters, NUL among them.
    """
    return bool(value or allow_empty) and value == value.strip() \
        and value.isprintable()


@dataclasses.dataclass(frozen=True)
class GeneSetSpec:
    """One declared gene set: its name, description and genes."""

    name: str
    desc: str
    genes: tuple[str, ...]

    def __post_init__(self) -> None:
        # Each value must survive being written as one stripped line:
        # the reader takes line 1 as the name, line 2 as the description
        # and every further line as a gene, stripping each.
        if not _is_one_line(self.name) or "/" in self.name \
                or self.name.startswith("."):
            raise ResourceValidationError(
                f"gene set name {self.name!r} must be a non-empty single "
                f"line, without surrounding whitespace, usable as one "
                f"file name")
        if not _is_one_line(self.desc, allow_empty=True):
            raise ResourceValidationError(
                f"gene set {self.name!r}: description {self.desc!r} must "
                f"be a single line without surrounding whitespace")
        bad_genes = [gene for gene in self.genes if not _is_one_line(gene)]
        if bad_genes:
            raise ResourceValidationError(
                f"gene set {self.name!r}: gene(s) {bad_genes!r} must each "
                f"be a non-empty single line without surrounding "
                f"whitespace")

    def render(self) -> str:
        """Render the set as a ``directory``-format gene set file."""
        return "\n".join((self.name, self.desc, *self.genes)) + "\n"


#: What a bare builder holds: one small set of three genes.
_DEFAULT_GENE_SET = GeneSetSpec(
    "main_candidates", "Main Candidates", ("POGZ", "CHD8", "ANK2"))


@dataclasses.dataclass(frozen=True)
class GeneSetCollectionBuilder(MetaMixin):
    """Immutable builder for a single ``gene_set_collection`` resource."""

    collection_id: str = "main"
    gene_sets: tuple[GeneSetSpec, ...] = ()
    web_label: str | None = None
    web_format_str: str | None = None

    def __post_init__(self) -> None:
        # ``GeneSetCollection`` asserts on both of these ids.
        if self.collection_id in ("", "denovo"):
            raise ResourceValidationError(
                f"gene set collection id {self.collection_id!r} cannot be "
                f"opened as a collection; 'denovo' is reserved")
        # Names are file names, and a case-insensitive filesystem would
        # write two names differing only by case to the same file.
        seen: set[str] = set()
        for gene_set in self.gene_sets:
            key = gene_set.name.casefold()
            if key in seen:
                raise ResourceValidationError(
                    f"gene set {gene_set.name!r} is already declared")
            seen.add(key)

    def with_id(self, collection_id: str) -> GeneSetCollectionBuilder:
        """Set the config's ``id:``, the collection's ``collection_id``."""
        return dataclasses.replace(self, collection_id=collection_id)

    def with_web_label(self, web_label: str) -> GeneSetCollectionBuilder:
        """Emit ``web_label:``; left undeclared, no key is emitted."""
        return dataclasses.replace(self, web_label=web_label)

    def with_web_format_str(
        self, web_format_str: str,
    ) -> GeneSetCollectionBuilder:
        """Emit ``web_format_str:``; left undeclared, no key is emitted."""
        return dataclasses.replace(self, web_format_str=web_format_str)

    def with_gene_set(
        self, name: str, desc: str, *, genes: Sequence[str],
    ) -> GeneSetCollectionBuilder:
        """Author one gene set, replacing the default set.

        Each call adds a set; ``name`` is both the set's name as the
        collection reports it and the stem of the file it is written to,
        so a name already declared -- ignoring case -- is refused.  The
        genes are copied.
        """
        return dataclasses.replace(
            self,
            gene_sets=(*self.gene_sets,
                       GeneSetSpec(name, desc, tuple(genes))))

    def _render_config(self) -> str:
        config: dict[str, Any] = {
            "type": "gene_set_collection",
            "id": self.collection_id,
            "format": "directory",
            "directory": _DIRECTORY,
        }
        if self.web_label is not None:
            config["web_label"] = self.web_label
        if self.web_format_str is not None:
            config["web_format_str"] = self.web_format_str
        return yaml.safe_dump(
            config, default_flow_style=False, sort_keys=False,
        ) + self.render_meta()

    def realize_into(self, resource_dir: pathlib.Path) -> None:
        """Write this gene set collection resource into ``resource_dir``."""
        gene_sets = self.gene_sets or (_DEFAULT_GENE_SET,)
        setup_directories(resource_dir, {
            GR_CONF_FILE_NAME: self._render_config(),
            _DIRECTORY: {
                f"{gene_set.name}.txt": gene_set.render()
                for gene_set in gene_sets
            },
        })

    def build_resource(self, tmp_path: pathlib.Path) -> GenomicResource:
        """Realize this single resource (repo id ``""``) into ``tmp_path``."""
        return _build_single_resource(self, tmp_path)


def a_gene_set_collection() -> GeneSetCollectionBuilder:
    """Return an immutable gene set collection builder."""
    return GeneSetCollectionBuilder()
