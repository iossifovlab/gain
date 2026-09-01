# pylint: disable=W0621,C0114,C0116,W0212,W0613
"""Gzip detection from a filename suffix when loading gene models (gain#975).

Both the gene-models file itself and the optional ``gene_mapping`` table
are opened with decompression decided by a ``.gz`` suffix.  The match used
to be case-sensitive, so a resource naming ``genes.TXT.GZ`` was handed to
the parser as undecompressed gzip bytes, which it died decoding as UTF-8.
Same defect class as gain#348.

Note the suffix vocabulary here is ``.gz`` only, not the ``.gz``/``.bgz``
pair ``reference_genome`` matches: making these two agree is a widening,
not a case fix, and is deliberately left out of gain#975.
"""
import gzip
import pathlib
import textwrap

import pytest
from gain.genomic_resources.gene_models.gene_models_factory import (
    build_gene_models_from_resource,
)
from gain.genomic_resources.repository import GenomicResource
from gain.genomic_resources.testing import (
    build_filesystem_test_resource,
    convert_to_tab_separated,
    setup_directories,
)

# this content follows the 'refflat' gene model format
GMM_CONTENT = """
#geneName name chrom strand txStart txEnd cdsStart cdsEnd exonCount exonStarts exonEnds
TP53      tx1  1     +      10      100   12       95     3         10,50,70   15,60,100
POGZ      tx3  17    +      10      100   12       95     3         10,50,70   15,60,100
"""  # ruff: ignore[line-too-long]

GENE_MAPPING_CONTENT = """
from   to
POGZ   gosho
TP53   pesho
"""


def _gzipped(content: str) -> bytes:
    return gzip.compress(convert_to_tab_separated(content).encode("utf8"))


def _setup_gene_models(
    resource_dir: pathlib.Path,
    genes_filename: str,
    mapping_filename: str | None = None,
) -> GenomicResource:
    """Write a gene-models resource whose files are named as given."""
    config = textwrap.dedent(f"""
        type: gene_models

        filename: {genes_filename}

        format: refflat
    """)
    if mapping_filename is not None:
        config += f"gene_mapping: {mapping_filename}\n"

    content: dict[str, object] = {
        "genomic_resource.yaml": config,
        genes_filename: (
            _gzipped(GMM_CONTENT)
            if genes_filename.lower().endswith(".gz")
            else convert_to_tab_separated(GMM_CONTENT)),
    }
    if mapping_filename is not None:
        content[mapping_filename] = (
            _gzipped(GENE_MAPPING_CONTENT)
            if mapping_filename.lower().endswith(".gz")
            else convert_to_tab_separated(GENE_MAPPING_CONTENT))

    setup_directories(resource_dir, content)
    return build_filesystem_test_resource(resource_dir)


@pytest.mark.parametrize("filename", [
    "genes.txt",
    "genes.txt.gz",
    "genes.TXT.GZ",
    "genes.txt.Gz",
])
def test_gene_models_load_whatever_the_suffix_case(
    tmp_path: pathlib.Path, filename: str,
) -> None:
    resource = _setup_gene_models(tmp_path, filename)

    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    assert set(gene_models.gene_names()) == {"TP53", "POGZ"}


@pytest.mark.parametrize("mapping_filename", [
    "geneMap.txt",
    "geneMap.txt.gz",
    "geneMap.TXT.GZ",
    "geneMap.txt.Gz",
])
def test_gene_mapping_applies_whatever_the_suffix_case(
    tmp_path: pathlib.Path, mapping_filename: str,
) -> None:
    resource = _setup_gene_models(tmp_path, "genes.txt", mapping_filename)

    gene_models = build_gene_models_from_resource(resource)
    gene_models.load()

    assert set(gene_models.gene_names()) == {"gosho", "pesho"}
