"""Emit a small filesystem GRR (genome + gene models) for the corpus.

These helpers turn cut+rebased sequence and transcripts into the on-disk
resource shape that ``build_filesystem_test_repository`` loads: a ``genome``
resource (single ``.fa`` with one line per local contig, plus a samtools
``.fai``) and a ``gene_models`` resource in GAIn's ``default`` format.
"""
from __future__ import annotations

import pathlib

import pysam
from gain.genomic_resources.gene_models.transcript_models import (
    TranscriptModel,
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


def _format_default_row(transcript: TranscriptModel) -> str:
    exon_starts = ",".join(str(e.start) for e in transcript.exons)
    exon_ends = ",".join(str(e.stop) for e in transcript.exons)
    exon_frames = ",".join(
        str(e.frame if e.frame is not None else -1)
        for e in transcript.exons
    )
    atts = ";".join(
        f"{k}:{str(v).replace(':', '_')}"
        for k, v in transcript.attributes.items()
    )
    columns = [
        transcript.chrom,
        transcript.tr_id,
        transcript.tr_name,
        transcript.gene,
        transcript.strand,
        transcript.tx[0],
        transcript.tx[1],
        transcript.cds[0],
        transcript.cds[1],
        exon_starts,
        exon_ends,
        exon_frames,
        atts,
    ]
    return "\t".join(str(x) if x != "" else "" for x in columns)


def write_gene_models_resource(
    resource_dir: pathlib.Path,
    filename: str,
    transcripts: list[TranscriptModel],
) -> None:
    """Write a ``gene_models`` resource in GAIn's ``default`` format."""
    resource_dir.mkdir(parents=True, exist_ok=True)
    header = "\t".join([
        "chr", "trID", "trOrigId", "gene", "strand",
        "tsBeg", "txEnd", "cdsStart", "cdsEnd",
        "exonStarts", "exonEnds", "exonFrames", "atts",
    ])
    lines = [header]
    lines.extend(_format_default_row(t) for t in transcripts)
    (resource_dir / filename).write_text("\n".join(lines) + "\n")
    (resource_dir / "genomic_resource.yaml").write_text(
        f"type: gene_models\nfilename: {filename}\nformat: default\n",
    )
