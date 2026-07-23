"""Emit a small filesystem GRR (genome + gene models) for the corpus.

These helpers turn cut+rebased sequence and transcripts into the on-disk
resource shape that ``build_filesystem_test_repository`` loads: a ``genome``
resource (single ``.fa`` with one line per local contig, plus a samtools
``.fai``) and a ``gene_models`` resource in GAIn's ``default`` format.
"""
from __future__ import annotations

import pathlib

import pysam
from gain.genomic_resources.gene_models.gene_models import GeneModels
from gain.genomic_resources.gene_models.serialization import (
    save_as_default_gene_models,
)


def write_genome_resource(
    resource_dir: pathlib.Path,
    fasta_filename: str,
    contigs: dict[str, str],
) -> None:
    """Write a ``genome`` resource with one line per contig.

    ``contigs`` maps local contig name -> nucleotide sequence.
    """
    resource_dir.mkdir(parents=True, exist_ok=True)
    fasta_path = resource_dir / fasta_filename
    with fasta_path.open("w") as outfile:
        for name, seq in contigs.items():
            outfile.write(f">{name}\n{seq}\n")
    # samtools .fai -- the plain-FASTA seek reader in gain parses this.
    pysam.faidx(str(fasta_path))
    (resource_dir / "genomic_resource.yaml").write_text(
        f"type: genome\nfilename: {fasta_filename}\n",
    )


def write_gene_models_resource(
    resource_dir: pathlib.Path,
    filename: str,
    gene_models: GeneModels,
) -> None:
    """Write a ``gene_models`` resource in GAIn's ``default`` format.

    Serialization is delegated to GAIn's own ``save_as_default_gene_models``
    so the fixture can never drift from the format GAIn actually parses.
    ``gene_models.transcript_models`` supplies the rows.
    """
    resource_dir.mkdir(parents=True, exist_ok=True)
    save_as_default_gene_models(
        gene_models, str(resource_dir / filename), gzipped=False)
    (resource_dir / "genomic_resource.yaml").write_text(
        f"type: gene_models\nfilename: {filename}\nformat: default\n",
    )
