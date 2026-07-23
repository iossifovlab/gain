"""Pure coordinate-rebasing helpers for the SpliceAI differential corpus.

The corpus builder cuts a window of sequence out of a real hg38 chromosome and
rebases it onto a *local contig* that starts at position 1.  Rebasing shifts
every genomic coordinate by the same offset, so all *relative* distances
between a variant and the transcripts around it are preserved -- which is the
only thing the SpliceAI annotator's padding math depends on.  Keeping these
helpers pure (no I/O) lets them be unit-tested without TensorFlow or a GRR.
"""
from __future__ import annotations

from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)


def rebase_pos(pos: int, window_start: int) -> int:
    """Map a 1-based genomic position onto a local contig starting at 1.

    ``window_start`` is the 1-based genomic coordinate that becomes local
    position 1.
    """
    return pos - window_start + 1


def rebase_transcript(
    transcript: TranscriptModel,
    window_start: int,
    local_chrom: str,
) -> TranscriptModel:
    """Return a copy of ``transcript`` rebased onto ``local_chrom``.

    Every coordinate (tx, cds, exon starts/stops) is shifted by the same
    offset so the transcript keeps its exact shape and its position relative
    to any rebased variant.
    """
    return TranscriptModel(
        gene=transcript.gene,
        tr_id=transcript.tr_id,
        tr_name=transcript.tr_name,
        chrom=local_chrom,
        strand=transcript.strand,
        tx=(
            rebase_pos(transcript.tx[0], window_start),
            rebase_pos(transcript.tx[1], window_start),
        ),
        cds=(
            rebase_pos(transcript.cds[0], window_start),
            rebase_pos(transcript.cds[1], window_start),
        ),
        exons=[
            Exon(
                rebase_pos(exon.start, window_start),
                rebase_pos(exon.stop, window_start),
                exon.frame,
            )
            for exon in transcript.exons
        ],
        attributes=dict(transcript.attributes),
    )
