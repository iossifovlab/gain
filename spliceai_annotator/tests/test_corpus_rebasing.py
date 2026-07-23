# pylint: disable=C0114,C0116
from gain.genomic_resources.gene_models.transcript_models import (
    Exon,
    TranscriptModel,
)

from tests.corpus.rebasing import rebase_pos, rebase_transcript


def test_rebase_pos_maps_window_start_to_one() -> None:
    assert rebase_pos(46096722, 46086422) == 10301
    assert rebase_pos(46086422, 46086422) == 1


def test_rebase_transcript_preserves_relative_layout() -> None:
    tm = TranscriptModel(
        gene="COL6A2",
        tr_id="ENST00000857090.1",
        tr_name="COL6A2-201",
        chrom="chr21",
        strand="+",
        tx=(46096722, 46132851),
        cds=(46096800, 46132000),
        exons=[Exon(46096722, 46096840, 0), Exon(46111450, 46111591, 1)],
        attributes={"gene_biotype": "protein_coding"},
    )
    window_start = 46086422
    local = rebase_transcript(tm, window_start, "col6a2")

    # chromosome renamed to the local contig
    assert local.chrom == "col6a2"
    # identity fields preserved
    assert local.gene == "COL6A2"
    assert local.tr_id == "ENST00000857090.1"
    assert local.strand == "+"
    assert local.attributes == {"gene_biotype": "protein_coding"}

    # every coordinate shifted by the same offset (window_start - 1)
    offset = window_start - 1
    assert local.tx == (46096722 - offset, 46132851 - offset)
    assert local.cds == (46096800 - offset, 46132000 - offset)
    assert [(e.start, e.stop, e.frame) for e in local.exons] == [
        (46096722 - offset, 46096840 - offset, 0),
        (46111450 - offset, 46111591 - offset, 1),
    ]
    # relative distances are preserved (the invariant the annotator relies on)
    assert local.tx[1] - local.tx[0] == tm.tx[1] - tm.tx[0]
